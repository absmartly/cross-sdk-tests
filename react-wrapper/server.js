const express = require('express');
const React = require('react');
const { renderToString } = require('react-dom/server');
const absmartly = require('@absmartly/javascript-sdk');
const sdkUtils = require('@absmartly/javascript-sdk/lib/utils');
const { Treatment, TreatmentVariant, TreatmentFunction, useTreatment } = require('@absmartly/react-sdk');
const SDKProvider = require('@absmartly/react-sdk').default;

process.on('uncaughtException', (err) => {
  console.error('Uncaught Exception:', err);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

const app = express();
app.use(express.json());

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

class CustomPublisher extends absmartly.ContextPublisher {
  constructor(eventCollector) {
    super();
    this.eventCollector = eventCollector;
    this._shouldFail = false;
  }

  publish(request, sdk, context) {
    if (this._shouldFail) {
      this._shouldFail = false;
      return Promise.reject(new Error('Publish failed'));
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

app.get('/capabilities', (req, res) => {
  res.json({diagnostics: true,
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

      const e2eSdk = new absmartly.SDK({
        endpoint: e2eEndpoint,
        apiKey: e2eApiKey,
        application: e2eApp,
        environment: e2eEnv,
        eventLogger: (ctx, eventName, eventData) => {
          eventCollector.handleEvent(ctx, eventName, eventData);
        }
      });

      const contextConfig = { units: req.body.units || {} };
      const context = e2eSdk.createContext(contextConfig);
      await context.ready();

      if (req.body.attributes) {
        for (const [key, value] of Object.entries(req.body.attributes)) {
          context.attribute(key, value);
        }
      }

      contexts.set(contextId, { context, eventCollector });

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
        { publishDelay: -1, refreshPeriod: 0, ...options }
      );
    } else if (failLoad) {
      const failedData = Promise.reject(new Error('Context load failed'));
      failedData.catch(() => {});
      context = sdk.createContextWith(
        { units },
        failedData,
        { publishDelay: -1, refreshPeriod: 0, ...options }
      );
      try { await context.ready(); } catch (e) {}
      await new Promise(r => setTimeout(r, 50));
    } else if (payloadThrottle > 0 && endpoint) {
      const deferredData = new Promise((resolve) => {
        setTimeout(() => {
          fetch(endpoint)
            .then(r => r.json())
            .then(resolve)
            .catch(() => resolve({ experiments: [] }));
        }, payloadThrottle);
      });
      context = sdk.createContextWith(
        { units },
        deferredData,
        { publishDelay: -1, refreshPeriod: 0, ...options }
      );
    } else {
      context = sdk.createContext(
        { units },
        { publishDelay: -1, refreshPeriod: 0, ...options }
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

  if (!context.isReady()) {
    return res.json({ result: 0, events: [] });
  }

  try {
    let capturedVariant = 0;
    const variants = [];
    for (let i = 0; i <= 10; i++) {
      variants.push(
        React.createElement(TreatmentVariant, { key: i, variant: i },
          React.createElement('span', { 'data-variant': i }, `Variant ${i}`)
        )
      );
    }

    const TreatmentWrapper = () => {
      return React.createElement(Treatment, {
        name: experimentName,
        context: context
      }, ...variants);
    };

    const html = renderToString(React.createElement(TreatmentWrapper));
    const match = html.match(/data-variant="(\d+)"/);
    if (match) {
      capturedVariant = parseInt(match[1], 10);
    }

    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: capturedVariant, events: newEvents });
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
    await context.ready();

    let capturedVariant = null;

    const PeekWrapper = () => {
      const { variant } = useTreatment(experimentName, true);
      capturedVariant = variant;
      return React.createElement('span', { 'data-variant': variant }, `Variant ${variant}`);
    };

    const html = renderToString(
      React.createElement(SDKProvider, { context },
        React.createElement(PeekWrapper)
      )
    );

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

  if (!context.isReady()) {
    return res.json({ result: req.body.defaultValue, events: [] });
  }

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
    res.json({ result: null, events: [] });
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
    res.json({ result: null, events: [] });
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

  if (!context.isReady()) {
    return res.json({ result: [], events: [] });
  }

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

  res.json({ result: data.context.pending(), events: [] });
});

app.get('/context/:contextId/isFinalized', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.isFinalized(), events: [] });
});

app.get('/context/:contextId/isReady', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.isReady(), events: [] });
});

app.get('/context/:contextId/isFailed', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  res.json({ result: data.context.isFailed(), events: [] });
});

app.get('/context/:contextId/experiments', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  if (!data.context.isReady()) {
    return res.json({ result: [], events: [] });
  }

  try {
    const experiments = data.context.experiments();
    res.json({ result: experiments, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getUnits', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const result = data.context.getUnits();
    res.json({ result, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getAttributes', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const result = data.context.getAttributes();
    res.json({ result, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/readyError', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const error = data.context.readyError();
  const result = error ? error.message || String(error) : null;
  res.json({ result, events: [] });
});

app.post('/context/:contextId/variableKeysMap', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const keys = data.context.variableKeys();
    res.json({ result: keys, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/globalCustomFieldKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    const keys = data.context.customFieldKeys();
    res.json({ result: keys, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/publishFail', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  data.customPublisher._shouldFail = true;
  res.json({ result: null, events: [] });
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
    await new Promise((resolve, reject) => {
      context._refresh((error) => {
        if (error) reject(error);
        else resolve();
      });
    });
  } catch (e) {
    // refresh may fail, still report events
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
    res.json({ result, events: newEvents });
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
    res.json({ result, events: newEvents });
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
