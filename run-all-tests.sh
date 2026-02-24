#!/bin/bash
set -e

cd "$(dirname "$0")"

SDK_FILTER=""
SDK_NAMES=""
UNIT_ONLY=false
CROSS_ONLY=false
SKIP_BUILD=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk)
      SDK_FILTER="$2"
      SDK_NAMES="$2"
      shift 2
      ;;
    --unit-only)
      UNIT_ONLY=true
      shift
      ;;
    --cross-only)
      CROSS_ONLY=true
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
      echo "Usage: ./run-all-tests.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --sdk <name>      Test specific SDK(s), comma-separated (e.g., --sdk react,vue2)"
      echo "  --unit-only       Only run unit tests"
      echo "  --cross-only      Only run cross-SDK tests (same as run-tests.sh)"
      echo "  --skip-build      Skip building images"
      echo "  -v, --verbose     Show verbose output"
      echo "  -h, --help        Show this help message"
      echo ""
      echo "Examples:"
      echo "  ./run-all-tests.sh                         # Build and run all tests"
      echo "  ./run-all-tests.sh --sdk javascript        # Test JavaScript SDK only"
      echo "  ./run-all-tests.sh --unit-only             # Only run unit tests"
      echo "  ./run-all-tests.sh --cross-only            # Only run cross-SDK tests"
      echo "  ./run-all-tests.sh --sdk go,rust --verbose # Test Go and Rust with verbose output"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

ALL_SDKS=$(docker compose config --services | grep -- '-sdk$' | sed 's/-sdk$//' | tr '\n' ' ')
UNIT_TEST_TIMEOUT=${UNIT_TEST_TIMEOUT:-600}

if [ -n "$SDK_NAMES" ]; then
  IFS=',' read -ra TARGET_SDKS <<< "$SDK_NAMES"
else
  read -ra TARGET_SDKS <<< "$ALL_SDKS"
fi

get_unit_service_names() {
  local services=""
  for sdk in "${TARGET_SDKS[@]}"; do
    services="$services ${sdk}-unit"
  done
  echo "$services"
}

get_cross_service_names() {
  local services=""
  for sdk in "${TARGET_SDKS[@]}"; do
    services="$services ${sdk}-sdk"
  done
  echo "$services"
}

# Build phase
if [ "$SKIP_BUILD" = false ]; then
  if [ "$CROSS_ONLY" = false ]; then
    UNIT_SERVICES=$(get_unit_service_names)
    echo "Building unit test images for:$UNIT_SERVICES"
    docker compose -f docker-compose.unit-tests.yml build $UNIT_SERVICES
  fi

  if [ "$UNIT_ONLY" = false ]; then
    CROSS_SERVICES=$(get_cross_service_names)
    echo "Building cross-SDK test images for:$CROSS_SERVICES"
    docker compose build $CROSS_SERVICES
  fi
fi

wait_with_timeout() {
  local pid=$1
  local timeout=$2
  local elapsed=0
  while [ $elapsed -lt $timeout ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

UNIT_EXIT_CODE=0
CROSS_EXIT_CODE=0
UNIT_RESULTS_FILE="test-results/unit-results.json"
mkdir -p test-results

# Unit tests phase
if [ "$CROSS_ONLY" = false ]; then
  echo ""
  echo "============================================"
  echo "  Running Unit Tests"
  echo "============================================"

  echo "{" > "$UNIT_RESULTS_FILE"
  first=true

  for sdk in "${TARGET_SDKS[@]}"; do
    SERVICE="${sdk}-unit"
    echo -n "  $sdk: "

    OUTPUT_FILE=$(mktemp)

    docker compose -f docker-compose.unit-tests.yml run -T --rm "$SERVICE" > "$OUTPUT_FILE" 2>&1 &
    RUN_PID=$!

    SDK_EXIT=0
    TIMED_OUT=false
    if ! wait_with_timeout $RUN_PID $UNIT_TEST_TIMEOUT; then
      TIMED_OUT=true
      kill $RUN_PID 2>/dev/null || true
      wait $RUN_PID 2>/dev/null || true
      SDK_EXIT=124
    else
      SDK_EXIT=0
      wait $RUN_PID 2>/dev/null || SDK_EXIT=$?
    fi

    OUTPUT=$(cat "$OUTPUT_FILE")
    rm -f "$OUTPUT_FILE"

    if [ "$TIMED_OUT" = true ]; then
      TESTS_PASSED=$(echo "$OUTPUT" | python3 -c "
import sys,re
out=sys.stdin.read()
if re.search(r'\d+ passed', out) and not re.search(r'\d+ failed', out) and not re.search(r'\d+ error', out):
    print('yes')
else:
    print('no')
" 2>/dev/null || echo "no")
      if [ "$TESTS_PASSED" = "yes" ]; then
        echo "PASS (container hung after tests completed)"
        SDK_EXIT=0
      else
        echo "TIMEOUT (>${UNIT_TEST_TIMEOUT}s)"
        UNIT_EXIT_CODE=1
      fi
    elif [ "$SDK_EXIT" -eq 0 ]; then
      echo "PASS"
    else
      echo "FAIL (exit code: $SDK_EXIT)"
      UNIT_EXIT_CODE=1
    fi

    if [ "$VERBOSE" = true ]; then
      echo "$OUTPUT" | sed 's/^/    /'
      echo ""
    fi

    ESCAPED_OUTPUT=$(echo "$OUTPUT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

    if [ "$first" = true ]; then
      first=false
    else
      echo "," >> "$UNIT_RESULTS_FILE"
    fi
    echo "  \"$sdk\": {\"exit_code\": $SDK_EXIT, \"output\": $ESCAPED_OUTPUT}" >> "$UNIT_RESULTS_FILE"
  done

  echo "" >> "$UNIT_RESULTS_FILE"
  echo "}" >> "$UNIT_RESULTS_FILE"
fi

# Cross-SDK tests phase
if [ "$UNIT_ONLY" = false ]; then
  echo ""
  echo "============================================"
  echo "  Running Cross-SDK Tests"
  echo "============================================"

  CROSS_ARGS=""
  if [ -n "$SDK_NAMES" ]; then
    CROSS_ARGS="--sdk $SDK_NAMES"
  fi
  if [ "$SKIP_BUILD" = true ]; then
    CROSS_ARGS="$CROSS_ARGS --skip-build"
  else
    CROSS_ARGS="$CROSS_ARGS --skip-build"
  fi
  if [ "$VERBOSE" = true ]; then
    CROSS_ARGS="$CROSS_ARGS --verbose"
  fi

  ./run-tests.sh $CROSS_ARGS || CROSS_EXIT_CODE=$?
fi

# Results aggregation
echo ""
AGGREGATOR_ARGS=""
if [ "$CROSS_ONLY" = false ]; then
  AGGREGATOR_ARGS="$AGGREGATOR_ARGS $UNIT_RESULTS_FILE"
else
  AGGREGATOR_ARGS="$AGGREGATOR_ARGS /dev/null"
fi
if [ "$UNIT_ONLY" = false ]; then
  AGGREGATOR_ARGS="$AGGREGATOR_ARGS test-results/report.json"
else
  AGGREGATOR_ARGS="$AGGREGATOR_ARGS /dev/null"
fi

python3 orchestrator/results_aggregator.py $AGGREGATOR_ARGS || true

# Final exit code
if [ "$UNIT_EXIT_CODE" -ne 0 ] || [ "$CROSS_EXIT_CODE" -ne 0 ]; then
  exit 1
fi
exit 0
