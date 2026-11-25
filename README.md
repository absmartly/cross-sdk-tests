# Cross-SDK Testing Infrastructure

Conformance testing for every ABsmartly SDK. Each SDK is wrapped in a small HTTP
service exposing a uniform API, and a single Python orchestrator drives all of
them through the same set of scenarios, asserting that they return identical
results and emit identical events. This is how we know the SDKs behave the same
across languages.

## What's here

- **22 SDK wrappers** — one HTTP service per SDK lane, each in a `<sdk>-wrapper/`
  directory:

  `javascript`, `react`, `angular`, `vue2`, `vue3`, `python`,
  `ruby`, `liquid`, `php`, `go`, `rust`, `java`, `java-v1`, `kotlin`, `scala`, `swift`,
  `swift-v1`, `dart`, `flutter`, `dotnet`, `cpp`, `elixir`.

  `swift` wraps swift-sdk 2.x (`feat/swift-2.0` until it merges, then `main`); `swift-v1` wraps
  the 1.x maintenance line (pinned to a 1.x commit, swift-sdk#12's holdouts head, until
  `release/1.x` exists, built from a `swift-sdk-v1/` checkout) for its 12-month support window.

  `java` runs java-sdk 2.x and `java-v1` runs the 1.x maintenance line (the
  `java-sdk-v1` pin in `sdk-branches.sh`), so both majors keep passing the
  contract while 1.x is supported.

