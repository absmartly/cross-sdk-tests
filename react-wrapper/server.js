const express = require('express');
const React = require('react');
const { renderToString } = require('react-dom/server');
const absmartly = require('@absmartly/javascript-sdk');
const sdkUtils = require('@absmartly/javascript-sdk/lib/utils');
const holdoutSelfTest = require('./holdoutSelfTest');
const { Treatment, TreatmentVariant, TreatmentFunction, useTreatment } = require('@absmartly/react-sdk');
const SDKProvider = require('@absmartly/react-sdk').default;
const { JSDOM } = require('jsdom');
const { createRoot } = require('react-dom/client');
const reactAct = React.act || require('react-dom/test-utils').act;

// <Treatment> records its exposure in a post-commit effect (peek during render,
// treatment() after commit), so it must be rendered into a real DOM with effects
// flushed. renderToString never runs effects and would report zero exposures.
// Renders are serialised because the DOM globals are process-wide.
let renderQueue = Promise.resolve();
function renderCommitted(element) {
  const run = async () => {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
    const saved = { window: global.window, document: global.document, act: global.IS_REACT_ACT_ENVIRONMENT };
    global.window = dom.window;
    global.document = dom.window.document;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = dom.window.document.getElementById('root');
    const root = createRoot(container);
    try {
      await reactAct(async () => {
        root.render(element);
      });
      // let the SDK's ready() promise settle and re-render the committed tree
      await reactAct(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      return container.cloneNode(true);
    } finally {
      await reactAct(async () => {
        root.unmount();
      });
      global.window = saved.window;
      global.document = saved.document;
      global.IS_REACT_ACT_ENVIRONMENT = saved.act;
      dom.window.close();
    }
  };
  const result = renderQueue.then(run, run);
  renderQueue = result.then(() => undefined, () => undefined);
  return result;
}

process.on('uncaughtException', (err) => {
  console.error('Uncaught Exception:', err);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

const app = express();
app.use(express.json());

app.use('/context/:contextId', (req, res, next) => {
  const entry = contexts.get(req.params.contextId);
  if (!entry) return next();
  const eventsBefore = entry.eventCollector.events.length;
  const json = res.json.bind(res);
  res.json = (body) => {
    if (body && typeof body === 'object' && !Array.isArray(body)) {
      body = { ...body, events: entry.eventCollector.events.slice(eventsBefore) };
    }
    return json(body);
  };
  next();
});

class EventCollector {
  constructor() {
    this.events = [];
  }

  handleEvent(context, eventName, data) {
    this.events.push({
      type: eventName,
      data: data !== undefined ? JSON.parse(JSON.stringify(data)) : undefined,
      timestamp: Date.now()
    });
  }
}

// Test contexts have no collector, so a publish succeeds without sending
// anything. e2e contexts pass forwardToSdk so the publish goes through the
// SDK's own ContextPublisher (real HTTP to the collector). Either way,
// /publishFail arms a one-shot failure of the next publish.
class CustomPublisher extends absmartly.ContextPublisher {
  constructor(eventCollector, { forwardToSdk = false } = {}) {
    super();
    this.eventCollector = eventCollector;
    this._shouldFail = false;
    this._forwardToSdk = forwardToSdk;
  }

  publish(request, sdk, context, requestOptions) {
    if (this._shouldFail) {
      this._shouldFail = false;
      return Promise.reject(new Error('Publish failed'));
    }
    if (this._forwardToSdk) {
      return super.publish(request, sdk, context, requestOptions);
    }
    return Promise.resolve();
  }
}

const contexts = new Map();
const payloadStore = {};

function normalizeAsyncEndpoint(endpoint) {
  if (!endpoint) return endpoint;
  try {
    const url = new URL(endpoint);
    if (url.pathname.startsWith('/context_payload/')) {
      url.hostname = '127.0.0.1';
      url.port = '3000';
      return url.toString();
    }
  } catch (error) {
    // Fallback to regex replacement below.
  }
  return endpoint.replace(/localhost:\d+/, '127.0.0.1:3000');
}

app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    sdk: 'react',
    version: '1.0.0'
  });
});

