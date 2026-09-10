// Behavioral self-test for the `holdouts` / `holdout_arms` wrapper capabilities. Mirrors the
// java-wrapper's HoldoutSelfTest/HoldoutArmsSelfTest pattern (see
// java-wrapper/src/main/java/com/absmartly/wrapper/{HoldoutProbeSupport,HoldoutSelfTest,
// HoldoutArmsSelfTest}.java): build a real ContextData-shaped fixture ported from the actual
// cross-sdk-tests scenario battery (test_scenarios_complete.json), construct a real Context
// against the built js-sdk, call treatment() and assert BOTH the returned variant and the exact
// ordered sequence of exposure events it fires. No reflection is needed here (unlike java) -
// this is plain JS, so a pre-holdout SDK build simply lacks the relevant fields/behavior and the
// try/catch below fails closed naturally.
//
// Lazy, cached, run-once: each battery only actually executes once per wrapper process lifetime;
// the result (a Promise, so concurrent /capabilities requests share one in-flight run) is cached
// at module scope. Fails closed: any exception anywhere in the battery (missing exports, thrown
// assertions, unexpected shape) makes the capability report `false` rather than crashing the
// wrapper or the /capabilities endpoint.

const absmartly = require('@absmartly/javascript-sdk');

function buildExperiment({ id, name, unitType, seedHi, seedLo, split, fullOnVariant, variantNames, holdoutIds }) {
  return {
    id,
    name,
    iteration: 1,
    unitType,
    seedHi,
    seedLo,
    split,
    trafficSeedHi: 1,
    trafficSeedLo: 2,
    trafficSplit: [0, 1],
    fullOnVariant,
    applications: [{ name: 'website' }],
    variants: variantNames.map((variantName) => ({ name: variantName, config: null })),
    audience: null,
    audienceStrict: false,
    customFieldValues: null,
    holdoutIds,
  };
}

function buildHoldout({ id, name, unitType, seedHi, seedLo, split, variantNames, holdoutType }) {
  return {
    id,
    name,
    iteration: 1,
    unitType,
    seedHi,
    seedLo,
    split,
    trafficSeedHi: 0,
    trafficSeedLo: 0,
    trafficSplit: [0, 1],
    fullOnVariant: 0,
    applications: [],
    variants: variantNames.map((variantName) => ({ name: variantName, config: null })),
    audience: null,
    audienceStrict: false,
    customFieldValues: null,
    holdoutType,
  };
}

function matchesSequence(actual, expected) {
  if (actual.length !== expected.length) return false;
  for (let i = 0; i < expected.length; i++) {
    const a = actual[i];
    const e = expected[i];
    if (a.id !== e.id || a.name !== e.name || a.variant !== e.variant) return false;
  }
  return true;
}

// Calls treatment(experimentName) exactly once, then requires BOTH the returned treatment AND
// the exact ordered id/name/variant sequence of exposure events appended by that single call to
// match the expected sequence. Order and every field matter - a check that only compared
// id/name/count could pass against an SDK that resolves the right ids with the wrong variants or
// the wrong order.
function verifyTreatmentAndExposures(logPrefix, checkLabel, context, exposureLog, experimentName, expectedTreatment, expectedSequence) {
  const before = exposureLog.length;
  const treatment = context.treatment(experimentName);
  const newExposures = exposureLog.slice(before);

  if (treatment !== expectedTreatment || !matchesSequence(newExposures, expectedSequence)) {
    console.log(
      `${logPrefix} check ${checkLabel} FAILED: expected treatment=${expectedTreatment} with exposure sequence ` +
        `${JSON.stringify(expectedSequence)}, got treatment=${treatment} exposures=${JSON.stringify(newExposures)}`
    );
    return false;
  }
  return true;
}

function makeExposureLoggingSdk() {
  const exposureLog = [];
  const sdk = new absmartly.SDK({
    endpoint: 'http://dummy',
    apiKey: 'dummy',
    application: 'test',
    environment: 'test',
    eventLogger: (ctx, eventName, eventData) => {
      if (eventName === 'exposure' && eventData) {
        exposureLog.push({ id: eventData.id, name: eventData.name, variant: eventData.variant });
      }
    },
  });
  return { sdk, exposureLog };
}

