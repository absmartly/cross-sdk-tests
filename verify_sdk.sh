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

for sdk in "$@"; do
  echo "=== bringing up ${sdk}-sdk ==="
  docker compose up -d --force-recreate "${sdk}-sdk" >/dev/null 2>&1
done

# resolve ports (poll until non-empty)
SDK_URLS=""
for sdk in "$@"; do
  PORT=""
  for i in $(seq 1 30); do
    PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | awk -F: '{print $NF}')
    [ -n "$PORT" ] && break
    sleep 1
  done
  if [ -z "$PORT" ]; then echo "ERROR: no port for ${sdk}-sdk"; exit 1; fi
  SDK_URLS="${SDK_URLS}${sdk}=http://localhost:${PORT};"
done

SDK_URLS="$SDK_URLS" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator
pairs = [p for p in os.environ['SDK_URLS'].split(';') if p]
sdks = dict(p.split('=', 1) for p in pairs)
o = TestOrchestrator(sdks, verbose=False, loose_error_match=True, allow_wrapper_skip=True)
working, failed = o.wait_for_services()
# Drive only the SDKs that actually came up, so a down SDK isn't run through
# every scenario at a 5s timeout each. The failed set is still handed to
# generate_report() below so a down SDK fails the run rather than vanishing.
o.sdks = working
scs = [s for s in json.load(open('test_scenarios_complete.json')) if 'steps' in s]
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
r = json.load(open('test-results/report.json'))
total = len(scs)
for sdk in sdks:
    # A service that never started is driven through no scenario, so every
    # per-scenario `passed` defaults True and the SDK would misprint as a full
    # pass. The report's sdk_stats flags it; surface it as DOWN instead.
    if r['sdk_stats'].get(sdk, {}).get('service_failed'):
        print(f"\n{sdk}: DOWN")
        continue
    fails = [sc['name'] for sc in r['results'] if not sc['sdks'].get(sdk, {}).get('passed', True)]
    mark = 'PASS' if not fails else 'FAIL'
    print(f"\n{sdk}: {total-len(fails)}/{total} {mark}")
    for f in fails:
        det = next(sc['sdks'][sdk]['failures'] for sc in r['results'] if sc['name']==f)
        print(f"  - {f} :: {json.dumps(det[:1])[:160]}")
sys.exit(report_exit)
PY
