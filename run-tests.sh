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

restart_docker() {
  if [[ "$(uname)" == "Darwin" ]]; then
    echo "Restarting Docker Desktop..."
    osascript -e 'quit app "Docker"' 2>/dev/null || true
    sleep 2
    if pgrep -q "com.docker.backend" 2>/dev/null; then
      echo "Docker still running, force killing..."
      killall Docker 2>/dev/null || true
      killall com.docker.backend 2>/dev/null || true
      killall com.docker.supervisor 2>/dev/null || true
      sleep 3
    fi
    open -a Docker
    echo "Waiting for Docker to become healthy..."
    local retries=0
    while ! docker run --rm hello-world >/dev/null 2>&1; do
      retries=$((retries + 1))
      if [ $retries -ge 120 ]; then
        echo "ERROR: Docker failed to become healthy after 120 seconds"
        exit 1
      fi
      sleep 1
    done
  else
    echo "Restarting Docker daemon..."
    sudo systemctl restart docker 2>/dev/null || sudo service docker restart 2>/dev/null || true
    local retries=0
    while ! docker run --rm hello-world >/dev/null 2>&1; do
      retries=$((retries + 1))
      if [ $retries -ge 30 ]; then
        echo "ERROR: Docker failed to become healthy after 30 seconds"
        exit 1
      fi
      sleep 1
    done
  fi
  echo "Docker is healthy."
}

check_docker_health() {
  if ! docker run --rm hello-world >/dev/null 2>&1; then
    echo "Docker is not healthy (cannot run containers)."
    restart_docker
  fi
}

docker_compose_with_recovery() {
  local cmd="$1"
  shift
  local max_attempts=3
  local tmplog
  tmplog=$(mktemp /tmp/docker-compose-XXXXXX.log)

  for attempt in $(seq 1 $max_attempts); do
    set +e
    docker compose $cmd "$@" 2>&1 | tee "$tmplog"
    EXIT_CODE=${PIPESTATUS[0]}
    set -e

    if [ $EXIT_CODE -eq 0 ]; then
      rm -f "$tmplog"
      return 0
    fi

    if [ $attempt -ge $max_attempts ]; then
      rm -f "$tmplog"
      echo "ERROR: docker compose $cmd failed after $max_attempts attempts"
      return $EXIT_CODE
    fi

    if grep -qi "500 Internal Server Error" "$tmplog"; then
      echo ""
      echo "Docker returned 500 (attempt $attempt/$max_attempts). Restarting Docker..."
      restart_docker
    elif [ "$cmd" = "build" ]; then
      echo ""
      echo "Build failed (attempt $attempt/$max_attempts). Pruning failed layers and retrying..."
      docker builder prune --filter type=regular -f >/dev/null 2>&1 || true
      sleep 3
    else
      rm -f "$tmplog"
      return $EXIT_CODE
    fi
  done

  rm -f "$tmplog"
  return 1
}

check_docker_health

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
  export COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-5}
  if [ -n "$SDK_NAMES" ]; then
    SERVICES=$(get_service_names "$SDK_NAMES")
    echo "Building containers for:$SERVICES orchestrator (parallel limit: $COMPOSE_PARALLEL_LIMIT)"
    docker_compose_with_recovery build $SERVICES orchestrator
  else
    echo "Building all containers (parallel limit: $COMPOSE_PARALLEL_LIMIT)..."
    docker_compose_with_recovery build
  fi
fi

if [ "$BUILD_ONLY" = true ]; then
  echo "Build complete."
  exit 0
fi

docker compose down --remove-orphans --volumes 2>/dev/null || true
docker compose rm -f 2>/dev/null || true
rm -f test-results/report.json 2>/dev/null || true

if [ -n "$SDK_NAMES" ]; then
  IFS=',' read -ra TARGET_SDKS <<< "$SDK_NAMES"
else
  read -ra TARGET_SDKS <<< "$(docker compose config --services | grep -- '-sdk$' | sed 's/-sdk$//' | tr '\n' ' ')"
