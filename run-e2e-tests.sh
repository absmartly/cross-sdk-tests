#!/bin/bash
set -e

cd "$(dirname "$0")"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-csdk}"

if [ -f e2e-config.env ]; then
  set -a
  source e2e-config.env
  set +a
fi

SDK_NAMES=""
UNITS="${ABSMARTLY_E2E_UNITS:-100}"
PROFILE="${ABSMARTLY_E2E_PROFILE:-e2e}"
TIMEOUT_VAL="${ABSMARTLY_E2E_TIMEOUT:-60}"
CLEANUP=false
VERBOSE=false
SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk)
      SDK_NAMES="$2"
      shift 2
      ;;
    --units)
      UNITS="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT_VAL="$2"
      shift 2
      ;;
    --cleanup)
      CLEANUP=true
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
      echo "Usage: ./run-e2e-tests.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --sdk <name>       Test specific SDK(s), comma-separated (e.g., --sdk react,vue2)"
      echo "  --units <n>        Number of units per SDK (default: 100)"
      echo "  --profile <name>   ABsmartly CLI profile (default: e2e)"
      echo "  --timeout <s>      Timeout in seconds for metrics polling (default: 60)"
      echo "  --cleanup          Archive stale e2e experiments and exit"
      echo "  --skip-build       Skip building wrapper containers"
      echo "  -v, --verbose      Show verbose output"
      echo "  -h, --help         Show this help message"
      echo ""
      echo "Examples:"
      echo "  ./run-e2e-tests.sh                          # Build and run e2e tests for all SDKs"
      echo "  ./run-e2e-tests.sh --sdk javascript,python  # Test specific SDKs only"
      echo "  ./run-e2e-tests.sh --skip-build             # Run without rebuilding containers"
      echo "  ./run-e2e-tests.sh --cleanup                # Archive stale e2e experiments"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

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

get_service_names() {
  local sdk_list="$1"
  local services=""
  IFS=',' read -ra SDKS <<< "$sdk_list"
  for sdk in "${SDKS[@]}"; do
    services="$services ${sdk}-sdk"
  done
  echo "$services"
}

check_docker_health

if [ "$CLEANUP" = true ]; then
  pip3 install -q requests 2>/dev/null || true
  VERBOSE_OPT=""
  [ "$VERBOSE" = true ] && VERBOSE_OPT="--verbose"
  SDK_SERVICES="placeholder" python3 orchestrator/e2e_runner.py --cleanup --profile "$PROFILE" $VERBOSE_OPT || true
  exit 0
fi

if [ -n "$SDK_NAMES" ]; then
  TARGET_SDKS_CSV="$SDK_NAMES"
  read -ra TARGET_SDKS <<< "$(echo "$SDK_NAMES" | tr ',' ' ')"
else
  read -ra TARGET_SDKS <<< "$(docker compose config --services | grep -- '-sdk$' | sed 's/-sdk$//' | tr '\n' ' ')"
  TARGET_SDKS_CSV=$(IFS=,; echo "${TARGET_SDKS[*]}")
fi

if [ "${#TARGET_SDKS[@]}" -eq 0 ]; then
  echo "No SDK services selected."
  exit 1
fi

TARGET_SERVICES=$(get_service_names "$TARGET_SDKS_CSV")

if [ "$SKIP_BUILD" = false ]; then
  export COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-3}

  HEAVY_SDKS="scala-sdk java-sdk kotlin-sdk swift-sdk cpp-sdk dotnet-sdk elixir-sdk rust-sdk"

  if [ -n "$SDK_NAMES" ]; then
    echo "Building containers for:$TARGET_SERVICES (parallel limit: $COMPOSE_PARALLEL_LIMIT)"
    docker_compose_with_recovery build $TARGET_SERVICES
  else
    HEAVY_TARGETS=""
    LIGHT_TARGETS=""
    for svc in $TARGET_SERVICES; do
      is_heavy=false
      for h in $HEAVY_SDKS; do
        if [ "$svc" = "$h" ]; then is_heavy=true; break; fi
      done
      if [ "$is_heavy" = true ]; then
        HEAVY_TARGETS="$HEAVY_TARGETS $svc"
      else
        LIGHT_TARGETS="$LIGHT_TARGETS $svc"
      fi
    done

    if [ -n "$HEAVY_TARGETS" ]; then
      echo "Building heavy SDK wrappers first (parallel limit: 2):$HEAVY_TARGETS"
      COMPOSE_PARALLEL_LIMIT=2 docker_compose_with_recovery build $HEAVY_TARGETS
    fi

    if [ -n "$LIGHT_TARGETS" ]; then
      echo "Building remaining SDK wrappers (parallel limit: $COMPOSE_PARALLEL_LIMIT):$LIGHT_TARGETS"
      docker_compose_with_recovery build $LIGHT_TARGETS
    fi
  fi
fi

docker compose down --remove-orphans --volumes 2>/dev/null || true
docker compose rm -f 2>/dev/null || true

BATCH_SIZE=${COMPOSE_SERVICE_BATCH:-5}
echo "Starting wrapper containers in batches of $BATCH_SIZE:$TARGET_SERVICES"

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
    echo "  Some services still unhealthy after $MAX_HEALTH_RETRIES attempts:$UNHEALTHY"
  fi
done

pip3 install -q requests 2>/dev/null || true

SDK_URLS=""
for sdk in "${TARGET_SDKS[@]}"; do
  PORT=$(docker compose port "${sdk}-sdk" 3000 2>/dev/null | awk -F: '{print $NF}')
  if [ -z "$PORT" ]; then
    echo "Warning: could not resolve port for ${sdk}-sdk — skipping" >&2
    continue
  fi
  SDK_URLS="${SDK_URLS}${sdk}=http://localhost:${PORT},"
done
SDK_URLS="${SDK_URLS%,}"

cleanup_containers() {
  docker compose down --remove-orphans 2>/dev/null || true
}
trap cleanup_containers EXIT

VERBOSE_OPT=""
[ "$VERBOSE" = true ] && VERBOSE_OPT="--verbose"

SDK_URLS_OVERRIDE="$SDK_URLS" \
SDK_SERVICES="$TARGET_SDKS_CSV" \
python3 orchestrator/e2e_runner.py \
  --units "$UNITS" \
  --profile "$PROFILE" \
  --timeout "$TIMEOUT_VAL" \
  $VERBOSE_OPT \
  || E2E_EXIT_CODE=$?

echo "Done. (exit code: ${E2E_EXIT_CODE:-0})"
exit "${E2E_EXIT_CODE:-0}"