app.get('/capabilities', async (req, res) => {
  const [holdouts, holdoutArms] = await Promise.all([holdoutSelfTest.runHoldouts(), holdoutSelfTest.runHoldoutArms()]);
  res.json({diagnosticsUtf8: true,
    diagnosticsBase64: true,
    diagnosticsTypeChecks: true,
    diagnosticsIsPromise: true,
    holdouts,
    holdout_arms: holdoutArms,
    diagnostics: true,
    attrsSeq: true,
    isWrapper: true,
    wrapsSDK: 'javascript',
    publishFail: true,
    variableKeysMap: true,
    globalCustomFieldKeys: true,
    getUnits: true,
    getAttributes: true,
    readyError: true,
    passThroughOperations: [
      'track', 'attribute', 'variableValue', 'peekVariableValue',
      'customFieldValue', 'override', 'customAssignment', 'pending',
      'isFinalized', 'publish', 'finalize', 'setUnit', 'getUnit',
      'getAttribute', 'variableKeys', 'customFieldKeys',
      'customFieldValueType', 'setOverride', 'setCustomAssignment', 'refresh',
      'diagnostic', 'experiments', 'isReady', 'isFailed',
      'getUnits', 'getAttributes', 'readyError', 'variableKeysMap',
      'globalCustomFieldKeys', 'publishFail'
    ]
  });
});

app.put('/context_payload/:payloadId', (req, res) => {
  payloadStore[req.params.payloadId] = req.body.data || { experiments: [] };
  res.json({ success: true });
});

app.get('/context_payload/:payloadId', (req, res) => {
  const throttle = parseInt(req.query.throttle || '0');
  const data = payloadStore[req.params.payloadId] || { experiments: [] };

  setTimeout(() => {
    res.json(data);
  }, throttle);
});

app.get('/context_payload/:payloadId/context', (req, res) => {
  const data = payloadStore[req.params.payloadId] || { experiments: [] };
  res.json(data);
});

app.post('/context', async (req, res) => {
  try {
    if (req.body.mode === "e2e") {
      const e2eEndpoint = process.env.ABSMARTLY_E2E_ENDPOINT;
      const e2eApiKey = process.env.ABSMARTLY_E2E_API_KEY;
      const e2eApp = process.env.ABSMARTLY_E2E_APPLICATION || "e2e-tests";
      const e2eEnv = process.env.ABSMARTLY_E2E_ENVIRONMENT || "production";

      if (!e2eEndpoint || !e2eApiKey) {
        return res.status(501).json({ error: "e2e mode not configured" });
      }

      const contextId = `ctx-${Date.now()}-${Math.random()}`;
      const eventCollector = new EventCollector();

      const customPublisher = new CustomPublisher(eventCollector, { forwardToSdk: true });
      const e2eSdk = new absmartly.SDK({
        endpoint: e2eEndpoint,
        apiKey: e2eApiKey,
        application: e2eApp,
        environment: e2eEnv,
        eventLogger: (ctx, eventName, eventData) => {
          eventCollector.handleEvent(ctx, eventName, eventData);
        },
        publisher: customPublisher
      });

      const contextConfig = { units: req.body.units || {} };
      const e2eOptions = { ...(req.body.options || {}), publishDelay: -1, refreshPeriod: 0 };
      const context = e2eSdk.createContext(contextConfig, e2eOptions);
      await context.ready();

      if (req.body.attributes) {
        for (const [key, value] of Object.entries(req.body.attributes)) {
          context.attribute(key, value);
        }
      }

      contexts.set(contextId, { context, eventCollector, customPublisher });

      return res.json({
        result: { contextId, ready: context.isReady(), failed: context.isFailed(), finalized: context.isFinalized() },
        events: eventCollector.events
      });
    }

    let { data, endpoint, units, options } = req.body;
    const contextId = `ctx-${Date.now()}-${Math.random()}`;

    if (endpoint) {
      endpoint = normalizeAsyncEndpoint(endpoint);
    }

    const eventCollector = new EventCollector();
    const customPublisher = new CustomPublisher(eventCollector);

    const sdk = new absmartly.SDK({
      endpoint: endpoint || 'http://dummy',
      apiKey: 'dummy',
      application: 'test',
      environment: 'test',
      eventLogger: (ctx, eventName, eventData) => {
        eventCollector.handleEvent(ctx, eventName, eventData);
      },
      publisher: customPublisher
    });

    let context;
    const payloadThrottle = options?.payloadThrottle || 0;
    const failLoad = req.body.failLoad || false;

    if (data) {
      context = sdk.createContextWith(
        { units },
        data,
        { ...options, publishDelay: -1, refreshPeriod: 0 }
      );
    } else if (failLoad) {
      const failedData = Promise.reject(new Error('Context load failed'));
      failedData.catch(() => {});
      context = sdk.createContextWith(
        { units },
        failedData,
        { ...options, publishDelay: -1, refreshPeriod: 0 }
      );
      await context.ready();
    } else if (payloadThrottle > 0 && endpoint) {
      const deferredData = new Promise((resolve, reject) => {
        setTimeout(() => {
          fetch(endpoint)
            .then(r => { if (!r.ok) throw new Error('payload fetch HTTP ' + r.status); return r.json(); })
            .then(resolve)
            .catch(reject);
        }, payloadThrottle);
      });
      context = sdk.createContextWith(
        { units },
        deferredData,
        { ...options, publishDelay: -1, refreshPeriod: 0 }
      );
    } else {
      context = sdk.createContext(
        { units },
        { ...options, publishDelay: -1, refreshPeriod: 0 }
      );
      await context.ready();
    }

    contexts.set(contextId, { context, eventCollector, sdk, customPublisher });

    res.json({
      result: {
        contextId,
        ready: context.isReady(),
        failed: context.isFailed(),
        finalized: context.isFinalized()
      },
      events: eventCollector.events
    });
  } catch (error) {
    console.error('Context creation error:', error);
    res.status(500).json({ error: error.message, type: error.constructor.name });
  }
});

