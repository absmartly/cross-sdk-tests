const express = require('express');

// Define 'self' for Node.js compatibility with UMD bundles
if (typeof self === 'undefined') {
  global.self = global;
}

const { createRenderer, h } = require('vue');
const absmartly = require('@absmartly/javascript-sdk');
const sdkUtils = require('@absmartly/javascript-sdk/lib/utils');
const vue3sdk = require('@absmartly/vue3-sdk');

process.on('uncaughtException', (err) => {
  console.error('Uncaught Exception:', err);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

// The SDK's <Treatment> picks its variant in beforeMount(), which Vue's SSR
// renderer never calls. Instead of re-implementing that hook, mount the SDK
// component exactly as shipped through Vue's own runtime (createRenderer) with a
// minimal in-memory host. This runs the real lifecycle (beforeCreate, created,
// beforeMount, render, mounted, beforeUnmount) without DOM globals that could
// change the JS SDK's environment detection.
const TreatmentComponent = vue3sdk.Treatment;
const memoryNodeOps = {
  createElement: (tag) => ({ tag, children: [], props: {}, parent: null }),
  createText: (text) => ({ text, parent: null }),
  createComment: (text) => ({ comment: text, parent: null }),
  setText: (node, text) => { node.text = text; },
  setElementText: (el, text) => { el.children = [{ text, parent: el }]; },
  insert: (child, parent, anchor) => {
    child.parent = parent;
    const i = anchor ? parent.children.indexOf(anchor) : -1;
    if (i >= 0) parent.children.splice(i, 0, child);
    else parent.children.push(child);
  },
  remove: (child) => {
    const p = child.parent;
    if (p) {
      const i = p.children.indexOf(child);
      if (i >= 0) p.children.splice(i, 1);
      child.parent = null;
    }
  },
  parentNode: (node) => node.parent || null,
  nextSibling: (node) => {
    const p = node.parent;
    if (!p) return null;
    return p.children[p.children.indexOf(node) + 1] || null;
  },
  patchProp: (el, key, prev, next) => { el.props[key] = next; },
};
const memoryRenderer = createRenderer(memoryNodeOps);

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

// The SDK's <Treatment> renders the 'loading' slot while the context is not
// ready; that render performs no SDK read. Ask the SDK itself for the
// treatment so its own not-ready error reaches the harness (no fabricated 0).
const LOADING_SLOT = 'loading';
function loadingSlotVariant(slot, context, experimentName) {
  if (slot !== LOADING_SLOT) return slot;
  if (context.isReady()) {
    throw new Error('SDK <Treatment> rendered its loading slot for a ready context');
  }
  return context.treatment(experimentName);
}

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
    sdk: 'vue3',
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
      'peek', 'track', 'attribute', 'variableValue', 'peekVariableValue',
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
    if (endpoint) {
      endpoint = normalizeAsyncEndpoint(endpoint);
    }
    const contextId = `ctx-${Date.now()}-${Math.random()}`;

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
      try { await context.ready(); } catch (e) {}
      await new Promise(r => setTimeout(r, 50));
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

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const experimentName = req.body.experimentName;

  if (context.isFinalized()) {
    // Let the SDK raise its own finalized error.
    try { context.treatment(experimentName); } catch (e) { return res.status(400).json({ error: e.message }); }
  }

  // No wrapper-side not-ready shortcut: the SDK's <Treatment> renders its
  // 'loading' slot itself while the context is not ready.
  try {
    // Each numbered slot records its index only when the SDK component invokes
    // it. No 'default' slot and no HTML scraping: if the SDK selects nothing we
    // can identify, that is an error, never variant 0.
    const rendered = [];
    const slots = {};
    for (let i = 0; i <= 10; i++) {
      const idx = i;
      slots[idx.toString()] = () => {
        rendered.push(idx);
        return h('span', String(idx));
      };
    }
    slots.loading = () => {
      rendered.push(LOADING_SLOT);
      return h('span', 'loading');
    };

    let renderError = null;
    const vueApp = memoryRenderer.createApp({
      render() {
        return h(TreatmentComponent, { name: experimentName }, slots);
      }
    });
    vueApp.config.errorHandler = (err) => { if (!renderError) renderError = err; };
    // Same per-app injection the SDK plugin performs (plugin.js install()),
    // scoped to this request's app instance.
    vueApp.config.globalProperties.__absmartlyGlobal = '$absmartly';
    vueApp.config.globalProperties.$absmartly = context;

    try {
      vueApp.mount(memoryNodeOps.createElement('root'));
    } finally {
      vueApp.unmount();
    }

    if (renderError) throw renderError;

    const distinct = [...new Set(rendered)];
    if (distinct.length !== 1) {
      return res.status(500).json({
        error: `Vue3 SDK <Treatment> rendered ${distinct.length} distinct variant slots (expected exactly 1): [${distinct.join(', ')}]`
      });
    }
    const variant = loadingSlotVariant(distinct[0], context, experimentName);

    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: variant, events: newEvents });
  } catch (error) {
    console.error('Treatment error:', error);
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/peek', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const variant = context.peek(req.body.experimentName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: variant, events: newEvents });
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
  try { res.json({ result: data.context.getUnits(), events: [] }); }
  catch (error) { res.status(400).json({ error: error.message }); }
});

app.post('/context/:contextId/getAttributes', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try { res.json({ result: data.context.getAttributes(), events: [] }); }
  catch (error) { res.status(400).json({ error: error.message }); }
});

app.post('/context/:contextId/readyError', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const error = data.context.readyError();
  res.json({ result: error ? { isError: true, message: error.message || String(error) } : null, events: [] });
});

app.post('/context/:contextId/variableKeysMap', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try { res.json({ result: data.context.variableKeys(), events: [] }); }
  catch (error) { res.status(400).json({ error: error.message }); }
});

app.post('/context/:contextId/globalCustomFieldKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try { res.json({ result: data.context.customFieldKeys(), events: [] }); }
  catch (error) { res.status(400).json({ error: error.message }); }
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
  console.log(`Vue3 SDK wrapper listening on port ${PORT}`);
});
