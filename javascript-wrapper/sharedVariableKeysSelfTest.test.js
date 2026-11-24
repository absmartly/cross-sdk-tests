// Run with node --test. Swaps @absmartly/javascript-sdk for a stub SDK whose contexts model the
// sharedVariableKeys rule, so each test can break one part of it and check the self-test notices.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');

function hasKey(config, key) {
  return Object.prototype.hasOwnProperty.call(config, key);
}

// A correct model of java-sdk Context.getVariableAssignment for the self-test's fixtures (every
// unit lands in variant 1; a strict audience matches only Country US), plus opt-in mutations.
function stubSdk(mutation = {}) {
  class StubContext {
    constructor(data, eventLogger) {
      this.data = data;
      this.eventLogger = eventLogger;
      this.attributes = {};
      this.exposed = new Set();
      this.queued = 0;
    }
    ready() {
      return Promise.resolve();
    }
    isReady() {
      return true;
    }
    isFailed() {
      return false;
    }
    attribute(name, value) {
      this.attributes[name] = value;
    }
    pending() {
      return this.queued;
    }
    expose(experiment) {
      if (this.exposed.has(experiment.id)) return;
      this.exposed.add(experiment.id);
      this.queued++;
      this.eventLogger(this, 'exposure', { id: experiment.id, name: experiment.name, variant: 1 });
    }
    candidates(key) {
      const found = this.data.experiments
        .map((e) => Object.assign({}, e, { configs: e.variants.map((v) => JSON.parse(v.config)) }))
        .filter((e) => e.configs.some((c) => hasKey(c, key)));
      return mutation.payloadOrder ? found : found.sort((a, b) => a.id - b.id);
    }
    assigned(experiment) {
      return !(experiment.audienceStrict && experiment.audience && this.attributes.Country !== 'US');
    }
    resolve(key, defaultValue, expose) {
      const candidates = this.candidates(key);
      const index = candidates.findIndex((e) => this.assigned(e));
      if (index < 0) return defaultValue;
      const winner = candidates[index];
      if (expose || mutation.peekExposes) this.expose(winner);
      if (expose && mutation.extraExposure) {
        const other = candidates.find((e) => e !== winner);
        if (other) this.expose(other);
      }
      if (hasKey(winner.configs[1], key)) return winner.configs[1][key];
      if (mutation.fallThrough) {
        const later = candidates.slice(index + 1).find((e) => this.assigned(e) && hasKey(e.configs[1], key));
        if (later) return later.configs[1][key];
      }
      return defaultValue;
    }
    peekVariableValue(key, defaultValue) {
      return this.resolve(key, defaultValue, false);
    }
    variableValue(key, defaultValue) {
      return this.resolve(key, defaultValue, true);
    }
  }

  class StubSDK {
    constructor(options) {
      this.eventLogger = options.eventLogger;
    }
    createContextWith(units, data) {
      return new StubContext(data, this.eventLogger);
    }
  }

  return { SDK: StubSDK };
}

async function runWith(mutation) {
  const stub = stubSdk(mutation);
  const load = Module._load;
  Module._load = function (request, ...rest) {
    return request === '@absmartly/javascript-sdk' ? stub : load.call(this, request, ...rest);
  };
  delete require.cache[require.resolve('./sharedVariableKeysSelfTest')];
  try {
    return await require('./sharedVariableKeysSelfTest').runSharedVariableKeys();
  } finally {
    Module._load = load;
    delete require.cache[require.resolve('./sharedVariableKeysSelfTest')];
  }
}

test('a correct stub passes, so the failures below come from the mutations', async () => {
  assert.equal(await runWith({}), true);
});

test('an extra exposure queued beside the winner fails', async () => {
  assert.equal(await runWith({ extraExposure: true }), false);
});

test("falling through to a later candidate's value when the winner lacks the key fails", async () => {
  assert.equal(await runWith({ fallThrough: true }), false);
});

test('visiting candidates in payload order fails', async () => {
  assert.equal(await runWith({ payloadOrder: true }), false);
});

test('a peek that exposes fails', async () => {
  assert.equal(await runWith({ peekExposes: true }), false);
});
