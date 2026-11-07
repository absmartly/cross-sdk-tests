#!/usr/bin/env node
// Runs the 2026-11 SDK-round scenario cases through the real @absmartly/javascript-sdk, pinned by
// commit in package.json / package-lock.json to javascript-sdk#67 (fix/assignment-rules-edge-cases),
// and prints what the SDK did at every step.
//
// That branch is the reference the other SDKs are being aligned to this round: it is main (already
// the TypeScript rewrite, custom assignment rules from #66, and the semver_* operators) plus the
// four assignment-rule edge-case fixes of PR #67:
//   - a rule variant for an experiment removed from the context data is rebuilt, not served stale;
//   - an audience filter that THROWS while evaluating is a mismatch (strict audience -> variant 0);
//   - assignment rules are parsed once per context-data install;
//   - system attributes are stamped with the earliest event and dropped when the user set the name.
//
// stdin:  {"cases": [{"contextData": {...}, "units": {...}, "steps": [{"action", "params"}]}]}
// stdout: {"sdkVersion": "...", "cases": [{"ready": {...}, "readyEvents": [...], "steps": [{"result", "events"}]}]}
//
// Each step's `events` is the exact list of events the SDK's eventLogger received while that step
// ran, in order. exposedAt / achievedAt / publishedAt timestamps are dropped; nothing else is
// rewritten. Used only by generate_sdk_round_2026_11_scenarios.py.
"use strict";

const fs = require("fs");
const path = require("path");

// The SDK is built from the sibling javascript-sdk checkout (the same source the javascript-wrapper
// and CI build from, pinned by sdk-branches.sh). SDK_PATH overrides the location; the generator
// builds it (npm install + compile + build-cjs) and sets this before invoking compute.js.
const SDK_PATH = process.env.SDK_PATH || path.resolve(__dirname, "..", "..", "..", "javascript-sdk");
const absmartly = require(path.join(SDK_PATH, "lib", "index.js"));

const INSTALLED = JSON.parse(fs.readFileSync(path.join(SDK_PATH, "package.json"), "utf8")).version;

class NullPublisher extends absmartly.ContextPublisher {
	publish() {
		return Promise.resolve();
	}
}

// A data provider whose payload the test can swap, so refresh() re-resolves against new context
// data exactly as a refetch from the collector would. The SDK calls getContextData() on create and
// on every refresh(); it returns the current payload.
class SwappableProvider extends absmartly.ContextDataProvider {
	constructor(initial) {
		super();
		this._data = initial;
	}
	setData(data) {
		this._data = data;
	}
	getContextData() {
		return Promise.resolve(this._data);
	}
}

function strip(eventName, data) {
	if (data === undefined) return undefined;
	const copy = JSON.parse(JSON.stringify(data));
	if (eventName === "exposure") delete copy.exposedAt;
	if (eventName === "goal") delete copy.achievedAt;
	if (eventName === "publish") {
		delete copy.publishedAt;
		for (const e of copy.exposures || []) delete e.exposedAt;
		for (const g of copy.goals || []) delete g.achievedAt;
	}
	return copy;
}

async function runCase(c) {
	const log = [];
	const provider = new SwappableProvider(c.contextData);
	const sdk = new absmartly.SDK({
		endpoint: "http://unused.invalid",
		apiKey: "unused",
		application: "website",
		environment: "test",
		publisher: new NullPublisher(),
		provider,
		eventLogger: (ctx, eventName, data) => log.push({ type: eventName, data: strip(eventName, data) }),
	});
	// createContextWith with pre-fetched data so creation is deterministic; refresh() still goes
	// through the provider above.
	const context = sdk.createContextWith({ units: c.units }, c.contextData, {
		publishDelay: -1,
		refreshPeriod: 0,
	});
	await context.ready();
	const out = {
		ready: { ready: context.isReady(), failed: context.isFailed(), finalized: context.isFinalized() },
		readyEvents: log.splice(0),
		steps: [],
	};
	for (const step of c.steps) {
		const p = step.params || {};
		let result = null;
		switch (step.action) {
			case "treatment":
				result = context.treatment(p.experimentName);
				break;
			case "peek":
				result = context.peek(p.experimentName);
				break;
			case "variableValue":
				result = context.variableValue(p.key, p.defaultValue);
				break;
			case "peekVariableValue":
				result = context.peekVariableValue(p.key, p.defaultValue);
				break;
			case "override":
				context.override(p.experimentName, p.variant);
				result = null;
				break;
			case "customAssignment":
				context.customAssignment(p.experimentName, p.variant);
				result = null;
				break;
			case "attribute":
				context.attribute(p.name, p.value);
				result = null;
				break;
			case "pending":
				result = context.pending();
				break;
			case "publish":
				await context.publish();
				result = null;
				break;
			case "refresh":
				if (p.newData !== undefined) provider.setData(p.newData);
				await context.refresh();
				result = null;
				break;
			default:
				throw new Error(`unsupported action ${step.action}`);
		}
		const events = log.splice(0);
		out.steps.push({ result: result === undefined ? null : result, events });
	}
	return out;
}

async function main() {
	const input = JSON.parse(fs.readFileSync(0, "utf8"));
	const cases = [];
	for (const c of input.cases) cases.push(await runCase(c));
	process.stdout.write(JSON.stringify({ sdkVersion: INSTALLED, cases }) + "\n");
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
