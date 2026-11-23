// Behavioral self-test behind the `switchback` capability (R33, cluster-defined switchbacks). Mirrors
// the java-wrapper's SwitchbackSelfTest: a sample of the platform golden vectors through the SDK's own
// switchback functions, then switchback serving through a real Context on an R33 payload parsed from
// JSON text, on the same clock-free grid the scenarios use. A javascript-sdk build without switchbacks
// lacks the module or serves the sticky unit assignment, so the battery fails closed.

const absmartly = require('@absmartly/javascript-sdk');

const PREFIX = '[switchback probe]';

// Grid constants shared with generate_switchback_scenarios.py: with a 400,000 h period "now" stays in
// period 0 until 2069.
const PERIOD_MS = 400000 * 3600000;
const ANCHOR_PERIOD_0 = 1704067200000;

// hashUnit("serving-9"): base64url-no-pad MD5, as published in the units.
const SERVING_UNIT_HASH = 'r5uxZRoi-JuM5yYJa0kmSQ';

// [clusterUnit, periodIndex, seed, armCount, expectedVariant], from the platform golden vectors,
// including Long.MIN_VALUE/MAX_VALUE periods and seeds.
const ASSIGN_CASES = [
  ['market_1', '0', '0', 2, 0],
  ['market_1', '-9223372036854775808', '1', 2, 1],
  ['market_1', '9223372036854775807', '1', 2, 1],
  ['123456789', '0', '0', 2, 1],
  ['market_1', '10', '0', 3, 2],
  ['123456789', '-2', '-1', 3, 0],
  ['market_1', '5', '1', 10, 0],
  ['market_2', '-11', '-9223372036854775808', 10, 3],
  ['market_2', '9223372036854775807', '-9223372036854775808', 10, 2],
  ['market_1', '1', '9223372036854775807', 2, 1],
];

// [clusterUnit, cycleIndex, seed, expectedPermutation], including Long.MIN_VALUE/MAX_VALUE cycles.
const PERMUTATION_CASES = [
  ['market_1', '0', '0', [0, 3, 7, 5, 8, 2, 6, 4, 1, 9]],
  ['market_1', '-9223372036854775808', '0', [1, 0]],
  ['market_1', '9223372036854775807', '1', [0, 1]],
  ['123456789', '0', '0', [2, 0, 1]],
];

function loadFunctions() {
  try {
    return require('@absmartly/javascript-sdk/lib/switchback-long');
  } catch (error) {
    return null;
  }
}

