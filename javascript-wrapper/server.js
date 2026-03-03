const express = require('express');
const absmartly = require('@absmartly/javascript-sdk');
const sdkUtils = require('@absmartly/javascript-sdk/lib/utils');

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
  }

  publish(request, sdk, context) {
    try {
      return Promise.resolve();
    } catch (error) {
      console.error('CustomPublisher error:', error);
      return Promise.reject(error);
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
  } catch (error) {
    // Fallback to regex replacement below.
  }
  return endpoint.replace(/localhost:\d+/, '127.0.0.1:3000');
}

app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    sdk: 'javascript',
    version: '1.0.0'
  });
});

app.get('/capabilities', (req, res) => {
  res.json({// Supports async context creation
    diagnostics: true,
    attrsSeq: true       // Supports attribute sequence tracking
  });
});

// Store context payload for async retrieval
app.put('/context_payload/:payloadId', (req, res) => {
  payloadStore[req.params.payloadId] = req.body.data || { experiments: [] };
  res.json({ success: true });
});

// Mock ABsmartly API - SDK calls GET /context_payload/{payloadId}/context?application=...&environment=...
app.get('/context_payload/:payloadId/context', (req, res) => {
  const data = payloadStore[req.params.payloadId] || { experiments: [] };
  res.json(data);
});

app.post('/context', async (req, res) => {
  try {
    let { data, endpoint, units, options } = req.body;

    units = units || {};

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

    if (data) {
      context = sdk.createContextWith(
        { units },
        data,
        { publishDelay: -1, refreshPeriod: 0, ...options }
      );
    } else {
      context = sdk.createContext(
        { units },
        { publishDelay: -1, refreshPeriod: 0, ...options }
      );
      if (payloadThrottle === 0) {
        await context.ready();
      }
    }

    contexts.set(contextId, { context, eventCollector });

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

app.post('/context/:contextId/treatment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const variant = context.treatment(req.body.experimentName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: variant, events: newEvents });
  } catch (error) {
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
    const keys = context.customFieldKeys(req.body.experimentName);
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

app.post('/context/:contextId/refresh', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  context._init(req.body.newData);
  const newEvents = eventCollector.events.slice(eventsBefore);
  eventCollector.handleEvent(context, 'refresh', req.body.newData);
  const finalEvents = eventCollector.events.slice(eventsBefore);

  res.json({ result: null, events: finalEvents });
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
    console.error('Finalize error:', error);
    res.status(500).json({ error: error.message, stack: error.stack });
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
  console.log(`JavaScript SDK wrapper listening on port ${PORT}`);
});
