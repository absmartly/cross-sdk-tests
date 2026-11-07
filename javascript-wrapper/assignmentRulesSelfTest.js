// Behavioral self-tests for the `assignmentRules` and `semverOperators` wrapper capabilities.
// Mirrors the holdoutSelfTest.js pattern: build real ContextData-shaped fixtures against the built
// js-sdk, drive treatment()/variableValue() and assert BOTH the returned value and the exact
// exposure the SDK fires. A pre-#67 / pre-rules / pre-semver SDK build simply lacks the behaviour,
// so the try/catch below makes the capability report `false` rather than crashing the wrapper.
//
// `assignmentRules`: an experiment's `assignmentRules` JSON string is evaluated; a matching assign
//   rule wins with a `ruleOverride:true`, `assigned:false` exposure and serves variable values.
// `semverOperators`: the semver_* operators work in audience filters (numeric, not lexicographic,
//   comparison; strict audience assigns variant 0 on mismatch).
//
// Lazy, cached, run-once per wrapper process; concurrent /capabilities requests share one run.
// Fails closed on any exception.

const absmartly = require('@absmartly/javascript-sdk');

const UNIT_TYPE = 'session_id';
const UID = 'svk-unit-1';

function experiment(overrides) {
  return Object.assign(
    {
      id: 1,
      name: 'probe',
      iteration: 1,
      unitType: UNIT_TYPE,
      seedHi: 0,
      seedLo: 0,
      split: [0, 1],
      trafficSeedHi: 0,
      trafficSeedLo: 0,
      trafficSplit: [0, 1],
      fullOnVariant: 0,
      applications: [{ name: 'website' }],
      variants: [{ name: 'A', config: null }, { name: 'B', config: null }],
      audience: null,
      audienceStrict: false,
      customFieldValues: null,
    },
    overrides
  );
}

function makeSdk() {
  const exposures = [];
  const sdk = new absmartly.SDK({
    endpoint: 'http://dummy',
    apiKey: 'dummy',
    application: 'website',
    environment: 'test',
    eventLogger: (ctx, eventName, eventData) => {
      if (eventName === 'exposure' && eventData) {
        exposures.push({
          id: eventData.id,
          variant: eventData.variant,
          assigned: eventData.assigned,
          ruleOverride: eventData.ruleOverride,
          audienceMismatch: eventData.audienceMismatch,
        });
      }
    },
  });
  return { sdk, exposures };
}

async function runAssignmentRulesBattery() {
  const prefix = '[assignmentRules probe]';
  const rules = JSON.stringify({ rules: [{ name: 'r', type: 'assign', variant: 1 }] });
  const data = {
    experiments: [
      experiment({
        id: 1,
        name: 'probe_rule',
        assignmentRules: rules,
        variants: [{ name: 'A', config: JSON.stringify({ color: 'red' }) }, { name: 'B', config: JSON.stringify({ color: 'blue' }) }],
      }),
    ],
  };
  const { sdk, exposures } = makeSdk();
  const context = sdk.createContextWith({ units: { [UNIT_TYPE]: UID } }, data, { publishDelay: -1, refreshPeriod: 0 });
  await context.ready();
  if (!context.isReady() || context.isFailed()) {
    console.log(`${prefix} FAILED: context not ready (failed=${context.isFailed()})`);
    return false;
  }
  // A rule win: variant 1, ruleOverride:true, assigned:false, and the exposure is queued.
  const variant = context.treatment('probe_rule');
  if (variant !== 1 || exposures.length !== 1) {
    console.log(`${prefix} FAILED: expected treatment 1 with one exposure, got ${variant} / ${JSON.stringify(exposures)}`);
    return false;
  }
  const exp = exposures[0];
  if (exp.variant !== 1 || exp.ruleOverride !== true || exp.assigned !== false) {
    console.log(`${prefix} FAILED: expected ruleOverride:true assigned:false variant:1, got ${JSON.stringify(exp)}`);
    return false;
  }
  // The rule win also serves variable values.
  const color = context.variableValue('color', 'default');
  if (color !== 'blue') {
    console.log(`${prefix} FAILED: expected variableValue('color') 'blue' through the rule win, got ${JSON.stringify(color)}`);
    return false;
  }
  console.log(`${prefix} PASSED`);
  return true;
}

async function runSemverBattery() {
  const prefix = '[semverOperators probe]';
  // 1.10.0 > 1.9.0 is true only with numeric (not lexicographic) identifier comparison.
  const audience = JSON.stringify({ filter: [{ semver_gt: [{ var: 'app_version' }, { value: '1.9.0' }] }] });
  const data = { experiments: [experiment({ id: 1, name: 'probe_semver', audience, audienceStrict: true })] };

  const match = makeSdk();
  const ctxMatch = match.sdk.createContextWith({ units: { [UNIT_TYPE]: UID } }, data, { publishDelay: -1, refreshPeriod: 0 });
  await ctxMatch.ready();
  ctxMatch.attribute('app_version', '1.10.0');
  const matchVariant = ctxMatch.treatment('probe_semver');
  if (matchVariant !== 1 || match.exposures.length !== 1 || match.exposures[0].audienceMismatch !== false) {
    console.log(`${prefix} FAILED (match): expected variant 1, audienceMismatch:false, got ${matchVariant} / ${JSON.stringify(match.exposures)}`);
    return false;
  }

  const miss = makeSdk();
  const ctxMiss = miss.sdk.createContextWith({ units: { [UNIT_TYPE]: UID } }, data, { publishDelay: -1, refreshPeriod: 0 });
  await ctxMiss.ready();
  ctxMiss.attribute('app_version', '1.2.0');
  const missVariant = ctxMiss.treatment('probe_semver');
  if (missVariant !== 0 || miss.exposures.length !== 1 || miss.exposures[0].audienceMismatch !== true) {
    console.log(`${prefix} FAILED (mismatch): expected variant 0, audienceMismatch:true, got ${missVariant} / ${JSON.stringify(miss.exposures)}`);
    return false;
  }
  console.log(`${prefix} PASSED`);
  return true;
}

let assignmentRulesPromise = null;
let semverPromise = null;

function runAssignmentRules() {
  if (!assignmentRulesPromise) {
    assignmentRulesPromise = runAssignmentRulesBattery().catch((err) => {
      console.log('[assignmentRules probe] FAILED with error: ' + (err && err.stack ? err.stack : err));
      return false;
    });
  }
  return assignmentRulesPromise;
}

function runSemverOperators() {
  if (!semverPromise) {
    semverPromise = runSemverBattery().catch((err) => {
      console.log('[semverOperators probe] FAILED with error: ' + (err && err.stack ? err.stack : err));
      return false;
    });
  }
  return semverPromise;
}

module.exports = { runAssignmentRules, runSemverOperators };