- **347 scenarios** in `test_scenarios_complete.json`. Each scenario is a
  `contextData` payload plus a list of `steps` (an action and its expected
  result/events). Scenarios with no executable steps are skipped by the
  orchestrator.

  Some scenarios only apply to SDKs that support a given feature. These carry
  a `requires: ["<capability>"]` field. Which capabilities each SDK must
  support is pinned in the checked-in policy
  [`orchestrator/expected-capabilities.json`](orchestrator/expected-capabilities.json):
  per SDK, a capability is either **required** (the wrapper's `/capabilities`
  must report `true`, otherwise the run fails and the scenarios still run) or a
  reviewed **unsupported** exception with a one-line reason (its scenarios are
  SKIPped and counted separately in the summary). An SDK missing from the
  policy, an unclassified capability, a malformed `/capabilities` body, or a
  `requires` name the policy does not declare all fail closed. A wrapper that
  starts advertising an excepted capability also fails the run until the
  policy moves it to `required`. Scenarios 203-208 require the `holdouts`
  capability. The SDKs that must support it are listed under `required` in
  `orchestrator/expected-capabilities.json`; their wrappers report it from a
  behavioral self-test, so a failing self-test fails the lane. The other SDKs
  list it as a reviewed exception. Scenario
  209 (holdout wire-format inertness) is deliberately
  ungated: it must pass on every SDK, holdout-aware or not. Scenarios 214-220
  require the `holdout_arms` capability and are skipped by SDKs that have not
  yet implemented three-arm holdout logic. Scenario 221 requires `holdouts`, and
  scenario 222 requires `holdout_arms`: it repeats the three-arm variant 0 case
  with `holdoutType` omitted, pinning that arity comes from the holdout's own
  split length rather than a type string on the wire. Scenarios 223-227 require
  `holdouts` and pin holdout behaviour over the life of a context: exposure
  state survives publish (223), refresh keeps it unless the holdout iteration
  changes (224), a refresh that changes `holdoutIds` re-resolves the experiment
  (225), `variableValue` on a suppressed experiment returns control (226), and
  a holdout resolves with the covered experiment's unit type (227). Scenario
  228 (`publishFail`) checks that a failed final publish surfaces the error,
  logs an `error` event, keeps the events queued and the context open, and that
  a second `finalize()` publishes them; scenario 229 (`readyError`) checks that
  `refresh()` on a context whose load failed is a successful no-op (no fetch,
  no event, still failed).

  Scenarios 400-420 and 422-426 (Switchback) require the `switchback`
  capability and scenario 421 (switchback wire-format inertness) is ungated, so
  it runs on every SDK. They test the
  R33 cluster-defined switchback contract: an experiment names, in an ordered
  `switchbackClusters` map, the unit types it is linked to and the attribute
  path holding each one's cluster value, and the first unit in declared order
  with a value serves the raw value as the cluster key of
  `assignSwitchback(key, floorDiv(now - switchbackStartAt, switchbackPeriodMs),
  seed, arms)`. The Collector serves it (absmartly/abs#5801) and java-sdk
  implements it (absmartly/java-sdk#15, `SWITCHBACK.md`). The SDKs required to
  support it are listed under `required` in `orchestrator/expected-capabilities.json`;
  each of their wrappers (java aside) reports it from a behavioral self-test, and
  every other SDK lists `switchback` as a reviewed exception. Scenario 426 (an
  attribute of `3.0` gives no value) also requires `integralFloatAttributes`:
  JavaScript parses `3.0` as `3`, so the JavaScript lanes list it as a reviewed
  exception (owner decision 2026-11-17).
  The group owns 400+ (`SWITCHBACK_FIRST_NUMBER` in `scenario_groups.py`), after
  the feature-parity range.

  | Rule | Scenarios |
  | --- | --- |
  | golden vectors via `POST /switchback/assign` and `/permutation` (882 + 504) | 400-405 |
  | single source, raw value is the key, period 0 and 1; an eligible draw of 0 is served and exposed (`assigned=true`, `eligible=true`) | 406 |
  | lower-declared source serves whatever the context unit order | 407 |
  | agreeing units serve | 408 |
  | disagreeing units: control, no exposure | 409 |
  | a unit without a value is skipped | 410 |
  | strings and whole numbers (decimal text) only; fractions, booleans, objects, arrays, `""`, null give no value | 411 |
  | every `attribute-path-cases.json` case; `.` in any segment, misplaced indices, unterminated quotes are malformed | 412 |
  | `switchbackClusterValues`: in, out, null, `[]` | 413 |
  | pre-anchor, unaligned anchor, no anchor, zero period, one arm: silent control | 414 |
  | traffic miss on an eligible cluster recorded `eligible=false`; traffic hashes the serving unit | 415 |
  | override (also without a cluster value), assignment rule, strict audience (`assigned=false`), full-on, custom over the draw; full-on, assignment rules and strict audiences never on an ineligible cluster; traffic miss before custom | 416 |
  | holdouts never apply | 417 |
  | missing or empty cluster map: legacy control; no linked unit: not applicable; Phase 1E fields ignored | 418 |
  | refresh with a changed cluster map re-resolves | 419 |
  | one exposure per period; payload has no switchback fields | 420 |
  | ungated: unit experiments with switchback fields are served as unit experiments | 421 |
  | serving unit and cluster key read once per period slot (eligible and ineligible) | 422 |
  | `peek`, `peekVariableValue`, `variableValue` resolve the cluster draw; only exposing calls expose | 423 |
  | a switchback with a covering holdout and an assign rule: the rule wins (`ruleOverride=true`), the holdout is never consulted and records no exposure | 424 |
  | three sources declared in non-alphabetical JSON order: the declared order serves, not a sorted or unordered map | 425 |
  | an integral float (`3.0`) gives no value (also `integralFloatAttributes`) | 426 |

  Grids keep "now" inside one known period until 2069, so no clock control is
  needed. The group is generated by `generate_switchback_scenarios.py` from
  `fixtures/switchback/golden-vectors.json` and `attribute-path-cases.json`
  (byte-identical platform copies); its expectations come from an in-generator
  model of the R33 rules, and generation fails unless every wrong model
  (hashed key, uid as key, Phase 1E bucket, last unit serves, units iterated
  outer, a sorted cluster map, a holdout applied or fired, period and seed
  arithmetic, ...) fails the scenario of the rule it breaks
  (`MUTANT_SCENARIOS`). Multi-unit publishes pin every hashed unit
  with `unorderedUnits: true` (matched as a set, since unit order is the
  SDK's map order). Edit the generator, not the
  scenarios. Run `python3 generate_switchback_scenarios.py --rules` to print
  this map and the mutant results.

  The java lane pins `java-sdk` to `next/major` (java-sdk 2.0) in
  `sdk-branches.sh`, which carries switchback (java-sdk#15) and the
  feature-parity round (java-sdk#22). The java-v1 lane pins the 1.x line to
  `v1.9.0`, which predates switchback, so it lists switchback as unsupported
  until `release/1.x` is cut from main.
  The go lane pins `go-sdk` to its switchback branch (`feat/switchbacks`) until
  it merges. The go wrapper decodes attribute values with `UseNumber`, so a
  JSON integer reaches the SDK as an integer and `3.0` as a float (scenario
  411), and advertises `switchback` from a behavioural self-test
  (`go-wrapper/switchback.go`) that fails closed on a sorted cluster map.

  Scenarios 501-507 (Holdout Conformance, H2-H8) add the holdout cases every
  SDK holdout port must agree on, beyond 203-227 and 305. They require
  `holdouts`. Their expectations are
  recorded from the javascript-sdk reference by
  `generate_holdout_conformance_scenarios.py`, and
  `orchestrator/tests/test_holdout_conformance_scenarios.py` replays them with
  an independent model and checks that each wrong implementation below fails
  its scenario.

  | Rule | Scenario | Mutant it catches |
  | --- | --- | --- |
  | a holdout with an empty or missing `split` is ignored | 501 | splitless holdout applies |
  | a same-iteration seed or split edit neither re-randomizes nor re-exposes | 502 | arm redrawn (for an experiment first evaluated after the edit too), or covered experiments re-resolved, on a seedHi or split edit |
  | `peek` queues no holdout exposure | 503 | peek fires holdouts |
  | unknown fields in a holdout entry, and experiment-only fields (traffic, audience, full-on), are ignored | 504 | full-on applied to a holdout |
  | one holdout over two unit types: two exposures, one arm per unit type | 505 | arm cached per holdout id |
  | a strict audience mismatch on a covered, not-held-out experiment still fires the holdout | 506 | mismatch skips holdouts |
  | shared variable key: a suppressed first candidate loses to an assigned one, and both holdouts fire | 507 | suppressed wins; only the winner fires |

  507's `variableValue` queues three exposures whose order differs between the
  reference SDKs (javascript-sdk queues each candidate's own exposure before its
  holdouts, java-sdk every visited candidate's holdouts first); the order is not
  part of the contract, so that step sets `unorderedEvents: true` and the
  harness matches its events one-to-one in any order.

  The plan's H1 (a repeated `holdoutIds` entry) is not a contract case: the
  backend never sends a holdout id twice, so no scenario pins it and 500 is
  left unused.

  Scenarios 230-253 pin the
  2026-11 SDK round's reference-aligned behaviours (see
  [docs/sdk-round-2026-11-conformance.md](docs/sdk-round-2026-11-conformance.md)):
  230-239 (`requires: assignmentRules`) cover custom assignment rules — a
  matching rule wins with a `ruleOverride:true`/`assigned:false` exposure, is
  beaten only by an override, takes precedence over audience targeting and
  custom assignments, serves variable values, out-of-range rule variants fall
  through without trying later rules, and a rule variant for an experiment
  removed on refresh is rebuilt rather than served stale (javascript-sdk#67).
  240-245 (`requires: semverOperators`) cover the `semver_*` audience operators
  (numeric, not lexicographic, comparison). 246-247 (ungated) pin audience
  re-evaluation after `setAttribute`: a flipped strict audience reassigns the
  unit and queues a fresh exposure. 248-253 (the §5 system-attributes group;
  248-252 ungated, 253 `requires: clientNoEnvironment`) pin that every SDK
  always evaluates audiences and rule conditions with the client's configured
  `application` and `environment` names (no opt-in), user attributes overlay
  the system values, an unconfigured name is left out of the map (the
  no-environment case, 253), and a variable read goes through the matched
  variant too. The SDKs also drop `app_version` from evaluation; these
  scenarios do not exercise that, the SDKs' own unit tests do. The scenarios set
  the client application/environment through the optional `client` field on
  `POST /context` (see [WRAPPER_API_SPEC.md](WRAPPER_API_SPEC.md)). With the
  parity pins every SDK requires the rules and semver capabilities, the
  ungated scenarios (246-252) run on every SDK, and 253 runs on the SDKs that
  advertise `clientNoEnvironment`.

  Scenarios 300-332 pin the 2026-11 feature-parity round, where every SDK
  ports assignment rules, the `semver_*` operators and a versioned `X-Agent`
  (see [docs/feature-parity-conformance.md](docs/feature-parity-conformance.md)):
  300-314 (`requires: assignmentRules`) cover split rules hashed with the
  experiment seed, environment scoping, first match, precedence over holdouts,
  traffic and full-on, the missing-unit split, malformed/wrong-count splits and
  the percentage tolerance; 315-319 (`requires: semverOperators`) cover each
  operator, pre-release ordering and invalid versions; 320-321
  (`requires: xAgentHeader`) check the SDK's own context fetch and publish
  requests carry `absmartly-<lang>-sdk/<version>`; 322-323 check a wrapper
  forwards an explicitly blank `client.application` instead of repairing it
  (`clientRejectsBlankApplication` / `clientAcceptsBlankApplication`, one per
  SDK); 324-326 (`requires: assignmentRules`) check rule conditions read the
  configured application/environment, with a user-set value winning, while a
  rule's environment scope always uses the configured environment; 327-330 check
  split-percentage parsing, unknown or malformed rules, rule changes on refresh
  and invalid rules JSON; 331-332 check semver operand normalisation
  (332 also `requires: semverNumericOperands`). With the parity pins in `sdk-branches.sh` every SDK requires the rules
  and semver capabilities.

  Scenarios 270-274 (generated by `generate_context_compression_scenarios.py`)
  cover HTTP compression of the SDK's own `GET {endpoint}/context`. Their payload
  is served by the negotiating context server (`context-server/`, a compose
  service), not by the wrapper, so the response encoding is decided and the
  request's `Accept-Encoding` recorded in one place. 270 is ungated: the server
  answers gzip only when the request advertised gzip, identity otherwise, and
  every SDK must load the context and match the direct-data twin 271 exactly,
  then refresh through its own fetch and serve the changed data. 272 requires
  `contextFetchCompression`, a capability the orchestrator **measures** on the
  context server instead of reading it from the wrapper. 273-274 repeat this
  with a payload that allows br > gzip > identity (270-272 never serve br): 273
  is ungated (whatever the SDK negotiates, it must match the twin 271 and use
  the refreshed data), 274 requires the measured `contextFetchBrotli` (br
  advertised on create and refresh, br served and decoded). How they are
  measured and gated, and what each SDK sends and decodes (with a Brotli survey):
  [docs/context-fetch-compression.md](docs/context-fetch-compression.md).
  Scenarios 340-368 (Shared Variable Key, `requires: sharedVariableKeys`) pin
  how `variableValue` and `peekVariableValue` pick an experiment when several
  define the same key, as java-sdk's `Context.getVariableAssignment`: candidates
  in ascending experiment id (not payload order); the first assigned, overridden
  or rule-won candidate wins even if its variant lacks the key (the default is
  returned, later candidates are not tried); otherwise the first
  holdout-suppressed candidate serves control; otherwise the default, with no
  exposure. A candidate that is not assigned (strict audience mismatch, no unit,
  a custom assignment on a strict mismatch, a switchback with no linked unit)
  never leaks its control value; a traffic-excluded or non-strict mismatched
  unit is assigned and does win. The group also covers overrides to control,
  full-on, holdouts (also `holdouts`), switchbacks (also `switchback`), reads
  agreeing with `treatment`/`variableKeys`, re-evaluation after `setAttribute`,
  a refresh removing the winner and a JSON `null` value. Expectations are
  recorded from the java-sdk wrapper by `generate_shared_variable_key_scenarios.py`
  and replayed in CI against a Python model and 19 wrong models
  (`orchestrator/tests/test_shared_variable_key_scenarios.py`; `--replay` prints
  the per-mutant kill counts locally).
  java, java-v1, kotlin, dart, flutter and dotnet require the capability (dotnet from
  dotnet-sdk 1.4.1, [dotnet-sdk#17](https://github.com/absmartly/dotnet-sdk/pull/17),
  merged, which its pinned `feat/switchbacks` branch contains), and so do
  javascript, react, vue2, vue3 and angular, which build javascript-sdk
  [#69](https://github.com/absmartly/javascript-sdk/pull/69) pinned at 2aa9a2d
  and report it from a behavioral self-test (`sharedVariableKeysSelfTest.js`).
  Every other SDK lists a reviewed exception naming its divergence.

- **An orchestrator** (`orchestrator/test_runner.py`) that talks to each wrapper
  over HTTP and validates responses against the expected results baked into the
  scenarios (the JavaScript SDK is the canonical reference).

  Switchback harness tests (`sameShapeAs`, capability gating, the switchback
  generator's `--check`, a replay of every switchback context scenario against
  mutant SDK models, and a digest of the 279 non-switchback scenarios) run in
  a few seconds with `python3 -m pytest orchestrator/tests -q` (needs `pytest`
  and `pip install -r orchestrator/requirements-test.txt`, which adds the
  test-only `brotli` pin); CI runs them in `harness-unit-tests` next to `tests/`.
  The java wrapper's switchback endpoint tests run inside the wrapper's
  `builder` image stage with `gradle test`; the rust wrapper's self-test unit
  tests run with `cargo test` in `rust-wrapper/`.

- **A live-backend e2e runner** (`orchestrator/e2e_runner.py`) that drives the
  wrappers against a real ABsmartly environment and verifies data arrives.

The wrapper API is specified in [WRAPPER_API_SPEC.md](WRAPPER_API_SPEC.md).

## Requirements

- Docker and Docker Compose
- Python 3 with the `requests` package (the scripts install it if missing)
- The SDK source repositories checked out as siblings of this directory. The
  wrapper Dockerfiles use build context `..` and `COPY` sibling `<name>-sdk/`
  directories, so e.g. `../javascript-sdk`, `../python3-sdk`, `../go-sdk` must
  exist next to `cross-sdk-tests/`. See `setup-sdks.sh` and
  [WRAPPER_REPOS.md](WRAPPER_REPOS.md).

## Quickstart

All commands run from this directory. The scripts build the needed Docker
images, start the containers, drive the orchestrator, and tear down.

```bash
# Build and test every SDK
./run-tests.sh

# Test one or more SDKs (comma-separated)
./run-tests.sh --sdk react,vue2

# Skip the (slow) image build and just run
./run-tests.sh --skip-build

# Only build images, don't run
./run-tests.sh --build-only
```

`run-tests.sh` is the everyday entry point (filtered/local runs). Two more
scripts cover wider matrices:

- **`./run-all-tests.sh`** — the full matrix: unit tests, cross-SDK tests, and
  optionally e2e, with `--sdk`, `--exclude`, `--unit-only`, `--cross-only`, and
  `--e2e` filters.
- **`./verify_sdk.sh <sdk> [<sdk2> ...]`** — verify a single SDK by bringing its
  container up and driving the orchestrator directly. Use this to confirm a
  result: it avoids a port-resolution race that can make filtered `run-tests.sh`
  runs report stale numbers.

Results are written to `test-results/report.json`.

### Running the orchestrator directly

`test_runner.py` requires the `SDK_SERVICES` environment variable — a
comma-separated list of SDK names. It resolves each to `http://<name>-sdk:3000`
on the compose network, so it is meant to run inside the orchestrator container:

```bash
docker compose up -d javascript-sdk
docker compose run --rm --no-deps \
  -e SDK_SERVICES=javascript \
  orchestrator python3 test_runner.py
```

`test_runner.py` matches error strings strictly by default (CI runs it this way).
Pass `--loose-error-match` to relax error-message comparison when iterating
locally on a wrapper whose error text does not yet match.

Validation is strict by default:

- An absent `result` or `events` key never matches an expected `null` / `[]`.
- A step that lists no `events` expects none, so any emitted event fails it.
- JSON types are distinct (`true` is not `1`, `null` is not `"null"`), but
  `1 == 1.0`. Lists are ordered, except the results of `variableKeys`,
  `customFieldKeys`, `globalCustomFieldKeys`, `experiments` and the values of
  `variableKeysMap`, which are compared as multisets (duplicates still count).
- Object results are data and must have exactly the expected keys, at every
  depth. Only the status records returned by `createContext`,
  `createContextWith`, `createContextFailed` and `readyError` are compared in
  part, because their contextId and error message differ per SDK.
- Expected event data stays partial: only the keys a scenario lists are
  compared (nested objects likewise by their listed keys; arrays must match
  in length). Keys it leaves out (timestamps such as `exposedAt` and
  `publishedAt`, SDK-specific fields) are not compared. A listed key is never
  skipped, so a scenario that lists the published `units` (as the Switchback
  group does) checks their hashed uids.

With 2+ SDKs the run also compares what the SDKs actually returned, step by
step: the asserted result fields, the event types, and the canonical exposure
fields. Volatile fields are ignored. Any SDK that differs from the majority
fails, and a multi-SDK run that made zero comparisons fails as `UNCHECKED`
instead of reporting agreement.

Every scenario runs against every wrapper by default. `--wrapper-skip` (also
on `run-tests.sh`) opts in to the old fast mode that skips scenarios a
wrapper declares as pure pass-through to another SDK under test.
`--no-wrapper-skip` is kept as a no-op for compatibility.

The orchestrator's own unit tests need no Docker. They need the harness test
requirements, as CI's `harness` job installs them (`brotli` is test-only: the
orchestrator image and the consistency job install `requirements.txt` alone):

```bash
pip install -r orchestrator/requirements-test.txt
python3 -m unittest discover -s tests -v
```

## E2E mode (live collector)

The e2e suite creates a real experiment on a test ABsmartly environment, has
each SDK send real exposures and goals, polls the metrics API, and verifies the
counts. It is separate from the cross-SDK suite and mutates real experiments, so
it needs credentials and the `abs` CLI.

1. Copy the example config and fill in real values:

   ```bash
   cp e2e-config.env.example e2e-config.env
   ```

2. Set `ABSMARTLY_E2E_ENDPOINT` to the **collector** endpoint. This is a
   `*.absmartly.io` host (e.g. `https://test-1.absmartly.io/v1`), **not**
   `*.absmartly.com` — the `.com` host is the management API and will not accept
   collector traffic. Fill in `ABSMARTLY_E2E_API_KEY` and adjust the application/
   environment/unit count as needed.

3. Run:

   ```bash
   ./run-e2e-tests.sh                          # all SDKs
   ./run-e2e-tests.sh --sdk javascript,python  # specific SDKs
   ./run-e2e-tests.sh --cleanup                # archive stale e2e experiments
   ```

Wrappers enter this path when a `POST /context` request sets `mode: "e2e"`; they
read credentials from their environment, not the request body. See the E2E Mode
section of [WRAPPER_API_SPEC.md](WRAPPER_API_SPEC.md).

Each SDK is verified on its own segment of the metric results (the `sdk_name`
attribute, sent as a `{"filter": {"and": [{"eq": ...}]}}` audience expression):
exposures are the sum of the variants' `unit_count` and goals the sum of their
`count`. Revenue is measured, not computed from goals: the runner keeps a
`goal_property` metric `e2e_purchase_revenue` on the `purchase` goal (value
source property `amount`) and queries it with `--metric <id>` (per SDK and
unfiltered) without attaching it to the experiment. Actual revenue is the sum
of the variants' `count`, compared with the planned amount (planned goals x 10).
An SDK whose exposures, goals or revenue the backend can't measure is a failure
(`unverified: <reason>` / `unverified: revenue <reason>`), not a skip: the run
prints `PASS All metrics verified` and exits 0 only when every non-skipped SDK
row was measured and passed. An existing `e2e_purchase_revenue` is read with `abs metrics get <id> --raw` and
refused unless it is a goal_property metric on the `amount` property (and,
when the payload names the goal, on `purchase`); one left inactive by a failed
run (looked up with `--include-drafts --archived`) is unarchived if needed and
reviewed and activated again, resuming after any review step that run already
completed (per the metric's `review.status`). Readiness polling stops waiting on an SDK whose count is already
past the over-publish bound, since it can no longer pass. An SDK named with `--sdk` must be verified: if it
is skipped (its wrapper returns 501 for e2e mode) or unknown, the run fails.
Only SDKs that were not requested (the default "all") may be skipped, and the
report lists them; a run where every SDK is skipped fails. `--dry-run` prints `DRY-RUN` and measures nothing. The design is in
[docs/specs/2026-03-20-e2e-testing-design.md](docs/specs/2026-03-20-e2e-testing-design.md).

## Continuous integration

`.github/workflows/cross-sdk.yml` runs the full 347-scenario suite for all 22
SDK lanes on every pull request (and on demand), one independent matrix job per SDK.
Each job checks out this repo alongside the SDK source repo(s) its wrapper builds
from, builds just that wrapper plus the orchestrator, and drives the suite over
the compose network. The live-backend e2e suite is intentionally excluded from
CI — it needs secrets and the `abs` CLI and mutates real experiments.

Each lane uploads its report, which records every scenario outcome and what
the SDK returned at each step. A separate `cross-sdk-consistency` job then
downloads the reports from all 22 lanes and compares the SDKs with each other
(`test_runner.py --compare-reports`), because a lane runs only one SDK and has
nothing to compare against. That job runs even when some lanes fail. It fails
if a report is missing or unreadable, if fewer than two SDKs or zero
comparisons were checked, or if any SDK disagrees with the others. Both Docker
jobs depend on a fast `harness-unit-tests` job, which runs `tests/` and
`orchestrator/tests/` without Docker and checks that the generated scenario
groups are up to date. Each lane also starts the `context-server` service.

## Repository layout

```
cross-sdk-tests/
├── README.md                     # this file
├── WRAPPER_API_SPEC.md           # the HTTP API every wrapper implements
├── WRAPPER_REPOS.md              # notes on standalone wrapper/SDK repos
├── test_scenarios_complete.json  # 347 scenarios
├── generate_shared_variable_key_scenarios.py  # records the Shared Variable
│                                 #   Key group (340-368) from java-sdk;
│                                 #   --replay checks it without Docker
├── generate_switchback_scenarios.py  # regenerates the Switchback group
│                                 #   (400-426) in place; --check verifies
├── generate_holdout_conformance_scenarios.py  # regenerates the Holdout
│                                 #   Conformance group (501-507); --check
├── fixtures/switchback/          # platform golden vectors (verbatim copy)
├── generate_scenarios.py         # STALE, non-canonical generator; output
│                                 #   test_scenarios_generated.json must be
│                                 #   diffed against the canonical file above
│                                 #   (the generated file is gitignored)
├── generate_context_compression_scenarios.py
│                                 # generates scenarios 270-274 (--check in CI)
├── context-server/               # negotiating GET /context server (compose service)
├── run-tests.sh                  # everyday entry point (filtered/local)
├── run-all-tests.sh              # full matrix (unit + cross-SDK + e2e)
├── run-e2e-tests.sh              # live-collector e2e (needs e2e-config.env)
├── verify_sdk.sh                 # single-SDK verification via orchestrator
├── setup-sdks.sh                 # clone/link sibling SDK source repos
├── e2e-config.env.example        # template for e2e credentials
├── docker-compose.yml            # wrapper + orchestrator service definitions
├── orchestrator/
│   ├── test_runner.py            # cross-SDK orchestrator
│   ├── e2e_runner.py             # live-backend e2e orchestrator
│   └── results_aggregator.py     # merges per-SDK reports into report.json
├── <sdk>-wrapper/                # one HTTP wrapper per SDK lane (21 total)
├── docs/                         # historical design docs (see banners)
└── test-results/                 # report.json output (generated)
```

## Scenario format

```json
{
  "name": "04 - Treatment - Queue Exposure",
  "description": "treatment() should queue an exposure event",
  "contextData": {
    "experiments": [
      { "id": 1, "name": "exp_test", "iteration": 1, "unitType": "session_id", "seedHi": 0, "seedLo": 0, "split": [0.5, 0.5], "trafficSeedHi": 0, "trafficSeedLo": 0, "trafficSplit": [0, 1], "fullOnVariant": 0, "applications": [], "variants": [{ "config": null }, { "config": null }], "audienceStrict": false, "audience": null }
    ]
  },
  "steps": [
    {
      "action": "createContext",
      "params": { "units": { "session_id": "test123" }, "options": { "publishDelay": -1 } },
      "expect": { "result": { "ready": true, "failed": false }, "events": [{ "type": "ready" }] }
    },
    {
      "action": "treatment",
      "params": { "experimentName": "exp_test" },
      "expect": { "result": 1, "events": [{ "type": "exposure" }] }
    }
  ]
}
```

Each SDK must return the exact result values and emit the expected event types
and data. Cache-invalidation scenarios are a particular focus: the cache must be
cleared (producing a new exposure) when an experiment changes, and retained (no
new exposure) when nothing changes.
