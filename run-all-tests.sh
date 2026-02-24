#!/bin/bash
set -e

cd "$(dirname "$0")"

SDK_NAMES=""
EXCLUDE_NAMES=""
UNIT_ONLY=false
CROSS_ONLY=false
SKIP_BUILD=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk)
      SDK_NAMES="$2"
      shift 2
      ;;
    --exclude)
      EXCLUDE_NAMES="$2"
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
      echo "  --exclude <name>  Exclude SDK(s), comma-separated (e.g., --exclude swift,python)"
      echo "  --unit-only       Only run unit tests"
      echo "  --cross-only      Only run cross-SDK tests (same as run-tests.sh)"
      echo "  --skip-build      Skip building images"
      echo "  -v, --verbose     Show verbose output"
      echo "  -h, --help        Show this help message"
      echo ""
      echo "Examples:"
      echo "  ./run-all-tests.sh                              # Build and run all tests"
      echo "  ./run-all-tests.sh --sdk javascript             # Test JavaScript SDK only"
      echo "  ./run-all-tests.sh --exclude swift              # Test all except Swift"
      echo "  ./run-all-tests.sh --exclude swift,python       # Exclude multiple SDKs"
      echo "  ./run-all-tests.sh --unit-only                  # Only run unit tests"
      echo "  ./run-all-tests.sh --cross-only                 # Only run cross-SDK tests"
      echo "  ./run-all-tests.sh --sdk go,rust --verbose      # Test Go and Rust with verbose output"
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

if [ -n "$EXCLUDE_NAMES" ]; then
  IFS=',' read -ra EXCLUDED <<< "$EXCLUDE_NAMES"
  FILTERED=()
  for sdk in "${TARGET_SDKS[@]}"; do
    skip=false
    for ex in "${EXCLUDED[@]}"; do
      if [ "$sdk" = "$ex" ]; then
        skip=true
        break
      fi
    done
    if [ "$skip" = false ]; then
      FILTERED+=("$sdk")
    fi
  done
  TARGET_SDKS=("${FILTERED[@]}")
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

UNIT_EXIT_CODE=0
CROSS_EXIT_CODE=0
UNIT_RESULTS_FILE="test-results/unit-results.json"
mkdir -p test-results

# Unit tests phase (parallel)
if [ "$CROSS_ONLY" = false ]; then
  echo ""
  echo "============================================"
  echo "  Running Unit Tests (${#TARGET_SDKS[@]} SDKs in parallel)"
  echo "============================================"

  UNIT_TMPDIR=$(mktemp -d)

  for sdk in "${TARGET_SDKS[@]}"; do
    docker compose -f docker-compose.unit-tests.yml run -T --rm "${sdk}-unit" \
      > "$UNIT_TMPDIR/${sdk}.output" 2>&1 &
    echo $! > "$UNIT_TMPDIR/${sdk}.pid"
  done

  ELAPSED=0
  RUNNING=${#TARGET_SDKS[@]}
  while [ $ELAPSED -lt $UNIT_TEST_TIMEOUT ] && [ $RUNNING -gt 0 ]; do
    RUNNING=0
    for sdk in "${TARGET_SDKS[@]}"; do
      [ -f "$UNIT_TMPDIR/${sdk}.exit" ] && continue
      pid=$(cat "$UNIT_TMPDIR/${sdk}.pid")
      if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null && SDK_RC=0 || SDK_RC=$?
        echo "$SDK_RC" > "$UNIT_TMPDIR/${sdk}.exit"
        echo "  $sdk: done (${ELAPSED}s)"
      else
        RUNNING=$((RUNNING + 1))
      fi
    done
    if [ $RUNNING -gt 0 ]; then
      sleep 1
      ELAPSED=$((ELAPSED + 1))
    fi
  done

  for sdk in "${TARGET_SDKS[@]}"; do
    [ -f "$UNIT_TMPDIR/${sdk}.exit" ] && continue
    pid=$(cat "$UNIT_TMPDIR/${sdk}.pid")
    echo "  $sdk: killing (timeout after ${UNIT_TEST_TIMEOUT}s)..."
    kill "$pid" 2>/dev/null || true
    docker compose -f docker-compose.unit-tests.yml kill "${sdk}-unit" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    echo "124" > "$UNIT_TMPDIR/${sdk}.exit"
  done

  echo ""
  echo "{" > "$UNIT_RESULTS_FILE"
  first=true

  for sdk in "${TARGET_SDKS[@]}"; do
    SDK_EXIT=$(cat "$UNIT_TMPDIR/${sdk}.exit" 2>/dev/null || echo "1")
    OUTPUT=$(cat "$UNIT_TMPDIR/${sdk}.output" 2>/dev/null || echo "")

    if [ "$SDK_EXIT" -eq 124 ]; then
      TESTS_PASSED=$(echo "$OUTPUT" | python3 -c "
import sys,re
out=sys.stdin.read()
if re.search(r'\d+ passed', out) and not re.search(r'\d+ failed', out) and not re.search(r'\d+ error', out):
    print('yes')
else:
    print('no')
" 2>/dev/null || echo "no")
      if [ "$TESTS_PASSED" = "yes" ]; then
        echo "  $sdk: PASS (container hung after tests completed)"
        SDK_EXIT=0
      else
        echo "  $sdk: TIMEOUT (>${UNIT_TEST_TIMEOUT}s)"
        UNIT_EXIT_CODE=1
      fi
    elif [ "$SDK_EXIT" -eq 0 ]; then
      echo "  $sdk: PASS"
    else
      echo "  $sdk: FAIL (exit code: $SDK_EXIT)"
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
  rm -rf "$UNIT_TMPDIR"
fi

# Cross-SDK tests phase
if [ "$UNIT_ONLY" = false ]; then
  echo ""
  echo "============================================"
  echo "  Running Cross-SDK Tests"
  echo "============================================"

  CROSS_ARGS=""
  SDK_CSV=$(IFS=,; echo "${TARGET_SDKS[*]}")
  if [ -n "$SDK_CSV" ]; then
    CROSS_ARGS="--sdk $SDK_CSV"
  fi
  CROSS_ARGS="$CROSS_ARGS --skip-build"
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