// Two-arm holdout semantics battery. Ported from real fixture values in
// cross-sdk-tests/test_scenarios_complete.json:
//   - scenario 203 (index 202) "Held-Out Unit Suppresses Covered Experiment" -> check A
//   - scenario 204 (index 203) "Not-Held-Out Unit Assigns Normally And Exposes Holdout" -> check B
//   - scenario 208 (index 207) "Suppresses Full-On Experiment Regardless Of Full-On Variant" -> check F
// Synthetic ids (301-303 for experiments, 401-403 for holdouts) are used instead of the
// scenarios' own ids so all three checks can be combined onto a single Context without id
// collisions; every value that actually drives variant assignment (unitType, seedHi, seedLo,
// split, fullOnVariant, the unit id) is carried over verbatim from the source scenario.
async function runHoldoutsBattery() {
  const HELD_OUT_UNIT_TYPE = 'session_id';
  // Same unit id used by scenarios 203/205/206/207/208: relative to a holdout with
  // seedHi=13/seedLo=111/split=[0.1,0.9] it lands in variant 0 (held out).
  const HELD_OUT_UID = 'e791e240fcd3df7d238cfc285f475e8152fcc0ec';

  const NOT_HELD_OUT_UNIT_TYPE = 'session_id_b';
  // Same unit id used by scenario 204: relative to the same seedHi/seedLo/split it lands
  // OUTSIDE the holdout's held-out arm (variant 1).
  const NOT_HELD_OUT_UID = 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3';

  // --- Check A fixture (mirrors scenario 203) ---
  const expA = buildExperiment({
    id: 301,
    name: 'probe_a_covered',
    unitType: HELD_OUT_UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: [0.5, 0.5],
    fullOnVariant: 0,
    variantNames: ['A', 'B'],
    holdoutIds: [401],
  });
  const holdoutA = buildHoldout({
    id: 401,
    name: 'probe_a_holdout',
    unitType: HELD_OUT_UNIT_TYPE,
    seedHi: 13,
    seedLo: 111,
    split: [0.1, 0.9],
    variantNames: ['A', 'B'],
    holdoutType: 'full',
  });

  // --- Check B fixture (mirrors scenario 204) ---
  const expB = buildExperiment({
    id: 302,
    name: 'probe_b_covered',
    unitType: NOT_HELD_OUT_UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: [0.5, 0.5],
    fullOnVariant: 0,
    variantNames: ['A', 'B'],
    holdoutIds: [402],
  });
  const holdoutB = buildHoldout({
    id: 402,
    name: 'probe_b_holdout',
    unitType: NOT_HELD_OUT_UNIT_TYPE,
    seedHi: 13,
    seedLo: 111,
    split: [0.1, 0.9],
    variantNames: ['A', 'B'],
    holdoutType: 'full',
  });

  // --- Check F fixture (mirrors scenario 208: suppresses a full-on experiment) ---
  const expF = buildExperiment({
    id: 303,
    name: 'probe_f_fullon',
    unitType: HELD_OUT_UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: [0.5, 0.5],
    fullOnVariant: 2,
    variantNames: ['A', 'B', 'C'],
    holdoutIds: [403],
  });
  const holdoutF = buildHoldout({
    id: 403,
    name: 'probe_f_holdout',
    unitType: HELD_OUT_UNIT_TYPE,
    seedHi: 13,
    seedLo: 111,
    split: [0.1, 0.9],
    variantNames: ['A', 'B'],
    holdoutType: 'full',
  });

  const data = {
    experiments: [expA, expB, expF],
    holdouts: [holdoutA, holdoutB, holdoutF],
  };

  const { sdk, exposureLog } = makeExposureLoggingSdk();

  const context = sdk.createContextWith(
    { units: { [HELD_OUT_UNIT_TYPE]: HELD_OUT_UID, [NOT_HELD_OUT_UNIT_TYPE]: NOT_HELD_OUT_UID } },
    data,
    { publishDelay: -1, refreshPeriod: 0 }
  );

  await context.ready();

  if (!context.isReady() || context.isFailed()) {
    console.log(`[holdouts probe] behavioral self-test FAILED: context not ready (failed=${context.isFailed()})`);
    return false;
  }

  const prefix = '[holdouts probe]';

  if (
    !verifyTreatmentAndExposures(prefix, 'A (mirrors 203)', context, exposureLog, 'probe_a_covered', 0, [
      { id: 401, name: 'probe_a_holdout', variant: 0 },
    ])
  )
    return false;

  if (
    !verifyTreatmentAndExposures(prefix, 'B (mirrors 204)', context, exposureLog, 'probe_b_covered', 0, [
      { id: 302, name: 'probe_b_covered', variant: 0 },
      { id: 402, name: 'probe_b_holdout', variant: 1 },
    ])
  )
    return false;

  if (
    !verifyTreatmentAndExposures(prefix, 'F (mirrors 208)', context, exposureLog, 'probe_f_fullon', 0, [
      { id: 403, name: 'probe_f_holdout', variant: 0 },
    ])
  )
    return false;

  console.log(`${prefix} behavioral self-test PASSED: all 3 checks mirroring scenarios 203, 204, 208 passed`);
  return true;
}

