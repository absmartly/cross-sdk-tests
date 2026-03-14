#!/bin/bash
set -e

cd "$(dirname "$0")"

SDK_NAME=$1
PORT=$2

if [[ ! "$SDK_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "Error: invalid SDK_NAME '$SDK_NAME' (must match [a-zA-Z0-9_-]+)" >&2
  exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "Error: invalid PORT '$PORT' (must be numeric)" >&2
  exit 1
fi

IMAGE="cross-sdk-tests-${SDK_NAME}-sdk"

echo "=== Testing $SDK_NAME SDK on port $PORT ==="

# Stop any existing container for this SDK
docker rm -f "test-${SDK_NAME}-sdk" 2>/dev/null || true

# Run the container
docker run -d --name "test-${SDK_NAME}-sdk" -p "${PORT}:3000" -e "SDK_NAME=${SDK_NAME}" "${IMAGE}"

# Wait for it to be ready
echo "Waiting for $SDK_NAME to be ready..."
MAX_RETRIES=30
for i in $(seq 1 $MAX_RETRIES); do
  if curl -s -o /dev/null -w "%{http_code}" http://localhost:${PORT}/health 2>/dev/null | grep -q "200"; then
    echo "  $SDK_NAME is ready!"
    break
  fi
  if [ $i -eq $MAX_RETRIES ]; then
    echo "  $SDK_NAME FAILED to start"
    docker logs "test-${SDK_NAME}-sdk" 2>&1 | tail -20
    docker rm -f "test-${SDK_NAME}-sdk" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done

# Run tests
pip3 install -q requests 2>/dev/null || true
SDK_NAME="$SDK_NAME" SDK_PORT="$PORT" python3 -c "
import json, os, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator

sdk_name = os.environ['SDK_NAME']
sdk_port = os.environ['SDK_PORT']

sdks = {sdk_name: f'http://localhost:{sdk_port}'}
orchestrator = TestOrchestrator(sdks, verbose=False)
working, failed = orchestrator.wait_for_services()

with open('test_scenarios_complete.json') as f:
    scenarios = [s for s in json.load(f) if 'steps' in s]

print(f'Running {len(scenarios)} scenarios against {sdk_name}')
for scenario in scenarios:
    orchestrator.run_scenario(scenario)

exit_code = orchestrator.generate_report(f'test-results/report-{sdk_name}.json', failed)
sys.exit(exit_code)
"
TEST_EXIT_CODE=$?

# Cleanup
docker rm -f "test-${SDK_NAME}-sdk" 2>/dev/null || true

echo "=== $SDK_NAME test complete (exit code: $TEST_EXIT_CODE) ==="
exit $TEST_EXIT_CODE
