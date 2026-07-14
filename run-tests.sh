#!/bin/bash
set -e

cd "$(dirname "$0")"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-csdk}"

# Remove stale, git-ignored build artifacts (js/es/lib/types/dist/...) from the
# JS-family SDK source dirs before building. These land in the Docker build
# context and break the in-image `npm run build` (prettier/eslint format:check
# trips on stale generated files). git clean -dfX only ever deletes files git
# already ignores, so source is never touched. No-op for non-git or absent dirs.
clean_js_build_artifacts() {
  local js_sdks="javascript-sdk typescript-sdk react-sdk vue2-sdk vue3-sdk angular-sdk"
  for sdk in $js_sdks; do
    local dir="../$sdk"
    [ -d "$dir" ] || continue
    if git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      git -C "$dir" clean -dfX -- js es lib types dist build coverage .nuxt .angular >/dev/null 2>&1 || true
    fi
  done
}

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

# Ensure the `requests` module is importable for the local orchestrator path.
# Avoids a PEP 668 "externally-managed-environment" failure by only attempting
# an install when the module is genuinely missing, and hard-failing (rather than
# silently continuing) if that install does not work.
ensure_requests() {
  if python3 -c "import requests" >/dev/null 2>&1; then
    return 0
  fi
  echo "Python 'requests' not found; attempting to install..."
  if pip3 install -q requests >/dev/null 2>&1 && python3 -c "import requests" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: the Python 'requests' package is required but could not be installed." >&2
  echo "       Install it manually (e.g. 'pip3 install --user requests' or in a venv) and re-run." >&2
  exit 1
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
  clean_js_build_artifacts
  export COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-3}

  # Build heavy SDKs first (JVM/native — need lots of memory) in small batches,
  # then build the rest. This prevents OOM during parallel builds.
  HEAVY_SDKS="scala-sdk scala-unit java-sdk java-unit kotlin-sdk kotlin-unit swift-sdk swift-unit cpp-sdk cpp-unit dotnet-sdk dotnet-unit elixir-sdk elixir-unit rust-sdk rust-unit"
  LIGHT_SDKS=""
  ALL_SERVICES=$(docker compose config --services 2>/dev/null)

  for svc in $ALL_SERVICES; do
    is_heavy=false
    for h in $HEAVY_SDKS; do
      if [ "$svc" = "$h" ]; then is_heavy=true; break; fi
    done
    if [ "$is_heavy" = false ]; then
      LIGHT_SDKS="$LIGHT_SDKS $svc"
    fi
  done

  if [ -n "$SDK_NAMES" ]; then
    SERVICES=$(get_service_names "$SDK_NAMES")
    echo "Building containers for:$SERVICES orchestrator (parallel limit: $COMPOSE_PARALLEL_LIMIT)"
    docker_compose_with_recovery build $SERVICES orchestrator
  else
    echo "Building heavy SDKs first (parallel limit: 2)..."
    COMPOSE_PARALLEL_LIMIT=2 docker_compose_with_recovery build $HEAVY_SDKS

    echo "Building remaining SDKs (parallel limit: $COMPOSE_PARALLEL_LIMIT)..."
    docker_compose_with_recovery build $LIGHT_SDKS
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

BATCH_SIZE=${COMPOSE_SERVICE_BATCH:-5}
echo "Starting services in batches of $BATCH_SIZE:$TARGET_SERVICES"

IFS=' ' read -ra SVC_ARRAY <<< "$TARGET_SERVICES"
for ((i=0; i<${#SVC_ARRAY[@]}; i+=BATCH_SIZE)); do
  BATCH=("${SVC_ARRAY[@]:i:BATCH_SIZE}")
  echo "  Starting batch: ${BATCH[*]}"
  set +e
  START_OUTPUT=$(docker compose up -d --remove-orphans "${BATCH[@]}" 2>&1)
  START_EXIT=$?
  set -e

  if [ "$START_EXIT" -ne 0 ]; then
    if echo "$START_OUTPUT" | grep -qi "500 Internal Server Error"; then
      echo "Docker returned 500. Restarting Docker..."
      restart_docker
      docker compose up -d --remove-orphans "${BATCH[@]}" 2>/dev/null || true
    elif echo "$START_OUTPUT" | grep -q "No such container"; then
      docker compose rm -f "${BATCH[@]}" 2>/dev/null || true
      docker compose up -d --remove-orphans "${BATCH[@]}" 2>/dev/null || true
    else
      echo "ERROR: 'docker compose up -d' failed for batch (${BATCH[*]}) with no recoverable cause:" >&2
      echo "$START_OUTPUT" >&2
      exit "$START_EXIT"
    fi
  fi
  sleep 2
done

echo "All containers launched. Checking health and restarting crashed services..."
sleep 5
MAX_HEALTH_RETRIES=3
for health_attempt in $(seq 1 $MAX_HEALTH_RETRIES); do
  UNHEALTHY=""
  for svc in "${SVC_ARRAY[@]}"; do
    STATUS=$(docker compose ps --format '{{.Status}}' "$svc" 2>/dev/null)
    if echo "$STATUS" | grep -qi "exit\|dead\|created"; then
      UNHEALTHY="$UNHEALTHY $svc"
    fi
  done

  if [ -z "$UNHEALTHY" ]; then
    break
  fi

  if [ "$health_attempt" -lt "$MAX_HEALTH_RETRIES" ]; then
    echo "  Restarting crashed services (attempt $health_attempt/$MAX_HEALTH_RETRIES):$UNHEALTHY"
    docker compose up -d $UNHEALTHY 2>/dev/null || true
    sleep 5
  else
    echo "  ⚠️  Some services still unhealthy after $MAX_HEALTH_RETRIES attempts:$UNHEALTHY"
  fi
done

echo "Waiting for services to be ready..."

echo "Running tests..."
TEST_EXIT_CODE=0
if [ -n "$SDK_NAMES" ]; then
  # For filtered runs, run locally to avoid orchestrator starting all dependencies
  ensure_requests

  # Derive SDK URLs from published ports in docker compose.yml
  SDK_URLS=""
  IFS=',' read -ra SDKS <<< "$SDK_NAMES"
  for sdk in "${SDKS[@]}"; do
    PORT=""
    for _ in $(seq 1 30); do
      PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | awk -F: '{print $NF}')
      [ -n "$PORT" ] && break
      sleep 1
    done
    if [ -z "$PORT" ]; then
      echo "ERROR: could not resolve published port for ${sdk}-sdk after 30s" >&2
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

orchestrator = TestOrchestrator(sdks, verbose=verbose, allow_wrapper_skip=True)
working, failed = orchestrator.wait_for_services()
# Drive only the SDKs that came up, so a down SDK isn't run through every
# scenario at a 5s timeout each. The failed set is still passed to
# generate_report() below, so a down SDK fails the run rather than vanishing.
orchestrator.sdks = working

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
    python3 test_runner.py $VERBOSE_FLAG || TEST_EXIT_CODE=$?
fi

docker compose down --remove-orphans 2>/dev/null || true

echo "Done. (exit code: $TEST_EXIT_CODE)"
exit $TEST_EXIT_CODE
