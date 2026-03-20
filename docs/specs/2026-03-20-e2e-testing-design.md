# E2E Testing Design

End-to-end tests that verify each SDK sends correct data through the ABsmartly pipeline — from SDK event emission to API-queryable metrics.

## Problem

Unit and cross-SDK tests validate SDK behavior locally using mock endpoints. They don't verify that events actually reach ABsmartly, get processed, and produce correct metrics. An SDK could pass all local tests but silently fail to deliver data in production due to serialization issues, HTTP client bugs, or publisher misconfigurations.

## Solution

An orchestrator-driven e2e test that:
1. Creates a real experiment on a test ABsmartly environment
2. Has each SDK wrapper send real exposures and goals through the actual ABsmartly API
3. Polls the API for metrics
4. Verifies exposure counts, goal conversions, revenue, and per-SDK segmentation
5. Tears down the experiment

## Architecture

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  e2e_runner   │────▶│  SDK Wrappers    │────▶│  ABsmartly API   │
│  (Python)     │     │  (20 containers) │     │  (test-1 env)    │
│               │     └─────────────────┘     └──────────────────┘
│               │                                      │
│               │◀─────────────────────────────────────┘
│               │     (poll metrics, verify results)
│               │
│               │     ┌─────────────────┐
│               │────▶│  ABsmartly CLI   │
│               │     │  (abs commands)  │
└──────────────┘     └─────────────────┘
                      create/start/stop/archive experiment
```

## Experiment Lifecycle

Managed via the ABsmartly CLI (`~/git_tree/absmartly-cli-ts-api-client`):

1. **Create**: `abs experiments create --name "e2e-{timestamp}-{random}" --type test --variants "control,treatment" --profile e2e`
2. **Start**: `abs experiments start {id}`
3. Run tests (all SDKs send events)
4. Wait for metrics to propagate (5-10 seconds)
5. Verify metrics via API
6. **Stop**: `abs experiments stop {id}`
7. **Archive**: `abs experiments archive {id}`

The CLI authenticates via an `e2e` profile configured with `abs auth login --profile e2e`.

## What Each SDK Does

For each of the 20 SDKs, per test run:

1. Create 100 contexts with unit IDs `e2e-{run_id}-{sdk_name}-{0..99}`
2. Set attribute `sdk_name` = the SDK name (e.g., `"javascript"`, `"python"`)
3. Call `treatment()` on the experiment — generates exposures
4. For the first 50 units (0..49), call `track("purchase", {"amount": 10})` — generates goals with revenue
5. Call `publish()` to flush all events to the ABsmartly API

## Wrapper Changes

Wrappers need a new `e2e` mode in their `createContext` handler. When the orchestrator sends:

```json
{
  "mode": "e2e",
  "endpoint": "https://test-1.absmartly.com/v1",
  "apiKey": "...",
  "application": "e2e-tests",
  "environment": "production",
  "units": {"session_id": "e2e-run123-javascript-0"},
  "attributes": {"sdk_name": "javascript"}
}
```

The wrapper:
- Creates a real SDK instance with the provided endpoint and API key
- Uses the SDK's `DefaultContextPublisher` (real HTTP, not a mock)
- Sets the `sdk_name` attribute on the context
- Returns the context as usual

This is one new code path in each wrapper's `createContext` handler.

## Verification

After all SDKs finish publishing, the orchestrator:

### Per-SDK verification (segmented by `sdk_name` attribute):
- **Exposures**: 100 total per SDK (~50/50 split across variants, determined by hashing)
- **Goal conversions**: 50 per SDK (units 0..49 tracked `purchase`)
- **Revenue**: 500 per SDK (50 goals x $10)

### Aggregate verification (across all 20 SDKs):
- **Total exposures**: 2,000
- **Total goal conversions**: 1,000
- **Total revenue**: 10,000

### Tolerance
- Exposure counts must be exact (deterministic hashing)
- Goal counts must be exact
- Revenue must be exact (integer amounts, no floating point)

## Test Runner

New script: `run-e2e-tests.sh`

```bash
./run-e2e-tests.sh                           # All SDKs
./run-e2e-tests.sh --sdk javascript,python   # Specific SDKs
./run-e2e-tests.sh --units 100               # Custom unit count (default 100)
./run-e2e-tests.sh --profile e2e             # CLI profile for ABsmartly
```

Internally calls `python3 orchestrator/e2e_runner.py` which orchestrates the full flow.

## Orchestrator: e2e_runner.py

```
class E2ERunner:
    def __init__(self, sdks, config):
        self.sdks = sdks           # dict of sdk_name -> wrapper_url
        self.config = config       # endpoint, api_key, application, profile
        self.experiment_id = None
        self.run_id = generate_run_id()

    def run(self):
        self.create_experiment()
        self.start_experiment()
        self.run_sdk_scenarios()
        self.wait_for_metrics()
        results = self.verify_metrics()
        self.cleanup_experiment()
        return results

    def create_experiment(self):
        # abs experiments create --name "e2e-{run_id}" --profile e2e
        pass

    def start_experiment(self):
        # abs experiments start {id} --profile e2e
        pass

    def run_sdk_scenarios(self):
        # For each SDK wrapper in parallel:
        #   POST /context (mode: e2e) x 100 units
        #   POST /treatment x 100
        #   POST /track x 50 (units 0..49)
        #   POST /publish x 100
        pass

    def wait_for_metrics(self, timeout=30, poll_interval=2):
        # Poll API until exposure count matches expected, or timeout
        pass

    def verify_metrics(self):
        # Fetch experiment results from API
        # Compare per-SDK segments against expected values
        # Return pass/fail per SDK
        pass

    def cleanup_experiment(self):
        # abs experiments stop {id}
        # abs experiments archive {id}
        pass
