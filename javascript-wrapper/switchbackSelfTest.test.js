// Run with node --test, with the linked switchback SDK installed.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const absmartly = require('@absmartly/javascript-sdk');

async function runFresh() {
  delete require.cache[require.resolve('./switchbackSelfTest')];
  return require('./switchbackSelfTest').runSwitchback();
}

test('the linked SDK passes the full battery', async () => {
  assert.equal(await runFresh(), true);
});

test('an inert publish cannot reuse the baseline identity', async () => {
  const original = absmartly.SDK.prototype.createContextWith;
  absmartly.SDK.prototype.createContextWith = function (...args) {
    const context = original.apply(this, args);
    const publish = context._publisher.publish;
    context._publisher.publish = function (request) {
      const copy = JSON.parse(JSON.stringify(request));
      for (const exposure of copy.exposures || []) {
        if (exposure.id === 527) Object.assign(exposure, { id: 514, name: 'sb_unit_baseline' });
      }
      return publish.call(this, copy);
    };
    return context;
  };
  try {
    assert.equal(await runFresh(), false);
  } finally {
    absmartly.SDK.prototype.createContextWith = original;
  }
});

test('taking the exposure unit from unitType fails the agreement fixture', async () => {
  const original = absmartly.SDK.prototype.createContextWith;
  let observed = false;
  absmartly.SDK.prototype.createContextWith = function (...args) {
    const fixture = args[1].experiments.find((e) => e.name === 'sb_declared_unit');
    assert.ok(fixture, 'the discriminating fixture must be present');
    assert.equal(fixture.unitType, 'unit_serving');
    assert.equal(Object.keys(fixture.switchbackClusters)[0], 'unit_user');
    observed = true;
    const context = original.apply(this, args);
    const log = context._logEvent;
    context._logEvent = function (name, data) {
      if (name === 'exposure' && data.name === fixture.name) data.unit = fixture.unitType;
      return log.call(this, name, data);
    };
    return context;
  };
  try {
    assert.equal(await runFresh(), false);
    assert.equal(observed, true);
  } finally {
    absmartly.SDK.prototype.createContextWith = original;
  }
});