// Three-arm holdout semantics battery. Ported from real fixture values in
// cross-sdk-tests/test_scenarios_complete.json:
//   - scenario 214 (index 213) "Three-Arm Variant 0 Holds Out Full-On And Non-Full-On" -> check 0
//   - scenario 215 (index 214) "Three-Arm Variant 1 Holds Out Non-Full-On, Assigns Full-On Normally" -> check 1
//   - scenario 216 (index 215) "Three-Arm Variant 2 Evaluates Both Experiments Normally" -> check 2
// Same ids as the scenarios themselves (601-606 for experiments, 621-623 for holdouts) since
// they were already collision-free and this is the same numbering java-wrapper's
// HoldoutArmsSelfTest uses for the same checks.
async function runHoldoutArmsBattery() {
  const UNIT_TYPE = 'session_id';
  // Same unit id used by scenarios 214-216; relative to the seedHi/seedLo pairs below it
  // deterministically lands in each holdout's variant 0, 1, and 2 respectively.
  const UID = 'e791e240fcd3df7d238cfc285f475e8152fcc0ec';

  const COVERED_SPLIT = [0.5, 0.5];
  const THREE_ARM_SPLIT = [0.3, 0.3, 0.4];
  const COVERED_FULL_ON_VARIANT = 2;

  // --- Check 0 fixture (mirrors scenario 214: arm 0 holds out both) ---
  const exp0NonFullOn = buildExperiment({
    id: 601,
    name: 'arms_0_non_fullon',
    unitType: UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: COVERED_SPLIT,
    fullOnVariant: 0,
    variantNames: ['A', 'B'],
    holdoutIds: [621],
  });
  const exp0FullOn = buildExperiment({
    id: 602,
    name: 'arms_0_fullon',
    unitType: UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: COVERED_SPLIT,
    fullOnVariant: COVERED_FULL_ON_VARIANT,
    variantNames: ['A', 'B', 'C'],
    holdoutIds: [621],
  });
  const holdout0 = buildHoldout({
    id: 621,
    name: 'arms_holdout_0',
    unitType: UNIT_TYPE,
    seedHi: 0,
    seedLo: 1,
    split: THREE_ARM_SPLIT,
    variantNames: ['A', 'B', 'C'],
    holdoutType: 'all_full_on',
  });

  // --- Check 1 fixture (mirrors scenario 215: arm 1 holds out only the non-full-on one) ---
  const exp1NonFullOn = buildExperiment({
    id: 603,
    name: 'arms_1_non_fullon',
    unitType: UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: COVERED_SPLIT,
    fullOnVariant: 0,
    variantNames: ['A', 'B'],
    holdoutIds: [622],
  });
  const exp1FullOn = buildExperiment({
    id: 604,
    name: 'arms_1_fullon',
    unitType: UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: COVERED_SPLIT,
    fullOnVariant: COVERED_FULL_ON_VARIANT,
    variantNames: ['A', 'B', 'C'],
    holdoutIds: [622],
  });
  const holdout1 = buildHoldout({
    id: 622,
    name: 'arms_holdout_1',
    unitType: UNIT_TYPE,
    seedHi: 0,
    seedLo: 3,
    split: THREE_ARM_SPLIT,
    variantNames: ['A', 'B', 'C'],
    holdoutType: 'all_full_on',
  });

  // --- Check 2 fixture (mirrors scenario 216: arm 2 evaluates both normally) ---
  const exp2NonFullOn = buildExperiment({
    id: 605,
    name: 'arms_2_non_fullon',
    unitType: UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: COVERED_SPLIT,
    fullOnVariant: 0,
    variantNames: ['A', 'B'],
    holdoutIds: [623],
  });
  const exp2FullOn = buildExperiment({
    id: 606,
    name: 'arms_2_fullon',
    unitType: UNIT_TYPE,
    seedHi: 100,
    seedLo: 200,
    split: COVERED_SPLIT,
    fullOnVariant: COVERED_FULL_ON_VARIANT,
    variantNames: ['A', 'B', 'C'],
    holdoutIds: [623],
  });
  const holdout2 = buildHoldout({
    id: 623,
    name: 'arms_holdout_2',
    unitType: UNIT_TYPE,
    seedHi: 0,
    seedLo: 0,
    split: THREE_ARM_SPLIT,
    variantNames: ['A', 'B', 'C'],
    holdoutType: 'all_full_on',
  });

  const data = {
    experiments: [exp0NonFullOn, exp0FullOn, exp1NonFullOn, exp1FullOn, exp2NonFullOn, exp2FullOn],
    holdouts: [holdout0, holdout1, holdout2],
  };

  const { sdk, exposureLog } = makeExposureLoggingSdk();

  const context = sdk.createContextWith({ units: { [UNIT_TYPE]: UID } }, data, { publishDelay: -1, refreshPeriod: 0 });

  await context.ready();

  if (!context.isReady() || context.isFailed()) {
    console.log(`[holdout_arms probe] FAILED: context not ready (failed=${context.isFailed()})`);
    return false;
  }

  const prefix = '[holdout_arms probe]';

  // Check 0 (mirrors 214): arm 0 holds out both the non-full-on and the full-on covered
  // experiment; the holdout's own exposure fires once, at variant 0, and neither covered
  // experiment emits its own exposure.
  if (
    !verifyTreatmentAndExposures(prefix, '0 non-full-on (mirrors 214)', context, exposureLog, 'arms_0_non_fullon', 0, [
      { id: 621, name: 'arms_holdout_0', variant: 0 },
    ])
  )
    return false;
  if (!verifyTreatmentAndExposures(prefix, '0 full-on (mirrors 214)', context, exposureLog, 'arms_0_fullon', 0, [])) return false;

  // Check 1 (mirrors 215): arm 1 holds out only the non-full-on experiment; the full-on
  // experiment takes its normal path and is assigned its own fullOnVariant with its own exposure.
  if (
    !verifyTreatmentAndExposures(prefix, '1 non-full-on (mirrors 215)', context, exposureLog, 'arms_1_non_fullon', 0, [
      { id: 622, name: 'arms_holdout_1', variant: 1 },
    ])
  )
    return false;
  if (
    !verifyTreatmentAndExposures(
      prefix,
      '1 full-on (mirrors 215)',
      context,
      exposureLog,
      'arms_1_fullon',
      COVERED_FULL_ON_VARIANT,
      [{ id: 604, name: 'arms_1_fullon', variant: COVERED_FULL_ON_VARIANT }]
    )
  )
    return false;

  // Check 2 (mirrors 216): arm 2 (normal traffic) evaluates both covered experiments exactly as
  // if uncovered; the holdout's own exposure fires once, at variant 2, and each covered
  // experiment emits its own exposure with its normally-assigned variant.
  if (
    !verifyTreatmentAndExposures(prefix, '2 non-full-on (mirrors 216)', context, exposureLog, 'arms_2_non_fullon', 1, [
      { id: 605, name: 'arms_2_non_fullon', variant: 1 },
      { id: 623, name: 'arms_holdout_2', variant: 2 },
    ])
  )
    return false;
  if (
    !verifyTreatmentAndExposures(
      prefix,
      '2 full-on (mirrors 216)',
      context,
      exposureLog,
      'arms_2_fullon',
      COVERED_FULL_ON_VARIANT,
      [{ id: 606, name: 'arms_2_fullon', variant: COVERED_FULL_ON_VARIANT }]
    )
  )
    return false;

  console.log(`${prefix} behavioral self-test PASSED: all 3 arm checks mirroring scenarios 214-216 passed`);
  return true;
}

