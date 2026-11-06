#!/usr/bin/env node
// Runs shared-variable-key cases through the real @absmartly/javascript-sdk (version pinned
// exactly in package.json / package-lock.json) and prints what the SDK did at every step.
//
// stdin:  {"cases": [{"contextData": {...}, "units": {...}, "steps": [{"action", "params"}]}]}
// stdout: {"sdkVersion": "...", "cases": [{"ready": [...], "steps": [{"result", "events"}]}]}
//
// Each step's `events` is the exact list of events the SDK's eventLogger received while that
// step ran, in order. exposedAt / publishedAt timestamps are dropped; nothing else is
// rewritten. Used only by generate_shared_variable_key_scenarios.py.
'use strict';

const fs = require('fs');
const absmartly = require('@absmartly/javascript-sdk');

const PINNED = require('./package.json').dependencies['@absmartly/javascript-sdk'];
const INSTALLED = JSON.parse(fs.readFileSync(require.resolve('@absmartly/javascript-sdk/package.json'), 'utf8')).version;
if (INSTALLED !== PINNED) {
  console.error(`installed @absmartly/javascript-sdk ${INSTALLED} != pinned ${PINNED}; run npm ci`);
  process.exit(2);
}

class NullPublisher extends absmartly.ContextPublisher {
  publish() {
    return Promise.resolve();
  }
}

function strip(eventName, data) {
  if (data === undefined) return undefined;
  const copy = JSON.parse(JSON.stringify(data));
  if (eventName === 'exposure') delete copy.exposedAt;
  if (eventName === 'publish') {
    delete copy.publishedAt;
    for (const e of copy.exposures || []) delete e.exposedAt;
  }
  return copy;
}

async function runCase(c) {
  const log = [];
  const sdk = new absmartly.SDK({
    endpoint: 'http://unused.invalid',
    apiKey: 'unused',
    application: 'website',
    environment: 'test',
    publisher: new NullPublisher(),
    eventLogger: (ctx, eventName, data) => log.push({ type: eventName, data: strip(eventName, data) }),
  });
  const context = sdk.createContextWith({ units: c.units }, c.contextData, { publishDelay: -1, refreshPeriod: 0 });
  await context.ready();
  const out = {
    ready: { ready: context.isReady(), failed: context.isFailed(), finalized: context.isFinalized() },
    readyEvents: log.splice(0),
    steps: [],
  };
  for (const step of c.steps) {
    const p = step.params || {};
    let result;
    switch (step.action) {
      case 'variableValue': result = context.variableValue(p.key, p.defaultValue); break;
      case 'peekVariableValue': result = context.peekVariableValue(p.key, p.defaultValue); break;
      case 'override': context.override(p.experimentName, p.variant); result = null; break;
      case 'treatment': result = context.treatment(p.experimentName); break;
      case 'peek': result = context.peek(p.experimentName); break;
      case 'attribute': context.attribute(p.name, p.value); result = null; break;
      case 'pending': result = context.pending(); break;
      case 'publish': await context.publish(); result = null; break;
      default: throw new Error(`unsupported action ${step.action}`);
    }
    out.steps.push({ result: result === undefined ? null : result, events: log.splice(0) });
  }
  return out;
}

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const cases = [];
  for (const c of input.cases) cases.push(await runCase(c));
  process.stdout.write(JSON.stringify({ sdkVersion: INSTALLED, cases }) + '\n');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
