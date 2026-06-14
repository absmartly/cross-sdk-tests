const express = require('express');
const absmartly = require('@absmartly/javascript-sdk');

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

  handleEvent(eventType, data) {
    this.events.push({
      type: eventType,
      data: data !== undefined ? JSON.parse(JSON.stringify(data)) : undefined,
      timestamp: Date.now()
    });
  }
}

class CustomPublisher {
  constructor() {
    this._shouldFail = false;
  }

  async publish(event) {
    if (this._shouldFail) {
      this._shouldFail = false;
      throw new Error('Publish failed');
    }
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
  } catch (error) {}
  return endpoint.replace(/localhost:\d+/, '127.0.0.1:3000');
}

app.get('/health', (req, res) => {
  res.json({ status: 'healthy', sdk: 'typescript', version: '2.0.0' });
});

app.get('/capabilities', (req, res) => {
  res.json({
    diagnostics: true,
    attrsSeq: true,
    publishFail: true,
    variableKeysMap: true,
    globalCustomFieldKeys: true,
    getUnits: true,
    getAttributes: true,
    readyError: true
  });
});

app.put('/context_payload/:payloadId', (req, res) => {
  payloadStore[req.params.payloadId] = req.body.data || { experiments: [] };
  res.json({ success: true });
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

      const e2eSdk = new absmartly.ABSmartly({
        endpoint: e2eEndpoint,
        apiKey: e2eApiKey,
        application: e2eApp,
        environment: e2eEnv,
        eventLogger: { handleEvent: (type, data) => eventCollector.handleEvent(type, data) }
      });

      const context = await e2eSdk.createContext({ units: req.body.units || {} });

      if (req.body.attributes) {
        for (const [key, value] of Object.entries(req.body.attributes)) {
          context.setAttribute(key, value);
        }
      }

      contexts.set(contextId, { context, eventCollector });

      return res.json({
        result: { contextId, ready: context.isReady(), failed: context.isFailed(), finalized: context.isFinalized() },
        events: eventCollector.events
      });
    }

    let { data, endpoint, units, options } = req.body;
    units = units || {};

    if (endpoint) {
      endpoint = normalizeAsyncEndpoint(endpoint);
    }

    const contextId = `ctx-${Date.now()}-${Math.random()}`;
    const eventCollector = new EventCollector();
    const customPublisher = new CustomPublisher();

    const contextOpts = {
      eventLogger: { handleEvent: (type, eventData) => eventCollector.handleEvent(type, eventData) },
      publisher: customPublisher,
      dataProvider: endpoint ? { getContextData: () => fetch(`${endpoint}/context`).then(r => r.json()) } : null,
    };

    let context;
    const payloadThrottle = options?.payloadThrottle || 0;
    const failLoad = req.body.failLoad || false;
    const publishDelay = options?.publishDelay ?? -1;
    const refreshInterval = options?.refreshPeriod ?? 0;

    if (data) {
      context = absmartly.Context.createWith(
        { units, publishDelay, refreshInterval },
        data,
        contextOpts
      );
    } else if (failLoad) {
      const failedData = Promise.reject(new Error('Context load failed'));
      failedData.catch(() => {});
      context = absmartly.Context.createAsync(
        { units, publishDelay, refreshInterval },
        failedData,
        contextOpts
      );
      try { await context.waitUntilReady(); } catch (e) {}
      await new Promise(r => setTimeout(r, 50));
    } else if (payloadThrottle > 0 && endpoint) {
      const deferredData = new Promise((resolve) => {
        setTimeout(() => {
          fetch(`${endpoint}/context`)
            .then(r => r.json())
            .then(resolve)
            .catch(() => resolve({ experiments: [] }));
        }, payloadThrottle);
      });
      context = absmartly.Context.createAsync(
        { units, publishDelay, refreshInterval },
        deferredData,
        contextOpts
      );
    } else if (endpoint) {
      const dataFuture = fetch(`${endpoint}/context`).then(r => r.json());
      context = absmartly.Context.createAsync(
        { units, publishDelay, refreshInterval },
        dataFuture,
        contextOpts
      );
      await context.waitUntilReady();
    } else {
      context = absmartly.Context.createWith(
        { units, publishDelay, refreshInterval },
        { experiments: [] },
        contextOpts
      );
    }

    contexts.set(contextId, { context, eventCollector, customPublisher });

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

app.post('/context/:contextId/setUnit', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  try {
    context.setUnit(req.body.unitType, req.body.uid);
    res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getUnit', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const result = context.getUnit(req.body.unitType) ?? null;
  res.json({ result, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/attribute', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  try {
    context.setAttribute(req.body.name, req.body.value);
    res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getAttribute', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const result = context.getAttribute(req.body.name) ?? null;
  res.json({ result, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/treatment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  if (!context.isReady()) return res.json({ result: 0, events: [] });
  const variant = context.treatment(req.body.experimentName);
  res.json({ result: variant, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/peek', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const variant = context.peek(req.body.experimentName);
  res.json({ result: variant, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/variableValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  if (!context.isReady()) return res.json({ result: req.body.defaultValue, events: [] });
  const value = context.variableValue(req.body.key, req.body.defaultValue);
  res.json({ result: value, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/peekVariableValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const value = context.peekVariableValue(req.body.key, req.body.defaultValue);
  res.json({ result: value, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/track', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  try {
    context.track(req.body.goalName, req.body.properties);
    res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/override', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  data.context.setOverride(req.body.experimentName, req.body.variant);
  res.json({ result: null, events: [] });
});

app.post('/context/:contextId/setOverride', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  context.setOverride(req.body.experimentName, req.body.variant);
  res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/customAssignment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  try {
    data.context.setCustomAssignment(req.body.experimentName, req.body.variant);
    res.json({ result: null, events: [] });
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
    context.setCustomAssignment(req.body.experimentName, req.body.variant);
    res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customFieldValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const value = context.customFieldValue(req.body.experimentName, req.body.fieldName);
  res.json({ result: value, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/variableKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  if (!context.isReady()) return res.json({ result: [], events: [] });
  const keys = context.variableKeys();
  res.json({ result: Object.keys(keys), events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/variableKeysMap', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const keys = context.variableKeys();
  res.json({ result: keys, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/customFieldKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const keys = context.customFieldKeys();
  res.json({ result: keys, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/globalCustomFieldKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const keys = context.customFieldKeys();
  res.json({ result: keys, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/customFieldValueType', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const valueType = context.customFieldValueType(req.body.experimentName, req.body.fieldName);
  res.json({ result: valueType, events: eventCollector.events.slice(eventsBefore) });
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
  if (!data.context.isReady()) return res.json({ result: [], events: [] });
  res.json({ result: data.context.experiments(), events: [] });
});

app.post('/context/:contextId/getUnits', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  res.json({ result: context.getUnits(), events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/getAttributes', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  res.json({ result: context.getAttributes(), events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/readyError', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  const error = context.getReadyError();
  const result = error ? error.message || String(error) : null;
  res.json({ result, events: eventCollector.events.slice(eventsBefore) });
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
    res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
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
    await context.refresh();
  } catch (e) {}
  res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
});

app.post('/context/:contextId/finalize', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });
  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;
  try {
    await context.finalize();
    res.json({ result: null, events: eventCollector.events.slice(eventsBefore) });
  } catch (error) {
    console.error('Finalize error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.delete('/context/:contextId', (req, res) => {
  contexts.delete(req.params.contextId);
  res.json({ result: 'deleted' });
});

app.post('/diagnostic', (req, res) => {
  try {
    const op = req.body?.operation;
    let result;
    switch (op) {
      case 'hashUnit':
        result = absmartly.hashUnit(req.body.value);
        break;
      case 'base64UrlNoPadding': {
        const input = req.body.value == null ? '' : String(req.body.value);
        result = absmartly.base64UrlNoPadding(absmartly.toUtf8Bytes(input));
        break;
      }
      case 'utf8Bytes': {
        const input = req.body.value == null ? '' : String(req.body.value);
        result = Array.from(absmartly.toUtf8Bytes(input));
        break;
      }
      case 'isObject':
        result = typeof req.body.value === 'object' && req.body.value !== null && !Array.isArray(req.body.value);
        break;
      case 'isNumeric':
        result = typeof req.body.value === 'number' && isFinite(req.body.value);
        break;
      case 'isPromise':
        result = req.body.value instanceof Promise;
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
  console.log(`TypeScript SDK wrapper listening on port ${PORT}`);
});
