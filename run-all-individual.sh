#!/bin/bash
cd "$(dirname "$0")"

SDKS=$(docker compose config --services | grep -- '-sdk$' | sed 's/-sdk$//')
RESULTS_DIR="test-results/individual"
mkdir -p "$RESULTS_DIR"

cleanup() {
  docker-compose down --remove-orphans 2>/dev/null
  docker rm -f $(docker ps -aq) 2>/dev/null
  sleep 1
}

for sdk in $SDKS; do
  echo "============================================"
  echo "=== Testing: $sdk"
  echo "============================================"

  cleanup

  docker-compose up -d --force-recreate "${sdk}-sdk" 2>&1

  echo "Waiting for ${sdk}-sdk to be ready..."
  sleep 10

  PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | cut -d: -f2)
  if [ -z "$PORT" ]; then
    echo "Error: could not resolve port for ${sdk}-sdk" >&2
    continue
  fi

  python3 -c "
import json, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator

sdks = {'$sdk': 'http://localhost:$PORT'}
orchestrator = TestOrchestrator(sdks, verbose=False)
working, failed = orchestrator.wait_for_services()

with open('test_scenarios_complete.json') as f:
    scenarios = [s for s in json.load(f) if 'steps' in s]

print(f'Running {len(scenarios)} scenarios for $sdk')
for scenario in scenarios:
    orchestrator.run_scenario(scenario)

exit_code = orchestrator.generate_report('$RESULTS_DIR/${sdk}_report.json', failed)
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