let holdoutsPromise = null;
let holdoutArmsPromise = null;

async function runHoldoutsSafely() {
  try {
    return await runHoldoutsBattery();
  } catch (err) {
    // Incompatible/regressed SDK builds can throw (missing exports, unexpected shape, thrown
    // assertions); the capability must fail closed rather than crash the wrapper.
    console.log('[holdouts probe] behavioral self-test FAILED with error: ' + (err && err.stack ? err.stack : err));
    return false;
  }
}

async function runHoldoutArmsSafely() {
  try {
    // Structural gate: three-arm support cannot exist without basic holdout support, so this is
    // a hard precondition rather than a second, independently-passable check that merely happens
    // to agree with `holdouts`.
    const baseOk = await runHoldouts();
    if (!baseOk) {
      console.log('[holdout_arms probe] skipped: base holdouts battery did not pass');
      return false;
    }
    return await runHoldoutArmsBattery();
  } catch (err) {
    console.log('[holdout_arms probe] behavioral self-test FAILED with error: ' + (err && err.stack ? err.stack : err));
    return false;
  }
}

// Runs (and caches) the two-arm holdout battery. Caching the in-flight Promise itself (not just
// its resolved value) means concurrent /capabilities requests before the first run completes all
// share one execution rather than racing multiple runs.
function runHoldouts() {
  if (!holdoutsPromise) {
    holdoutsPromise = runHoldoutsSafely();
  }
  return holdoutsPromise;
}

function runHoldoutArms() {
  if (!holdoutArmsPromise) {
    holdoutArmsPromise = runHoldoutArmsSafely();
  }
  return holdoutArmsPromise;
}

module.exports = { runHoldouts, runHoldoutArms };
