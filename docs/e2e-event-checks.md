# Collector-arrival e2e checks

These checks assert SDK events arrive, not analysis eligibility/dedup/audience semantics.
They extend the baseline e2e run (per-SDK exposures, goals and measured revenue on the
`sdk_name` segment, from cross-sdk-tests#52) without touching it: case units carry the
attributes `e2e_sdk` + `e2e_case` instead of `sdk_name`, so they never enter the baseline
segment. They also run in their own run-scoped experiments, `e2e-<run>-cases` (user_id) and
`e2e-<run>-session` (session_id), so they add nothing to the baseline experiment's numbers, and
both are archived in cleanup. Every case query pins a metric ID and the `e2e_sdk` + `e2e_case` segment. Missing,
malformed, non-finite or absent measurements fail; no revenue is derived from count. A failing
case is listed with the baseline problems and gates the PASS banner and the exit code.
Comparisons use an absolute 1e-9 allowance for arithmetic noise, never rounding to integer.

## Cases

- `close`: track, then SDK finalize/close with no explicit publish.
- `pre_ready`: a gated SDK provider keeps data unresolved while tracking, then fetches
  live data and waits for readiness. A prior context establishes the same unit's exposure
  because goal metrics count achievements after the first exposure. Raw collector properties
  are checked separately. Supported by JavaScript and Python; other wrappers explicitly
  declare the missing hook under each SDK's `e2e.unsupported` policy.
- `repeated`: seven tracks from one unit, measured by **goal_count**, not unique count.
- `batch`: 300 tracks before one publish.
- `unicode`, `long_id`, `numeric_id`: one exposure, matched to the independently computed
  UTF-8 MD5/base64url unit hash in raw backend events. Numeric IDs use existing capability
  exceptions for PHP/.NET, never silently coerce or skip.
- `multiple_units`: one context with user_id + session_id, two real experiments (one for
  each unit type), independently measured participation and raw identity checks.
- Numeric integer, 10.5, 0.1, negative, zero, large value: each separately plus their total,
  measured using goal_property `purchase.amount`.
- Strings EUR/USD: goal_count filtered on EUR, plus intact raw properties.
- Nested order.total: goal_property path is `order/total` (backend slash path grammar).
- Booleans: separate true and false property-filtered goal_count metrics.
- Missing properties, null properties, null amount: goal_count increases, amount sum zero.
  `null_value` is a reviewed exception for python (`e2e.unsupported.nullPropertyValues`):
  python3-sdk serializes with `strip_nulls=True` and sends `{}`; that is an SDK release gate.

Polling waits for baseline participants plus one sentinel query per SDK (the batch case's
goal count); the full case check runs once afterwards. A CLI call
is capped at 30s. An HTTP 429 (CLI text `Rate limit exceeded.`) sleeps 120s and retries only
while that sleep ends before the deadline; every poll call, including the final fetch, carries
the deadline, so a poll overruns `--timeout` by at most one in-flight call. The case check that
follows gets its own `--timeout` budget the same way. Each case's wrapper calls get `--timeout` too,
capped by one sending budget for the whole run (`--timeout` per SDK); no call starts after it,
and a 429 wait never ends past it.
Raw property comparison is by numeric value (10 equals 10.0); agents must carry a version
unless the SDK's `xAgentHeader` policy is a reviewed exception.

The raw events oracle additionally verifies unit type, unit hash, application/environment,
SDK agent, exposure attributes, and exact multiset of goal properties; no-properties/null
are normalized to an empty property map. Wrapper response events are never the arrival oracle.

## Backend confirmation and live execution

The worker cannot resolve test-1 or access collector/management credentials. CLI 1.18.0 was
installed with `npm install @absmartly/cli@1.18.0` in `/tmp/abscli`.
Coordinator probes on test-1 confirmed `events list` returns collector rows with hashed
`unit_uid`, `unit_type`, `agent`, app/environment, exposure attributes, goal properties and
pagination. An independently computed MD5 of `e2e-0c11789c-go-94` matched the backend UID
`OSS6pz0j4iJ-ymv9M3DHvg`. Raw and formatted event queries agreed.

Metric type/flag syntax is confirmed by CLI help; property-filter envelope and slash paths
are confirmed by backend sources (`office/backend/src/lib/engine.js`, metric route validation,
`office/shared/lib/src/validation/attributePath.ts`). Live metric create/query probes have
been requested but are not yet confirmed. **No live pass is claimed.**

Coordinator command (with authenticated `test-1` management profile and collector credentials
in `e2e-config.env`, environment `prod`):

```sh
npm install --prefix /tmp/abscli @absmartly/cli@1.18.0
export PATH="/tmp/abscli/node_modules/.bin:$PATH"
./run-e2e-tests.sh --sdk javascript,python --profile test-1 --units 10 --timeout 480 --verbose
```

The runner finds metrics by name, otherwise creates them, requests/approves review, activates
and validates type/goal/path/filter/outlier settings. Names are stable; experiments and unit
IDs are run-scoped. Both experiments are stopped/archived in finally. No analysis override,
rule, deduplication or audience checks are included.

Local tests:

```sh
python -m pytest orchestrator/tests -q
python -m unittest discover -s tests -q
node --test javascript-wrapper/preReadyGoals.test.js
# With python3-sdk and python-wrapper/requirements.txt installed:
python -m pytest python-wrapper/test_pre_ready_goals.py -q
```

Mutation tests change each case's backend count/value or remove its measurement and assert
FAIL; wrapper call-order tests forbid explicit publish in close and require exactly one
publish for the batch.
