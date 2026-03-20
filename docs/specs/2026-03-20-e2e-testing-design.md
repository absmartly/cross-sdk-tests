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

Managed via the ABsmartly CLI (`~/git_tree/absmartly-cli-ts-api-client`). All CLI commands use the `--profile e2e` flag.

1. **Create**: `abs experiments create --name "e2e-{run_id}" --application e2e-tests --unit-type session_id --variants "control,treatment" --profile e2e`
2. **Start**: `abs experiments start {id} --profile e2e`
3. Run tests (all SDKs send events)
4. Wait for metrics (poll with timeout)
5. Verify metrics via API
6. **Stop**: `abs experiments stop {id} --profile e2e`
7. **Archive**: `abs experiments archive {id} --profile e2e`

The CLI authenticates via an `e2e` profile: `abs auth login --profile e2e --api-key $KEY --endpoint $ENDPOINT`.

Note: CLI flags above are illustrative. The implementation must use the actual CLI flags as documented in `abs experiments create --help`.

## Eligible SDKs

Of the 20 wrappers, frontend-only SDKs (angular, react, vue2, vue3, liquid) run through the JavaScript SDK and may not support independent server-side context creation. The e2e runner should:
- Attempt all 20 SDKs
- Skip any wrapper that returns an error for e2e mode (with a warning, not a failure)
- Report which SDKs were tested vs skipped

## What Each SDK Does

For each eligible SDK, per test run:

1. Create 100 contexts with unit IDs `e2e-{run_id}-{sdk_name}-{0..99}`
2. Set attribute `sdk_name` = the SDK name (e.g., `"javascript"`, `"python"`)
3. Call `treatment()` on the experiment — generates exposures
4. For the first 50 units (0..49), call `track("purchase", {"amount": 10})` — generates goals with revenue
5. Call `publish()` to flush all events to the ABsmartly API

SDKs are run sequentially in batches of 5 to avoid overwhelming the test environment.

## Wrapper Changes

Wrappers need a new `e2e` mode in their `createContext` handler. The ABsmartly API credentials are injected as Docker environment variables (`ABSMARTLY_E2E_ENDPOINT`, `ABSMARTLY_E2E_API_KEY`, `ABSMARTLY_E2E_APPLICATION`, `ABSMARTLY_E2E_ENVIRONMENT`), NOT passed in the request body.

The orchestrator sends:

```json
{
  "mode": "e2e",
  "units": {"session_id": "e2e-run123-javascript-0"},
  "attributes": {"sdk_name": "javascript"}
}
```

The wrapper:
- Reads endpoint/apiKey/application/environment from its own environment variables
- Creates a real SDK instance with those credentials
- Uses the SDK's `DefaultContextPublisher` (real HTTP, not a mock)
- Sets the `sdk_name` attribute on the context
- Returns the context as usual

If a wrapper does not support e2e mode, it returns HTTP 501 and the orchestrator skips it.

## Verification

After all SDKs finish publishing, the orchestrator polls the ABsmartly API.

### Metrics API

The orchestrator fetches experiment results via `abs experiments get {id} --format json --profile e2e` which returns exposure and goal data. For per-SDK segmentation, it queries the API with the `sdk_name` attribute filter.

The exact API endpoint and query parameters for attribute-segmented metrics will be determined during implementation by inspecting the CLI's `experiments get` output and the ABsmartly REST API documentation.

### Per-SDK verification (segmented by `sdk_name` attribute):
- **Exposures**: 100 total per SDK (~50/50 split across variants, determined by hashing)
- **Goal conversions**: 50 per SDK (units 0..49 tracked `purchase`)
- **Revenue**: 500 per SDK (50 goals x $10)

### Aggregate verification (across all tested SDKs):
- **Total exposures**: N x 100 (where N = number of SDKs that ran)
- **Total goal conversions**: N x 50
- **Total revenue**: N x 500

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

