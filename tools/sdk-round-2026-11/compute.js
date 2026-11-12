#!/usr/bin/env node
// Runs the 2026-11 SDK-round scenario cases through the real @absmartly/javascript-sdk, pinned by
// PINNED_COMMIT in generate_sdk_round_2026_11_scenarios.py to javascript-sdk#68
// (feat/sdk-system-attributes), and prints what the SDK did at every step.
//
// That branch is the reference the other SDKs are being aligned to this round: it is main (already
// the TypeScript rewrite, custom assignment rules from #66, the semver_* operators, and the four
// #67 assignment-rule edge-case fixes) plus the §5 system-attributes change:
//   - audiences and assignment-rule conditions always see the configured application and environment
//     names (system values first, user attributes overlaid on top), and app_version is dropped from
//     both the evaluation map and the publish payload.
//
// stdin:  {"cases": [{"contextData": {...}, "units": {...}, "client": {application, environment}?, "steps": [{"action", "params"}]}]}
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
// The reference Client, so a case can configure the client application/environment
// (the 2026-11 system-attributes round). A case may also ask for NO environment
// (the "no environment configured" case), which the real Client's constructor
// forbids; NoEnvironmentClient constructs with a placeholder and reports the
// environment as absent, exactly as the SDK's own unit tests mock getEnvironment()
// to undefined. This mirrors what a wrapper does when POST /context omits
// client.environment.
const Client = require(path.join(SDK_PATH, "lib", "client.js")).default;
class NoEnvironmentClient extends Client {
	getEnvironment() {
		return undefined;
	}
}

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
	const eventLogger = (ctx, eventName, data) => log.push({ type: eventName, data: strip(eventName, data) });
	// Per-case client config (the system-attributes group). Default to the fixed
	// application "website" / environment "test" every other case has always used,
	// so the rules/semver/re-eval groups are byte-for-byte unchanged. When a case
	// sets client.application / client.environment, configure the SDK client with
	// them; an explicitly absent environment means "no environment configured", so
	// the SDK is built with a client that reports no environment.
	const client = c.client || {};
	const application = "application" in client ? client.application : "website";
	const hasEnvironment = "environment" in client ? client.environment != null && client.environment !== "" : true;
	const environment = "environment" in client ? client.environment : "test";
	let sdkOptions;
	if (hasEnvironment) {
		sdkOptions = {
			endpoint: "http://unused.invalid",
			apiKey: "unused",
			application,
			environment,
			publisher: new NullPublisher(),
			provider,
			eventLogger,
		};
	} else {
		// The real Client forbids an empty environment at construction, so build one
		// with a placeholder and report the environment as absent (how a wrapper
		// honours an omitted client.environment, and how the SDK's own tests mock it).
		const noEnvClient = new NoEnvironmentClient({
			endpoint: "http://unused.invalid",
			apiKey: "unused",
			application,
			environment: "placeholder",
		});
		sdkOptions = { client: noEnvClient, publisher: new NullPublisher(), provider, eventLogger };
	}
	const sdk = new absmartly.SDK(sdkOptions);
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
