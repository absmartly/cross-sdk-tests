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

let ABSmartlyService = null;
let angularServiceAvailable = false;

async function initAngularSDK() {
  try {
    const angularSDK = await import('@absmartly/angular-sdk');
    ABSmartlyService = angularSDK.ABSmartlyService;
    angularServiceAvailable = true;
    console.log('Angular SDK service loaded - routing operations through ABSmartlyService');
  } catch (error) {
    console.error('Angular SDK service unavailable:', error.message);
    throw error;
  }
}

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

const SERVICE_PASSTHROUGH = [
  'customFieldKeys', 'refresh'
];

app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    sdk: 'angular',
    version: '1.0.0'
  });
});

app.get('/capabilities', (req, res) => {
  res.json({diagnostics: true,
    attrsSeq: true,
    isWrapper: true,
    wrapsSDK: 'javascript',
    angularServiceAvailable,
    passThroughOperations: SERVICE_PASSTHROUGH
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

function createService(context) {
  if (!ABSmartlyService) {
    throw new Error('Angular SDK service is not initialized');
  }
  try {
    const dummyConfig = {
      endpoint: 'http://dummy', apiKey: 'dummy',
      environment: 'test', application: 'test', units: {}
    };
    return new ABSmartlyService(dummyConfig, context);
  } catch (error) {
    throw new Error(`Failed to create ABSmartlyService instance: ${error.message}`);
  }
}

app.post('/context', async (req, res) => {
  try {
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

    const service = createService(context);
    contexts.set(contextId, { context, service, eventCollector });

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

app.post('/context/:contextId/treatment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const variant = service
      ? service.treatment(req.body.experimentName)
      : context.treatment(req.body.experimentName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: variant, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/peek', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const variant = service
      ? service.peek(req.body.experimentName)
      : context.peek(req.body.experimentName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: variant, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/track', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      service.track(req.body.goalName, req.body.properties);
    } else {
      context.track(req.body.goalName, req.body.properties);
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/attribute', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      service.attribute(req.body.name, req.body.value);
    } else {
      context.attribute(req.body.name, req.body.value);
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getAttribute', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const result = service
      ? service.getAttribute(req.body.name)
      : context.getAttribute(req.body.name);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/setAttributes', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const attributes = req.body.attributes || {};
    if (service) {
      service.attributes(attributes);
    } else {
      for (const [name, value] of Object.entries(attributes)) {
        context.attribute(name, value);
      }
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getAttributes', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const result = context.getAttributes();
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/setUnits', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const units = req.body.units || {};
    for (const [unitType, uid] of Object.entries(units)) {
      context.unit(unitType, uid);
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getUnits', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const result = context.getUnits();
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/variableValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const value = service
      ? service.variableValue(req.body.key, req.body.defaultValue)
      : context.variableValue(req.body.key, req.body.defaultValue);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: value, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/peekVariableValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const value = service
      ? service.peekVariableValue(req.body.key, req.body.defaultValue)
      : context.peekVariableValue(req.body.key, req.body.defaultValue);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: value, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/override', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service } = data;

  try {
    if (service) {
      service.override(req.body.experimentName, req.body.variant);
    } else {
      context.override(req.body.experimentName, req.body.variant);
    }
    res.json({ result: null, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customAssignment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service } = data;

  try {
    if (service) {
      service.customAssignment(req.body.experimentName, req.body.variant);
    } else {
      context.customAssignment(req.body.experimentName, req.body.variant);
    }
    res.json({ result: null, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/customFieldValue', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const value = service
      ? service.customFieldValue(req.body.experimentName, req.body.fieldName)
      : context.customFieldValue(req.body.experimentName, req.body.fieldName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: value, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/variableKeys', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const keys = service ? service.variableKeys() : context.variableKeys();
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

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const valueType = service
      ? service.customFieldValueType(req.body.experimentName, req.body.fieldName)
      : context.customFieldValueType(req.body.experimentName, req.body.fieldName);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: valueType, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.get('/context/:contextId/pending', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { service, context } = data;
  const result = service ? service.pending() : context.pending();
  res.json({ result, events: [] });
});

app.get('/context/:contextId/isFinalized', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { service, context } = data;
  const result = service ? service.isFinalized() : context.isFinalized();
  res.json({ result, events: [] });
});

app.get('/context/:contextId/isReady', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { service, context } = data;
  const result = service ? service.isReady() : context.isReady();
  res.json({ result, events: [] });
});

app.get('/context/:contextId/isFailed', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { service, context } = data;
  const result = service ? service.isFailed() : context.isFailed();
  res.json({ result, events: [] });
});

app.get('/context/:contextId/experiments', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  try {
    const { service, context } = data;
    const experiments = service ? service.experiments() : context.experiments();
    res.json({ result: experiments, events: [] });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/publish', async (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      await service.publish();
    } else {
      await context.publish();
    }
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

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      await service.finalize();
    } else {
      await context.finalize();
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    console.error('Finalize error:', error);
    res.status(500).json({ error: error.message, stack: error.stack });
  }
});

app.post('/context/:contextId/setUnit', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      service.setUnit(req.body.unitType, req.body.uid);
    } else {
      context.unit(req.body.unitType, req.body.uid);
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/getUnit', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    const result = service
      ? service.getUnit(req.body.unitType)
      : context.getUnit(req.body.unitType);
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/setOverride', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      service.override(req.body.experimentName, req.body.variant);
    } else {
      context.override(req.body.experimentName, req.body.variant);
    }
    const newEvents = eventCollector.events.slice(eventsBefore);
    res.json({ result: null, events: newEvents });
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/context/:contextId/setCustomAssignment', (req, res) => {
  const data = contexts.get(req.params.contextId);
  if (!data) return res.status(404).json({ error: 'Context not found' });

  const { context, service, eventCollector } = data;
  const eventsBefore = eventCollector.events.length;

  try {
    if (service) {
      service.customAssignment(req.body.experimentName, req.body.variant);
    } else {
      context.customAssignment(req.body.experimentName, req.body.variant);
    }
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

initAngularSDK().then(() => {
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Angular SDK wrapper listening on port ${PORT}`);
  });
}).catch((error) => {
  console.error('Failed to initialize Angular SDK wrapper:', error.message);
  process.exit(1);
});
