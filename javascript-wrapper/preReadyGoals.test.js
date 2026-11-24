'use strict';

// Run with: node --test javascript-wrapper/preReadyGoals.test.js
// No installed SDK or Express required: load only the wrapper with test doubles.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function harness({ unexpectedlyReady = false, fetchError = false, syncFetchError = false } = {}) {
  const routes = new Map();
  const calls = [];
  let instance;
  class Publisher { publish() { return Promise.resolve(); } }
  class SDK {
    constructor(config) { this.config = config; instance = this; }
    createContextWith(config, data, options) {
      calls.push('createWith');
      return this.makeContext(data, options);
    }
    createContext(config, options) {
      calls.push('create');
      return this.makeContext(this.getContextData(), options);
    }
    getContextData() {
      calls.push('fetch');
      if (syncFetchError) throw new Error('fetch failed');
      return fetchError ? Promise.reject(new Error('fetch failed')) : Promise.resolve({ experiments: [] });
    }
    makeContext(data, options) {
      assert.equal(options.publishDelay, -1);
      assert.equal(options.refreshPeriod, 0);
      let ready = unexpectedlyReady;
      let failed = false;
      const attrs = {};
      const promise = data.then(() => { ready = true; }, () => { failed = ready = true; });
      this.context = {
        isReady() { calls.push(`isReady:${ready}`); return ready; },
        isFailed() { return failed; },
        isFinalized() { return false; },
        track(name, properties) { calls.push(['track', name, properties, ready]); },
        ready() { calls.push('wait'); return promise; },
        attribute(name, value) { attrs[name] = value; },
        attrs,
      };
      return this.context;
    }
  }
  const app = {
    use() {},
    get(route, fn) { routes.set(`GET ${route}`, fn); },
    post(route, fn) { routes.set(`POST ${route}`, fn); },
    put() {}, delete() {}, listen() {},
  };
  const express = () => app;
  express.json = () => {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8'), {
    require(name) {
      if (name === 'express') return express;
      if (name === '@absmartly/javascript-sdk') return { SDK, ContextPublisher: Publisher };
      if (name.endsWith('/lib/client')) return { default: class {} };
      return {};
    },
    process: { env: { ABSMARTLY_E2E_ENDPOINT: 'http://collector', ABSMARTLY_E2E_API_KEY: 'fake' }, on() {} },
    console: { log() {}, error() {} },
    URL, setTimeout, clearTimeout,
  });
  return {
    calls,
    get sdk() { return instance; },
    async create(body) {
      let status = 200;
      let response;
      const res = { status(code) { status = code; return this; }, json(value) { response = value; return this; } };
      await routes.get('POST /context')({ body }, res);
      return { status, body: response };
    },
  };
}

const goals = [{ goalName: 'purchase', properties: { revenue: 9.5, nested: { ok: true } } }, { goalName: 'signup' }];

test('gates real SDK data until all tracks, measures each track, and sets attributes', async () => {
  const h = harness();
  const result = await h.create({ mode: 'e2e', preReadyGoals: goals,
    attributes: { sdk_name: 'javascript', e2e_case: 'pre-ready' },
    options: { publishDelay: 0, refreshPeriod: 1 } });
  assert.equal(result.status, 200);
  assert.equal(result.body.result.preReadyTracked, true);
  assert.equal(result.body.result.ready, true);
  assert.equal(result.body.result.failed, false);
  const tracks = h.calls.filter(Array.isArray);
  assert.equal(tracks.length, 2);
  for (let i = 0; i < tracks.length; i++) {
    assert.equal(tracks[i][1], goals[i].goalName);
    assert.equal(tracks[i][2], goals[i].properties);
    assert.equal(tracks[i][3], false);
    assert.equal(h.calls[h.calls.indexOf(tracks[i]) - 1], 'isReady:false');
    assert.ok(h.calls.indexOf(tracks[i]) < h.calls.indexOf('fetch'));
  }
  assert.equal(h.calls[0], 'createWith');
  assert.equal(h.sdk.context.attrs.sdk_name, 'javascript');
  assert.equal(h.sdk.context.attrs.e2e_case, 'pre-ready');
  assert.equal(h.sdk.config.publisher._forwardToSdk, true);
});

test('omitted option leaves normal e2e creation unchanged; empty list still measures readiness', async () => {
  const normal = harness();
  const result = await normal.create({ mode: 'e2e' });
  assert.equal(result.status, 200);
  assert.equal(Object.hasOwn(result.body.result, 'preReadyTracked'), false);
  assert.equal(normal.calls[0], 'create');
  const empty = harness();
  assert.equal((await empty.create({ mode: 'e2e', preReadyGoals: [] })).body.result.preReadyTracked, true);
  assert.equal(empty.calls[0], 'createWith');
});

test('refuses to claim pre-ready tracking if SDK reports ready', async () => {
  const h = harness({ unexpectedlyReady: true });
  const result = await h.create({ mode: 'e2e', preReadyGoals: goals });
  assert.equal(result.status, 500);
  assert.equal(h.calls.some(Array.isArray), false);
  assert.equal(h.calls.includes('fetch'), false);
  assert.equal(result.body.result, undefined);
});

for (const syncFetchError of [false, true]) {
  test(`fetch failure settles through SDK readiness (sync=${syncFetchError})`, async () => {
    const h = harness({ fetchError: true, syncFetchError });
    const result = await h.create({ mode: 'e2e', preReadyGoals: goals });
    assert.equal(result.status, 200);
    assert.equal(result.body.result.failed, true);
    assert.equal(result.body.result.preReadyTracked, true);
  });
}

test('rejects malformed batches atomically, including non-e2e usage', async () => {
  const invalid = [null, {}, 'goal', 1, true, [null], [[]], ['goal'], [{}],
    [{ goalName: '' }], [{ goalName: '  ' }], [{ goalName: 7 }],
    [{ goalName: 'x', extra: true }], ...[null, [], 'bad', 5, false].map(properties => [{ goalName: 'x', properties }])];
  for (const preReadyGoals of invalid) {
    const h = harness();
    const result = await h.create({ mode: 'e2e', preReadyGoals });
    assert.equal(result.status, 400, JSON.stringify(preReadyGoals));
    assert.equal(h.calls.length, 0);
  }
  for (const body of [null, [], 'bad', { preReadyGoals: [] }, { mode: 'test', preReadyGoals: [] },
    { mode: 'e2e', preReadyGoals: [goals[0], {}] },
    ...['attributes', 'units', 'options'].map(key => ({ mode: 'e2e', preReadyGoals: [], [key]: [] }))]) {
    const h = harness();
    assert.equal((await h.create(body)).status, 400);
    assert.equal(h.calls.length, 0);
  }
});