fi

if [ "${#TARGET_SDKS[@]}" -eq 0 ]; then
  echo "No SDK services selected."
  exit 1
fi

SDK_CSV=$(IFS=,; echo "${TARGET_SDKS[*]}")
TARGET_SERVICES=$(get_service_names "$SDK_CSV")

RUN_FALLBACK=false
FALLBACK_CONTAINERS=()

started=false
for attempt in 1 2 3; do
  echo "Starting services:$TARGET_SERVICES"
  set +e
  START_OUTPUT=$(docker compose up -d --remove-orphans $TARGET_SERVICES 2>&1)
  START_EXIT=$?
  set -e

  echo "$START_OUTPUT"

  if [ "$START_EXIT" -eq 0 ]; then
    started=true
    break
  fi

  if echo "$START_OUTPUT" | grep -qi "500 Internal Server Error"; then
    echo "Docker returned 500 during startup (attempt $attempt/3). Restarting Docker..."
    restart_docker
    docker compose down --remove-orphans --volumes 2>/dev/null || true
    sleep 2
    continue
  fi

  if echo "$START_OUTPUT" | grep -q "No such container"; then
    echo "Compose startup hit stale container reference (attempt $attempt/3), retrying..."
    docker compose down --remove-orphans --volumes 2>/dev/null || true
    docker compose rm -f 2>/dev/null || true
    sleep 2
    continue
  fi

  exit "$START_EXIT"
done

if [ "$started" != true ]; then
  echo "Compose startup failed after retries. Falling back to detached service runs..."
  RUN_FALLBACK=true
  for sdk in "${TARGET_SDKS[@]}"; do
    service="${sdk}-sdk"
    container_name="${sdk}-sdk"
    docker rm -f "$container_name" >/dev/null 2>&1 || true

    set +e
    START_OUTPUT=$(docker compose run -d --name "$container_name" "$service" 2>&1)
    START_EXIT=$?
    set -e
    echo "$START_OUTPUT"

    if [ "$START_EXIT" -ne 0 ]; then
      echo "Failed to start fallback service: $service (exit code: $START_EXIT)"
      exit "$START_EXIT"
    fi

    FALLBACK_CONTAINERS+=("$container_name")
  done
fi

echo "Waiting for services to be ready..."

echo "Running tests..."
TEST_EXIT_CODE=0
if [ "$RUN_FALLBACK" = true ]; then
  SDK_SERVICES=$(IFS=,; echo "${TARGET_SDKS[*]}")
  docker compose run --no-deps --rm -e "SDK_SERVICES=$SDK_SERVICES" orchestrator \
    python3 test_runner.py $VERBOSE_FLAG --loose-error-match || TEST_EXIT_CODE=$?
elif [ -n "$SDK_NAMES" ]; then
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

  SDK_URLS="$SDK_URLS" VERBOSE="$VERBOSE" python3 -c "
import json, os, sys
sys.path.insert(0, 'orchestrator')
from test_runner import TestOrchestrator

sdk_urls = os.environ['SDK_URLS']
verbose = os.environ.get('VERBOSE', 'false') == 'true'
sdks = dict(item.split(':http://') for item in sdk_urls.split(','))
sdks = {k: 'http://' + v for k, v in sdks.items()}

orchestrator = TestOrchestrator(sdks, verbose=verbose, loose_error_match=True, allow_wrapper_skip=True)
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
  SDK_SERVICES=$(IFS=,; echo "${TARGET_SDKS[*]}")
  docker compose run --rm -e "SDK_SERVICES=$SDK_SERVICES" orchestrator \
    python3 test_runner.py $VERBOSE_FLAG --loose-error-match || TEST_EXIT_CODE=$?
fi

for container in "${FALLBACK_CONTAINERS[@]}"; do
  docker rm -f "$container" >/dev/null 2>&1 || true
done

docker compose down --remove-orphans 2>/dev/null || true

echo "Done. (exit code: $TEST_EXIT_CODE)"
exit $TEST_EXIT_CODE