```

## Configuration

Environment variables (or `.env` file):

| Variable | Description | Default |
|----------|-------------|---------|
| `ABSMARTLY_E2E_ENDPOINT` | ABsmartly API URL | — (required) |
| `ABSMARTLY_E2E_API_KEY` | API key for test environment | — (required) |
| `ABSMARTLY_E2E_PROFILE` | CLI profile name (alternative to endpoint+key) | `e2e` |
| `ABSMARTLY_E2E_APPLICATION` | Application name | `e2e-tests` |
| `ABSMARTLY_E2E_ENVIRONMENT` | Environment name | `production` |
| `ABSMARTLY_E2E_UNITS` | Number of units per SDK | `100` |
| `ABSMARTLY_E2E_TIMEOUT` | Metrics poll timeout (seconds) | `30` |

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Experiment creation fails | Abort entire run with error |
| SDK fails to send events | That SDK shows FAIL, others continue |
| Metrics don't arrive within timeout | Report expected vs actual, FAIL |
| Partial match (some metrics off) | Report which SDKs/metrics diverged |
| Cleanup fails | Log warning, don't fail the run |

## Output

Same table format as existing tests:

```
┌─────────────────┬──────────────────────┬──────────────────────┬──────────────────────┐
│ SDK             │ Exposures            │ Goals                │ Revenue              │
├─────────────────┼──────────────────────┼──────────────────────┼──────────────────────┤
│ javascript      │ 100/100 PASS         │ 50/50 PASS           │ 500/500 PASS         │
│ python          │ 100/100 PASS         │ 50/50 PASS           │ 500/500 PASS         │
│ ...             │                      │                      │                      │
├─────────────────┼──────────────────────┼──────────────────────┼──────────────────────┤
│ TOTAL           │ 2000/2000 PASS       │ 1000/1000 PASS       │ 10000/10000 PASS     │
└─────────────────┴──────────────────────┴──────────────────────┴──────────────────────┘
```

## Integration with run-all-tests.sh

Add `--e2e` flag to `run-all-tests.sh`:

```bash
./run-all-tests.sh              # unit + cross-SDK (existing)
./run-all-tests.sh --e2e        # unit + cross-SDK + e2e
./run-e2e-tests.sh              # e2e only
```

E2E tests run after unit and cross-SDK tests pass.

## Files to Create/Modify

### New files:
- `orchestrator/e2e_runner.py` — main e2e orchestration logic
- `run-e2e-tests.sh` — shell entrypoint
- `e2e-config.env.example` — example configuration

### Modified files:
- All 20 wrapper servers — add `mode: "e2e"` handling in `createContext`
- `run-all-tests.sh` — add `--e2e` flag
- `docker-compose.yml` — add e2e-specific environment variable passthrough
