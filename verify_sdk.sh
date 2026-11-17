#!/bin/bash
# Verify a single SDK against the full cross-SDK suite WITHOUT the run-tests.sh
# port race. Brings the container up, waits for the published port, then drives
# the orchestrator directly. Usage: ./verify_sdk.sh <sdk> [<sdk2> ...]
set -e
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-csdk}"

# Rebuild requested SDK images first (under THIS project name) so we never test
# a stale image. Pass NO_BUILD=1 to skip.
if [ "${NO_BUILD:-0}" != "1" ]; then
  for sdk in "$@"; do
    echo "=== building ${sdk}-sdk ==="
    docker compose build "${sdk}-sdk" >/dev/null 2>&1 || { echo "build failed for ${sdk}-sdk"; exit 1; }
  done
fi

# published_port <service> <container port>: the host port, polled for 30s.
published_port() {
  local port=""
  for i in $(seq 1 30); do
    port=$(docker compose port "$1" "$2" 2>/dev/null | awk -F: '{print $NF}')
    [ -n "$port" ] && break
    sleep 1
  done
  if [ -z "$port" ]; then echo "ERROR: no port for $1" >&2; return 1; fi
  echo "$port"
}

docker compose build context-server >/dev/null 2>&1 || { echo "build failed for context-server"; exit 1; }
docker compose up -d --force-recreate context-server >/dev/null 2>&1 || { echo "could not start context-server"; exit 1; }
CS_PORT=$(published_port context-server 8080) || exit 1
export CONTEXT_SERVER_URL="http://localhost:${CS_PORT}" CONTEXT_SERVER_SDK_URL="http://context-server:8080"

for sdk in "$@"; do
  echo "=== bringing up ${sdk}-sdk ==="
  docker compose up -d --force-recreate "${sdk}-sdk" >/dev/null 2>&1
done

# resolve ports (poll until non-empty)
SDK_URLS=""
for sdk in "$@"; do
  PORT=$(published_port "${sdk}-sdk" 3000) || exit 1
  SDK_URLS="${SDK_URLS}${sdk}=http://localhost:${PORT};"
done

SDK_URLS="$SDK_URLS" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator, load_capability_policy, load_scenarios
from results_aggregator import verify_status_lines
pairs = [p for p in os.environ['SDK_URLS'].split(';') if p]
sdks = dict(p.split('=', 1) for p in pairs)
policy = load_capability_policy()
# Same loader/validation as test_runner.main(); every scenario runs on every
# wrapper (no pass-through skipping).
scs, _empty, invalid = load_scenarios('test_scenarios_complete.json', set(policy['capabilities']))
if invalid:
    for entry in invalid:
        print(f"invalid scenario [{entry['index']}] {entry['name']}: {entry['reason']}")
    sys.exit(1)
o = TestOrchestrator(sdks, verbose=False, capability_policy=policy)
working, failed = o.wait_for_services()
# Drive only the SDKs that actually came up, so a down SDK isn't run through
# every scenario at a 5s timeout each. The failed set is still handed to
# generate_report() below so a down SDK fails the run rather than vanishing.
o.sdks = working
for sc in scs:
    o.run_scenario(sc)
os.makedirs('test-results', exist_ok=True)
# generate_report() is the single source of truth for the run verdict: it
# accounts for services that never started (failed), capabilities-fetch
# failures, zero-scenarios-run, and cross-SDK consistency failures that the
# per-scenario `passed` flags below do not capture (those default to True and
# would print PASS / exit 0). Gate on its return code; keep the per-scenario
# detail print purely as human diagnostics.
report_exit = o.generate_report('test-results/report.json', failed)
with open('test-results/report.json') as src:
    r = json.load(src)
total = len(scs)
# Per-SDK lines: results_aggregator.verify_status_lines (tested in
# tests/test_harness.py). A failed capability measurement prints MEASURE FAIL,
# never PASS, matching generate_report()'s verdict.
for sdk in sdks:
    print("\n".join(verify_status_lines(r, sdk, total)))
sys.exit(report_exit)
PY
