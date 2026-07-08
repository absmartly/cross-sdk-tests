# Cross-SDK Testing Infrastructure

Conformance testing for every ABsmartly SDK. Each SDK is wrapped in a small HTTP
service exposing a uniform API, and a single Python orchestrator drives all of
them through the same set of scenarios, asserting that they return identical
results and emit identical events. This is how we know the SDKs behave the same
across languages.

## What's here

- **21 SDK wrappers** — one HTTP service per SDK, each in a `<sdk>-wrapper/`
  directory:

  `javascript`, `typescript`, `react`, `angular`, `vue2`, `vue3`, `python`,
  `ruby`, `liquid`, `php`, `go`, `rust`, `java`, `kotlin`, `scala`, `swift`,
  `dart`, `flutter`, `dotnet`, `cpp`, `elixir`.

- **202 scenarios** in `test_scenarios_complete.json`. Each scenario is a
  `contextData` payload plus a list of `steps` (an action and its expected
  result/events). Scenarios with no executable steps are skipped by the
  orchestrator.

- **An orchestrator** (`orchestrator/test_runner.py`) that talks to each wrapper
  over HTTP and validates responses against the expected results baked into the
  scenarios (the JavaScript SDK is the canonical reference).

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

## Continuous integration

`.github/workflows/cross-sdk.yml` runs the full 202-scenario suite for all 21
SDKs on every pull request (and on demand), one independent matrix job per SDK.
Each job checks out this repo alongside the SDK source repo(s) its wrapper builds
from, builds just that wrapper plus the orchestrator, and drives the suite over
the compose network. The live-backend e2e suite is intentionally excluded from
CI — it needs secrets and the `abs` CLI and mutates real experiments.

A separate `cross-sdk-consistency` job brings up javascript, python, and go
together and runs the orchestrator once over all three, so the cross-SDK
assignment-consistency check (which needs at least two SDKs to compare
assignments) actually executes on every PR — the per-SDK matrix jobs run one SDK
each and never trigger it.

## Repository layout

```
cross-sdk-tests/
├── README.md                     # this file
├── WRAPPER_API_SPEC.md           # the HTTP API every wrapper implements
├── WRAPPER_REPOS.md              # notes on standalone wrapper/SDK repos
├── test_scenarios_complete.json  # 202 scenarios
├── generate_scenarios.py         # regenerates scenarios; output
│                                 #   test_scenarios_generated.json must be
│                                 #   diffed against the canonical file above
│                                 #   (the generated file is gitignored)
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
├── <sdk>-wrapper/                # one HTTP wrapper per SDK (21 total)
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