function checkGoldenSample(functions) {
  if (functions == null || typeof functions.assignSwitchback !== 'function' ||
      typeof functions.switchbackPermutation !== 'function') {
    console.log(`${PREFIX} FAILED: the linked javascript-sdk has no switchback assigner`);
    return false;
  }
  for (const [unit, period, seed, arms, expected] of ASSIGN_CASES) {
    const actual = functions.assignSwitchback(unit, period, seed, arms);
    if (actual !== expected) {
      console.log(`${PREFIX} golden assign FAILED for ${JSON.stringify([unit, period, seed, arms])}: got ${actual}`);
      return false;
    }
  }
  for (const [unit, cycle, seed, expected] of PERMUTATION_CASES) {
    const actual = functions.switchbackPermutation(unit, cycle, seed, expected.length);
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      console.log(`${PREFIX} golden permutation FAILED for ${unit}/${cycle}/${seed}: expected ` +
        `${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
      return false;
    }
  }
  return true;
}

function base(id, name, seedHi, seedLo, arms) {
  const split = [];
  const variants = [];
  for (let i = 0; i < arms; i++) {
    split.push(1 / arms);
    variants.push({ name: String.fromCharCode(65 + i), config: null });
  }
  return {
    id,
    name,
    iteration: 1,
    unitType: 'unit_serving',
    seedHi,
    seedLo,
    split,
    trafficSeedHi: 1,
    trafficSeedLo: 2,
    trafficSplit: [0, 1],
    fullOnVariant: 0,
    applications: [{ name: 'website' }],
    variants,
    audienceStrict: false,
  };
}

// The switchbackClusters object is written as JSON text so the parsed key order is the declared one.
function switchbackJson(id, name, seedHi, seedLo, arms, clusters, extra) {
  const experiment = Object.assign(base(id, name, seedHi, seedLo, arms), { randomizationType: 'switchback' });
  if (clusters) experiment.unitType = clusters[0][0];
  Object.assign(experiment, {
    switchbackStartAt: ANCHOR_PERIOD_0,
    switchbackPeriodMs: PERIOD_MS,
    switchbackDesignType: 'balanced_block',
    switchbackEstimand: 'global_average_treatment_effect',
  }, extra || {});
  // Named, non-index keys keep their insertion order through JSON.stringify.
  if (clusters) experiment.switchbackClusters = Object.fromEntries(clusters);
  return JSON.stringify(experiment);
}

// The unit baseline: seeds whose 3-arm unit draw for serving-9 is 1.
function unit(id, name) {
  return base(id, name, -559038737, 1234567890, 3);
}

// Every expected variant is a platform golden vector, chosen (with the Python reference) to differ
// from every wrong model for both context uids (serving-9, probe-user): the hashed key, the uid as key,
// the hashed uid as key, a Phase 1E bucket and the sticky unit assignment.
//   assignSwitchback("market_1", 0, 42, 10) = 3; assignSwitchback("123456789", 0, 42, 10) = 6;
//   assignSwitchback("market_1", 0, 1, 3) = 1 (payload shape; not discriminating on its own).
function probePayloadText() {
  const withFields = Object.assign(unit(527, 'sb_unit_with_fields'), {
    randomizationType: 'unit',
    switchbackClusters: { unit_serving: 'city' },
    switchbackClusterValues: ['market_1'],
    switchbackStartAt: ANCHOR_PERIOD_0,
    switchbackPeriodMs: PERIOD_MS,
    switchbackDesignType: 'balanced_block',
    switchbackCarryoverDurationMs: 0,
    switchbackBurnInDurationMs: 0,
    switchbackBurnOutDurationMs: 0,
    switchbackEstimand: 'global_average_treatment_effect',
    switchbackClusterUnitType: 'unit_serving',
    switchbackClusterCount: 10,
    switchbackClusterAttribute: 'city',
  });
  const experiments = [
    switchbackJson(521, 'sb_agree', 0, 42, 10, [['unit_serving', 'city'], ['unit_user', 'profile/city']]),
    switchbackJson(522, 'sb_declared_order', 0, 42, 10, [['unit_user', 'profile/city'], ['unit_serving', 'city']]),
    switchbackJson(529, 'sb_declared_unit', 0, 42, 10, [['unit_user', 'profile/city'], ['unit_serving', 'city']],
      { unitType: 'unit_serving' }),
    switchbackJson(523, 'sb_disagree', 0, 42, 10, [['unit_serving', 'city'], ['unit_user', 'profile/zone']]),
    switchbackJson(524, 'sb_whole_number', 0, 42, 10, [['unit_serving', 'store_number']]),
    // switchbackClusterValues [] admits every value, like null.
    switchbackJson(528, 'sb_allow_empty', 0, 42, 10, [['unit_serving', 'city']], { switchbackClusterValues: [] }),
    switchbackJson(525, 'sb_value_filtered', 0, 1, 10, [['unit_serving', 'city']], { switchbackClusterValues: ['market_2'] }),
    // Only the abandoned Phase 1E fields: no cluster map, so the legacy ineligible control.
    switchbackJson(526, 'sb_phase1e_only', 0, 1, 10, null, {
      switchbackClusterUnitType: 'unit_serving',
      switchbackClusterCount: 10,
      switchbackClusterAttribute: 'city',
    }),
    switchbackJson(513, 'sb_payload', 0, 1, 3, [['unit_serving', 'city']]),
    JSON.stringify(unit(514, 'sb_unit_baseline')),
    JSON.stringify(withFields),
  ];
  return `{"experiments":[${experiments.join(',')}]}`;
}

function exposureOf(id, name, unitType, variant) {
  return {
    id, name, unit: unitType, variant,
    assigned: true, eligible: true, overridden: false, fullOn: false, custom: false,
    audienceMismatch: false, ruleOverride: false,
  };
}

const EXPOSURE_FIELDS = ['id', 'name', 'unit', 'variant', 'assigned', 'eligible', 'overridden', 'fullOn', 'custom',
  'audienceMismatch', 'ruleOverride'];

function servedAs(actual, expected) {
  return EXPOSURE_FIELDS.every((field) => actual[field] === expected[field]) &&
    Number.isInteger(actual.exposedAt) && actual.exposedAt > 0;
}

function expect(context, exposures, check, experimentName, expectedTreatment, expectedExposures) {
  const before = exposures.length;
  const treatment = context.treatment(experimentName);
  const emitted = exposures.slice(before);
  const matches = treatment === expectedTreatment && emitted.length === expectedExposures.length &&
    expectedExposures.every((expected, i) => servedAs(emitted[i], expected));
  if (!matches) {
    console.log(`${PREFIX} check ${check} FAILED: expected treatment=${expectedTreatment} exposures=` +
      `${JSON.stringify(expectedExposures)}, got treatment=${treatment} exposures=${JSON.stringify(emitted)}`);
  }
  return matches;
}

function shapeMismatch(baseline, actual, path) {
  const kind = (v) => (Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v);
  if (kind(baseline) !== kind(actual)) return `${path}: baseline is ${kind(baseline)}, got ${kind(actual)}`;
  if (Array.isArray(baseline)) {
    if (baseline.length !== actual.length) return `${path}: baseline length ${baseline.length}, got ${actual.length}`;
    for (let i = 0; i < baseline.length; i++) {
      const mismatch = shapeMismatch(baseline[i], actual[i], `${path}[${i}]`);
      if (mismatch) return mismatch;
    }
  } else if (kind(baseline) === 'object') {
    const a = Object.keys(baseline).sort();
    const b = Object.keys(actual).sort();
    if (JSON.stringify(a) !== JSON.stringify(b)) return `${path}: baseline fields ${a}, got ${b}`;
    for (const key of a) {
      const mismatch = shapeMismatch(baseline[key], actual[key], `${path}.${key}`);
      if (mismatch) return mismatch;
    }
  }
  return null;
}

function publishedExactly(request, expected) {
  return request.hashed === true && Array.isArray(request.exposures) && request.exposures.length === 1 &&
    servedAs(request.exposures[0], expected) &&
    request.units.some((u) => u.type === 'unit_serving' && u.uid === SERVING_UNIT_HASH);
}

async function publishOne(context, published) {
  const before = published.length;
  await context.publish();
  return published.length === before + 1 ? published[before] : null;
}

async function checkContextServing() {
  const contextData = JSON.parse(probePayloadText());
  const declared = contextData.experiments.find((e) => e.name === 'sb_declared_order');
  if (JSON.stringify(Object.keys(declared.switchbackClusters)) !== '["unit_user","unit_serving"]') {
    console.log(`${PREFIX} FAILED: switchbackClusters lost its declared order`);
    return false;
  }

  const exposures = [];
  const published = [];
  class CapturingPublisher extends absmartly.ContextPublisher {
    publish(request) {
      published.push(JSON.parse(JSON.stringify(request)));
      return Promise.resolve();
    }
  }
  const sdk = new absmartly.SDK({
    endpoint: 'http://dummy',
    apiKey: 'dummy',
    application: 'website',
    environment: 'test',
    eventLogger: (ctx, eventName, data) => {
      if (eventName === 'exposure') exposures.push(data);
    },
    publisher: new CapturingPublisher(),
  });
  // unit_user is set first, so declared order, not context order, must pick the serving unit.
  const context = sdk.createContextWith(
    { units: { unit_user: 'probe-user', unit_serving: 'serving-9' } },
    contextData,
    { publishDelay: -1, refreshPeriod: 0 }
  );
  if (!context.isReady() || context.isFailed()) {
    console.log(`${PREFIX} FAILED: context not ready (failed=${context.isFailed()})`);
    return false;
  }
  context.attribute('city', 'market_1');
  context.attribute('profile', { city: 'market_1', zone: 'market_2' });
  context.attribute('store_number', 123456789);

  const served =
    expect(context, exposures, 'agreement', 'sb_agree', 3, [exposureOf(521, 'sb_agree', 'unit_serving', 3)]) &&
    expect(context, exposures, 'agreement repeat', 'sb_agree', 3, []) &&
    expect(context, exposures, 'declared order', 'sb_declared_order', 3,
      [exposureOf(522, 'sb_declared_order', 'unit_user', 3)]) &&
    expect(context, exposures, 'declared unit differs from unitType', 'sb_declared_unit', 3,
      [exposureOf(529, 'sb_declared_unit', 'unit_user', 3)]) &&
    expect(context, exposures, 'disagreement', 'sb_disagree', 0, []) &&
    expect(context, exposures, 'whole number', 'sb_whole_number', 6,
      [exposureOf(524, 'sb_whole_number', 'unit_serving', 6)]) &&
    expect(context, exposures, 'value filtered out', 'sb_value_filtered', 0, []) &&
    expect(context, exposures, 'empty allow-list admits all', 'sb_allow_empty', 3,
      [exposureOf(528, 'sb_allow_empty', 'unit_serving', 3)]) &&
    expect(context, exposures, 'Phase 1E fields only', 'sb_phase1e_only', 0, []);
  if (!served) return false;

  await context.publish();
  const before = exposures.length;
  const unitServed = exposureOf(514, 'sb_unit_baseline', 'unit_serving', 1);
  const switchbackServed = exposureOf(513, 'sb_payload', 'unit_serving', 1);
  if (!expect(context, exposures, 'unit baseline', 'sb_unit_baseline', 1, [unitServed])) return false;
  const baselineEvent = await publishOne(context, published);
  if (!expect(context, exposures, 'payload', 'sb_payload', 1, [switchbackServed])) return false;
  const switchbackEvent = await publishOne(context, published);
  const emitted = exposures.slice(before);
  if (emitted.length !== 2 || baselineEvent == null || switchbackEvent == null) {
    console.log(`${PREFIX} check payload shape FAILED: expected 2 exposures and 2 publishes, got ` +
      `${JSON.stringify(emitted)} and ${JSON.stringify([baselineEvent, switchbackEvent])}`);
    return false;
  }
  const mismatch = shapeMismatch(emitted[0], emitted[1], 'exposure') ||
    shapeMismatch(baselineEvent, switchbackEvent, 'publish');
  if (mismatch || !publishedExactly(baselineEvent, unitServed) || !publishedExactly(switchbackEvent, switchbackServed)) {
    console.log(`${PREFIX} check publish payload FAILED: ${mismatch || ''} baseline ${JSON.stringify(baselineEvent)}, ` +
      `switchback ${JSON.stringify(switchbackEvent)}`);
    return false;
  }

  // A unit experiment carrying every R33 field publishes the same bytes as the plain baseline once id,
  // name and the timestamps are aligned.
  const beforeInert = exposures.length;
  if (context.treatment('sb_unit_with_fields') !== 1) {
    console.log(`${PREFIX} check unit inertness FAILED: sb_unit_with_fields did not serve 1`);
    return false;
  }
  const inertEvent = await publishOne(context, published);
  if (inertEvent == null ||
      !publishedExactly(inertEvent, exposureOf(527, 'sb_unit_with_fields', 'unit_serving', 1)) ||
      exposures.length !== beforeInert + 1 ||
      !servedAs(exposures[beforeInert], exposureOf(527, 'sb_unit_with_fields', 'unit_serving', 1))) {
    console.log(`${PREFIX} check unit inertness FAILED: ${JSON.stringify(exposures.slice(beforeInert))}`);
    return false;
  }
  const normalized = (event) => {
    const copy = JSON.parse(JSON.stringify(event));
    delete copy.publishedAt;
    Object.assign(copy.exposures[0], { id: 0, name: '', exposedAt: 0 });
    for (const attribute of copy.attributes || []) attribute.setAt = 0;
    return JSON.stringify(copy);
  };
  if (normalized(baselineEvent) !== normalized(inertEvent)) {
    console.log(`${PREFIX} check unit inertness FAILED: publish ${normalized(inertEvent)} differs from ` +
      `${normalized(baselineEvent)}`);
    return false;
  }
  return true;
}

async function runSwitchbackBattery() {
  if (!checkGoldenSample(loadFunctions()) || !(await checkContextServing())) return false;
  console.log(`${PREFIX} behavioral self-test PASSED`);
  return true;
}

let switchbackPromise = null;

async function runSwitchbackSafely() {
  try {
    return await runSwitchbackBattery();
  } catch (err) {
    console.log(`${PREFIX} behavioral self-test FAILED with error: ${err && err.stack ? err.stack : err}`);
    return false;
  }
}

function runSwitchback() {
  if (!switchbackPromise) {
    switchbackPromise = runSwitchbackSafely();
  }
  return switchbackPromise;
}

module.exports = { runSwitchback, checkGoldenSample, loadFunctions };