app.post('/context/:contextId/treatment', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector, sdk } = data;
  const eventsBefore = eventCollector.events.length;
  const experimentName = req.body.experimentName;

  // No wrapper-side not-ready/finalized shortcut: when the context is not ready
  // the SDK's <Treatment> renders its loading state, which performs no SDK read.
  try {
    if (context.isFinalized()) {
      context.treatment(experimentName);
    }
    // Each TreatmentVariant child contains a marker element that exists in the
    // committed DOM only if the SDK's <Treatment> actually rendered that variant.
    // No default: if the SDK renders no variant child, that is an error, not variant 0.
    const VariantMarker = ({ variant }) =>
      React.createElement('span', { 'data-variant': String(variant) }, `Variant ${variant}`);
    const variants = [];
    for (let i = 0; i <= 10; i++) {
      variants.push(
        React.createElement(TreatmentVariant, { key: i, variant: i },
          React.createElement(VariantMarker, { variant: i })
        )
      );
    }

    const container = await renderCommitted(React.createElement(Treatment, {
      name: experimentName,
      context: context,
      loadingComponent: React.createElement('span', { 'data-loading': 'true' })
    }, ...variants));

    const rendered = Array.from(container.querySelectorAll('[data-variant]'))
      .map((el) => Number(el.getAttribute('data-variant')));
    const renderedLoading = container.querySelector('[data-loading]') !== null;

    if (renderedLoading) {
      // <Treatment> rendered its loading state, which performs no SDK read.
      // Ask the SDK for the treatment so its own not-ready error reaches the
      // harness; a loading state on a ready context is an error.
      if (context.isReady()) {
        throw new Error('React SDK <Treatment> rendered its loading state for a ready context');
      }
      const variant = context.treatment(experimentName);
      return res.json({ result: variant, events: eventCollector.events.slice(eventsBefore) });
    }

    if (rendered.length !== 1) {
      return res.status(500).json({
        error: `React SDK <Treatment> rendered ${rendered.length} variant children (expected exactly 1): [${rendered.join(', ')}]`
      });
    }

    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: rendered[0], events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/peek', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const experimentName = req.body.experimentName;

  try {
    let capturedVariant = null;
    let capturedLoading = false;
    let capturedError = null;

    // Rendered into a committed DOM so the hook's effect runs: that is where
    // useTreatment reads the SDK and records any SDK error in its `error` state.
    const PeekWrapper = () => {
      const { variant, loading, error } = useTreatment(experimentName, true);
      capturedVariant = variant;
      capturedLoading = loading;
      capturedError = error;
      return React.createElement('span', { 'data-variant': String(variant) }, `Variant ${variant}`);
    };

    await renderCommitted(
      React.createElement(SDKProvider, { context },
        React.createElement(PeekWrapper)
      )
    );

    if (capturedError) throw capturedError;

    if (capturedLoading) {
      if (context.isReady()) {
        throw new Error('React SDK useTreatment stayed loading for a ready context');
      }
      // useTreatment performs no SDK read while loading; ask the SDK so its
      // own not-ready error reaches the harness.
      capturedVariant = context.peek(experimentName);
    }

    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: capturedVariant, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/track', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    context.track(req.body.goalName, req.body.properties);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/attribute', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    context.attribute(req.body.name, req.body.value);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/variableValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const value = context.variableValue(req.body.key, req.body.defaultValue);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: value, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/peekVariableValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const value = context.peekVariableValue(req.body.key, req.body.defaultValue);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: value, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/override', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context } = data;

  try {
    context.override(req.body.experimentName, req.body.variant);
    res.json({ result: null });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customAssignment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context } = data;

  try {
    context.customAssignment(req.body.experimentName, req.body.variant);
    res.json({ result: null });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customFieldValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const value = context.customFieldValue(req.body.experimentName, req.body.fieldName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: value, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/variableKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const keys = context.variableKeys();
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: Object.keys(keys), events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customFieldKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const keys = context.customFieldKeys();
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: keys, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customFieldValueType', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const valueType = context.customFieldValueType(req.body.experimentName, req.body.fieldName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: valueType, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.get('/context/:contextId/pending', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.pending() });
});

app.get('/context/:contextId/isFinalized', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.isFinalized() });
});

