#!/bin/bash
cd "$(dirname "$0")"

SDKS=$(docker compose config --services | grep -- '-sdk$' | sed 's/-sdk$//')
RESULTS_DIR="test-results/individual"
mkdir -p "$RESULTS_DIR"

cleanup() {
  docker compose down --remove-orphans 2>/dev/null || true
  sleep 1
}

for sdk in $SDKS; do
  echo "============================================"
  echo "=== Testing: $sdk"
  echo "============================================"

  cleanup

  docker-compose up -d --force-recreate "${sdk}-sdk" 2>&1

  echo "Waiting for ${sdk}-sdk to be ready..."
  MAX_RETRIES=30
  for i in $(seq 1 $MAX_RETRIES); do
    SDK_PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | cut -d: -f2)
    if [ -n "$SDK_PORT" ] && curl -s -o /dev/null -w "%{http_code}" "http://localhost:${SDK_PORT}/health" 2>/dev/null | grep -q "200"; then
      echo "  ${sdk}-sdk is ready!"
      break
    fi
    if [ $i -eq $MAX_RETRIES ]; then
      echo "  ${sdk}-sdk FAILED to start within ${MAX_RETRIES}s"
    fi
    sleep 1
  done

  PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | cut -d: -f2)
  if [ -z "$PORT" ]; then
    echo "Error: could not resolve port for ${sdk}-sdk" >&2
    continue
  fi

  SDK_NAME="$sdk" SDK_PORT="$PORT" RESULTS_DIR="$RESULTS_DIR" python3 -c "
import json, os, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator

sdk_name = os.environ['SDK_NAME']
sdk_port = os.environ['SDK_PORT']
results_dir = os.environ['RESULTS_DIR']

sdks = {sdk_name: f'http://localhost:{sdk_port}'}
orchestrator = TestOrchestrator(sdks, verbose=False)
working, failed = orchestrator.wait_for_services()

with open('test_scenarios_complete.json') as f:
    scenarios = [s for s in json.load(f) if 'steps' in s]

print(f'Running {len(scenarios)} scenarios for {sdk_name}')
for scenario in scenarios:
    orchestrator.run_scenario(scenario)

exit_code = orchestrator.generate_report(f'{results_dir}/{sdk_name}_report.json', failed)
sys.exit(exit_code)
" 2>&1 | tee "$RESULTS_DIR/${sdk}_output.txt"

  echo "Result for $sdk: exit code $?"
  echo ""
done

cleanup

echo ""
echo "============================================"
echo "=== ALL TESTS COMPLETE"
echo "============================================"
