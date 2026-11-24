// Behavioral self-test for the `sharedVariableKeys` wrapper capability (WRAPPER_API_SPEC.md): a key
// shared by several experiments resolves as java-sdk Context.getVariableAssignment. Mirrors the
// assignmentRulesSelfTest.js pattern: real ContextData fixtures against the built js-sdk, asserting
// both the returned value and the exact exposures. Two batteries, taken from scenarios 344 and 351:
//
//   * a strict-mismatched lower-id candidate listed after an assigned higher-id one: candidates are
//     visited in id order, the unassigned one is skipped and never exposed, the assigned one wins;
//   * the winner's variant lacks the key: the default is returned (no fall-through to the later
//     candidate) and only the winner is exposed.
//
// peekVariableValue must expose nothing in both. Lazy, cached, run-once; fails closed on any exception.

const absmartly = require('@absmartly/javascript-sdk');

const UNIT_TYPE = 'session_id';
const UID = 'svk-probe-unit';
const KEY = 'shared.key';
const DEFAULT = 'caller-default';
const AUDIENCE_US = JSON.stringify({ filter: [{ eq: [{ var: 'Country' }, { value: 'US' }] }] });

// split [0, 1] assigns every unit variant 1.
function experiment(id, name, configs, overrides) {
  return Object.assign(
    {
      id,
      name,
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
      variants: configs.map((config, i) => ({ name: String.fromCharCode(65 + i), config: JSON.stringify(config) })),
      audience: null,
      audienceStrict: false,
      customFieldValues: null,
    },
    overrides
  );
}

async function resolve(data, attributes) {
  const exposures = [];
  const sdk = new absmartly.SDK({
    endpoint: 'http://dummy',
    apiKey: 'dummy',
    application: 'website',
    environment: 'test',
    eventLogger: (ctx, eventName, eventData) => {
      if (eventName === 'exposure' && eventData) exposures.push({ id: eventData.id, variant: eventData.variant });
    },
  });
  const context = sdk.createContextWith({ units: { [UNIT_TYPE]: UID } }, data, { publishDelay: -1, refreshPeriod: 0 });
  await context.ready();
  if (!context.isReady() || context.isFailed()) throw new Error(`context not ready (failed=${context.isFailed()})`);
  for (const [name, value] of Object.entries(attributes)) context.attribute(name, value);
  const peeked = context.peekVariableValue(KEY, DEFAULT);
  const peekExposures = exposures.length;
  const value = context.variableValue(KEY, DEFAULT);
  return { peeked, peekExposures, value, exposures, pending: context.pending() };
}

function check(prefix, label, got, want) {
  const ok =
    got.peeked === want.value &&
    got.peekExposures === 0 &&
    got.value === want.value &&
    got.pending === 1 &&
    got.exposures.length === 1 &&
    got.exposures[0].id === want.id &&
    got.exposures[0].variant === 1;
  if (!ok) console.log(`${prefix} FAILED (${label}): want ${JSON.stringify(want)} with one exposure, got ${JSON.stringify(got)}`);
  return ok;
}

async function runBattery() {
  const prefix = '[sharedVariableKeys probe]';
  const strictMismatch = await resolve(
    {
      experiments: [
        experiment(176, 'svk_assigned', [{ [KEY]: 'assigned-control' }, { [KEY]: 'assigned-treatment' }]),
        experiment(175, 'svk_strict', [{ [KEY]: 'strict-control' }, { [KEY]: 'strict-treatment' }], {
          audience: AUDIENCE_US,
          audienceStrict: true,
        }),
      ],
    },
    { Country: 'IE' }
  );
  if (!check(prefix, 'strict mismatch skipped', strictMismatch, { value: 'assigned-treatment', id: 176 })) return false;

  const lacksKey = await resolve(
    {
      experiments: [
        experiment(195, 'svk_lacks_key', [{ [KEY]: 'lacks-control' }, { 'other.key': 'x' }]),
        experiment(196, 'svk_after', [{ [KEY]: 'after-control' }, { [KEY]: 'after-treatment' }]),
      ],
    },
    {}
  );
  if (!check(prefix, 'winner lacks key', lacksKey, { value: DEFAULT, id: 195 })) return false;

  console.log(`${prefix} PASSED`);
  return true;
}

let promise = null;

function runSharedVariableKeys() {
  if (!promise) {
    promise = runBattery().catch((err) => {
      console.log('[sharedVariableKeys probe] FAILED with error: ' + (err && err.stack ? err.stack : err));
      return false;
    });
  }
  return promise;
}

module.exports = { runSharedVariableKeys };