app.get('/context/:contextId/isReady', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.isReady() });
});

app.get('/context/:contextId/isFailed', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.isFailed() });
});

app.get('/context/:contextId/experiments', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });


  try {
    const experiments = data.context.experiments();
    res.json({ result: experiments });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getUnits', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const result = data.context.getUnits();
    res.json({ result });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getAttributes', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const result = data.context.getAttributes();
    res.json({ result });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/readyError', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const error = data.context.readyError();
  const result = error ? { isError: true, message: error.message || String(error) } : null;
  res.json({ result });
});

app.post('/context/:contextId/variableKeysMap', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const keys = data.context.variableKeys();
    res.json({ result: keys });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/globalCustomFieldKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const keys = data.context.customFieldKeys();
    res.json({ result: keys });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/publishFail', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  data.customPublisher._shouldFail = true;
  res.json({ result: null });
});

app.post('/context/:contextId/publish', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    await context.publish();
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post('/context/:contextId/refresh', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    // Public SDK API: context.refresh() re-fetches through the SDK's data
    // provider (the wrapper's mock /context_payload endpoint, which the
    // orchestrator updates before calling this) and rejects on failure.
    await context.refresh();
  } catch (error) {
    const newEvents = eventCollector.events.slice(eventsBefore);
    return res.status(500).json({ error: error.message, events: newEvents });
  }

  const newEvents = eventCollector.events.slice(eventsBefore);
  res.json({ result: null, events: newEvents });
});

app.post('/context/:contextId/finalize', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    await context.finalize();
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post('/context/:contextId/setUnit', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    context.unit(req.body.unitType, req.body.uid);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getUnit', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const result = context.getUnit(req.body.unitType);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: result ?? null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getAttribute', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const result = context.getAttribute(req.body.name);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: result ?? null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/setOverride', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    context.override(req.body.experimentName, req.body.variant);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/setCustomAssignment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    context.customAssignment(req.body.experimentName, req.body.variant);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.delete('/context/:contextId', (req, res) => {
  contexts.delete(req.params.contextId);
  res.json({ result: 'deleted' });
});

app.post('/diagnostic', (req, res) => {
  try {
    const body = req.body || {};
    const op = body.operation;
    let result;

    switch (op) {
      case 'hashUnit':
        result = sdkUtils.hashUnit(body.value);
        break;
      case 'base64UrlNoPadding': {
        const input = body.value == null ? '' : String(body.value);
        result = sdkUtils.base64UrlNoPadding(sdkUtils.stringToUint8Array(input));
        break;
      }
      case 'utf8Bytes': {
        const input = body.value == null ? '' : String(body.value);
        result = Array.from(sdkUtils.stringToUint8Array(input));
        break;
      }
      case 'isObject':
        result = sdkUtils.isObject(body.value);
        break;
      case 'isNumeric':
        result = sdkUtils.isNumeric(body.value);
        break;
      case 'isPromise':
        result = sdkUtils.isPromise(body.value);
        break;
      default:
        return res.status(400).json({ error: `Unsupported diagnostic operation: ${op}` });
    }

    res.json({ result, events: [] });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`React SDK wrapper listening on port ${PORT}`);
});
