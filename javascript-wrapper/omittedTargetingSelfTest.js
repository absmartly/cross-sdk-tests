const absmartly = require('@absmartly/javascript-sdk');

let cached;

async function battery() {
  let passed = true;
  for (const strict of [false, true]) {
    for (const rulesSignal of [false, true]) {
      const exposures = [];
      const filter = country => JSON.stringify({ filter: [{ eq: [{ var: 'country' }, { value: country }] }] });
      const experiment = {
        id: 1, name: 'probe_targeting', iteration: 1, unitType: 'session_id',
        seedHi: 0, seedLo: 0, split: [0, 1], trafficSeedHi: 0, trafficSeedLo: 0,
        trafficSplit: [0, 1], fullOnVariant: 0, applications: [{ name: 'website' }],
        variants: [{ name: 'A', config: null }, { name: 'B', config: null }],
        audience: filter('US'), audienceStrict: strict,
      };
      const refreshed = { ...experiment };
      if (rulesSignal) refreshed.audience = filter('CA');
      else delete refreshed.audience;
      const sdk = new absmartly.SDK({ endpoint: 'http://dummy', apiKey: 'dummy',
        application: 'website', environment: 'test',
        eventLogger: (ctx, type, data) => {
          if (type === 'exposure') exposures.push({ ...data });
        },
      });
      const context = sdk.createContextWith({ units: { session_id: 'test123' } },
        { experiments: [experiment] }, { publishDelay: -1, refreshPeriod: 0 });
      // Exercise refresh through the public provider interface, without network access.
      context.provider().getContextData = () => Promise.resolve({ experiments: [refreshed] });
      await context.ready();
      context.attribute('country', 'CA');
      const before = context.treatment(experiment.name);
      await context.refresh();
      if (!rulesSignal) context.attribute('unrelated', 'changed');
      const after = context.treatment(experiment.name);
      const event = exposures[1];
      passed = passed && before === (strict ? 0 : 1) && after === 1 &&
        exposures.length === 2 && exposures[0].audienceMismatch === true &&
        event.assigned === true && event.audienceMismatch === false;
    }
  }
  return passed;
}

function run() {
  if (!cached) cached = battery().catch(() => false);
  return cached;
}

module.exports = { run };
