#!/bin/bash
set -e

cd "$(dirname "$0")"

SDK_FILTER=""
SDK_NAMES=""
BUILD_ONLY=false
SKIP_BUILD=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk)
      SDK_FILTER="--sdk $2"
      SDK_NAMES="$2"
      shift 2
      ;;
    --build-only)
      BUILD_ONLY=true
      shift
      ;;
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    -v|--verbose)
      VERBOSE=true
      shift
      ;;
    -h|--help)
      echo "Usage: ./run-tests.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --sdk <name>      Test specific SDK(s), comma-separated (e.g., --sdk react,vue2)"
      echo "  --build-only      Only build containers, don't run tests"
      echo "  --skip-build      Skip building, just run tests"
      echo "  -v, --verbose     Show verbose output"
      echo "  -h, --help        Show this help message"
      echo ""
      echo "Examples:"
      echo "  ./run-tests.sh                    # Build and test all SDKs"
      echo "  ./run-tests.sh --sdk react        # Build and test React SDK only"
      echo "  ./run-tests.sh --skip-build       # Run tests without rebuilding"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

VERBOSE_FLAG=""
if [ "$VERBOSE" = true ]; then
  VERBOSE_FLAG="--verbose"
fi

get_service_names() {
  local sdk_list="$1"
  local services=""
  IFS=',' read -ra SDKS <<< "$sdk_list"
  for sdk in "${SDKS[@]}"; do
    services="$services ${sdk}-sdk"
  done
  echo "$services"
}

if [ "$SKIP_BUILD" = false ]; then
  if [ -n "$SDK_NAMES" ]; then
    SERVICES=$(get_service_names "$SDK_NAMES")
    echo "Building containers for:$SERVICES"
    docker compose build $SERVICES
  else
    echo "Building all containers..."
    docker compose build
  fi
fi

if [ "$BUILD_ONLY" = true ]; then
  echo "Build complete."
  exit 0
fi

docker compose down --remove-orphans --volumes 2>/dev/null || true
docker compose rm -f 2>/dev/null || true
docker network prune -f 2>/dev/null || true

if [ -n "$SDK_NAMES" ]; then
  SERVICES=$(get_service_names "$SDK_NAMES")
  echo "Starting services:$SERVICES"
  docker compose up -d --force-recreate --remove-orphans $SERVICES
else
  echo "Starting all services..."
  docker compose up -d --force-recreate --remove-orphans
fi

echo "Waiting for services to be ready..."
sleep 5

echo "Running tests..."
TEST_EXIT_CODE=0
if [ -n "$SDK_NAMES" ]; then
  # For filtered runs, run locally to avoid orchestrator starting all dependencies
  pip3 install -q requests 2>/dev/null || true

  # Derive SDK URLs from published ports in docker compose.yml
  SDK_URLS=""
  IFS=',' read -ra SDKS <<< "$SDK_NAMES"
  for sdk in "${SDKS[@]}"; do
    PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | cut -d: -f2)
    if [ -z "$PORT" ]; then
      echo "Error: could not resolve port for ${sdk}-sdk" >&2
      exit 1
    fi
    SDK_URLS="$SDK_URLS$sdk:http://localhost:$PORT,"
  done
  SDK_URLS="${SDK_URLS%,}"

  python3 -c "
import json, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator

sdk_urls = '$SDK_URLS'
verbose = '$VERBOSE' == 'true'
sdks = dict(item.split(':http://') for item in sdk_urls.split(','))
sdks = {k: 'http://' + v for k, v in sdks.items()}

orchestrator = TestOrchestrator(sdks, verbose=verbose)
working, failed = orchestrator.wait_for_services()

with open('test_scenarios_complete.json') as f:
    scenarios = [s for s in json.load(f) if 'steps' in s]

print(f'Running {len(scenarios)} scenarios')
for scenario in scenarios:
    orchestrator.run_scenario(scenario)

exit_code = orchestrator.generate_report('test-results/report.json', failed)
sys.exit(exit_code)
" || TEST_EXIT_CODE=$?
else
  SDK_SERVICES=$(docker compose config --services | grep -- '-sdk$' | sed 's/-sdk$//' | paste -sd, -)
  docker compose run --rm -e "SDK_SERVICES=$SDK_SERVICES" orchestrator python3 test_runner.py $VERBOSE_FLAG || TEST_EXIT_CODE=$?
fi

docker compose down --remove-orphans 2>/dev/null || true

echo "Done. (exit code: $TEST_EXIT_CODE)"
exit $TEST_EXIT_CODE