```python
import time
import uuid

def generate_run_id():
    """8-char hex string for unique run identification."""
    return uuid.uuid4().hex[:8]

class E2ERunner:
    def __init__(self, sdks, config):
        self.sdks = sdks           # dict of sdk_name -> wrapper_url
        self.config = config       # profile, units_per_sdk, timeout
        self.experiment_id = None
        self.run_id = generate_run_id()
        self.profile = config.get("profile", "e2e")

    def run(self):
        try:
            self.create_experiment()
            self.start_experiment()
            self.run_sdk_scenarios()
            self.wait_for_metrics()
            results = self.verify_metrics()
            return results
        finally:
            self.cleanup_experiment()

    def create_experiment(self):
        # abs experiments create --name "e2e-{run_id}" --profile {profile}
        # Parse experiment ID from CLI output
        pass

    def start_experiment(self):
        # abs experiments start {id} --profile {profile}
        pass

    def run_sdk_scenarios(self):
        # For each SDK wrapper (batches of 5):
        #   POST /context (mode: e2e) x 100 units
        #   POST /treatment x 100
        #   POST /track x 50 (units 0..49)
        #   POST /publish x 100
        # Retries: up to 3 attempts per publish call on transient errors
        pass

    def wait_for_metrics(self):
        # Poll API until exposure count >= expected, or timeout
        # Default timeout: 60s, poll interval: 5s
        pass

    def verify_metrics(self):
        # Fetch experiment results from API
        # Compare per-SDK segments against expected values
        # Return pass/fail per SDK
        pass

    def cleanup_experiment(self):
        # abs experiments stop {id} --profile {profile}
        # abs experiments archive {id} --profile {profile}
        # Errors logged but do not fail the run
        pass
```

## Configuration

Credentials are passed as environment variables to Docker containers (via `docker-compose.yml`). They must NOT be committed to version control.

| Variable | Description | Default |
|----------|-------------|---------|
| `ABSMARTLY_E2E_ENDPOINT` | ABsmartly API URL | — (required) |
| `ABSMARTLY_E2E_API_KEY` | API key for test environment | — (required) |
| `ABSMARTLY_E2E_PROFILE` | CLI profile name | `e2e` |
| `ABSMARTLY_E2E_APPLICATION` | Application name | `e2e-tests` |
| `ABSMARTLY_E2E_ENVIRONMENT` | Environment name | `production` |
| `ABSMARTLY_E2E_UNITS` | Number of units per SDK | `100` |
| `ABSMARTLY_E2E_TIMEOUT` | Metrics poll timeout (seconds) | `60` |

The `e2e-config.env` file is gitignored. An `e2e-config.env.example` is provided with placeholder values.

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Experiment creation fails | Abort entire run with error |
| SDK wrapper returns 501 (no e2e support) | Skip with warning, continue |
| SDK fails to send events | That SDK shows FAIL, others continue |
| Publish call fails (transient) | Retry up to 3 times with backoff |
| Metrics don't arrive within timeout | Report expected vs actual, FAIL |
| Partial match (some metrics off) | Report which SDKs/metrics diverged |
| Cleanup fails | Log warning, don't fail the run |

## Orphan Cleanup

Experiments follow the naming convention `e2e-{8-char-hex}`. A cleanup command is provided:

```bash
./run-e2e-tests.sh --cleanup    # Archive all e2e-* experiments older than 1 hour
```

This can be run manually or scheduled in CI to prevent accumulation of stale experiments from interrupted runs.

## Output

```
┌─────────────────┬──────────────────────┬──────────────────────┬──────────────────────┐
│ SDK             │ Exposures            │ Goals                │ Revenue              │
├─────────────────┼──────────────────────┼──────────────────────┼──────────────────────┤
│ javascript      │ 100/100 PASS         │ 50/50 PASS           │ 500/500 PASS         │
│ python          │ 100/100 PASS         │ 50/50 PASS           │ 500/500 PASS         │
│ angular         │ SKIP (no e2e)        │ SKIP                 │ SKIP                 │
│ ...             │                      │                      │                      │
├─────────────────┼──────────────────────┼──────────────────────┼──────────────────────┤
│ TOTAL           │ 1500/1500 PASS       │ 750/750 PASS         │ 7500/7500 PASS       │
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

## Dependencies

### Python packages (add to orchestrator/requirements.txt):
- `requests` (already present for cross-SDK tests)

### External tools:
- ABsmartly CLI (`abs`) — must be installed and `e2e` profile configured
- Docker + Docker Compose — for running SDK wrappers

## Files to Create/Modify

### New files:
- `orchestrator/e2e_runner.py` — main e2e orchestration logic
- `run-e2e-tests.sh` — shell entrypoint
- `e2e-config.env.example` — example configuration with placeholder values
- `.gitignore` — ensure `e2e-config.env` is excluded

### Modified files:
- All 20 wrapper servers — add `mode: "e2e"` handling in `createContext`
- `run-all-tests.sh` — add `--e2e` flag
- `docker-compose.yml` — add e2e environment variable passthrough to wrapper containers
- `WRAPPER_API_SPEC.md` — document `mode: "e2e"` request format
