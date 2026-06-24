#!/bin/bash
set -e

cd "$(dirname "$0")"

SDK_NAMES=""
EXCLUDE_NAMES=""
UNIT_ONLY=false
CROSS_ONLY=false
E2E=false
SKIP_BUILD=false
VERBOSE=false
PROFILE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --sdk requires a value"
        exit 1
      fi
      SDK_NAMES="$2"
      shift 2
      ;;
    --exclude)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --exclude requires a value"
        exit 1
      fi
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
    --e2e)
      E2E=true
      shift
      ;;
    --profile)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --profile requires a value"
        exit 1
      fi
      PROFILE="$2"
      shift 2
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
      echo "  --e2e             Also run end-to-end tests after unit+cross-SDK tests"
      echo "  --profile <name>  ABsmartly CLI profile for --e2e (default: e2e)"
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
      echo "  ./run-all-tests.sh --e2e --profile test-1       # Also run e2e against the test-1 profile"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [ "$UNIT_ONLY" = true ] && [ "$CROSS_ONLY" = true ]; then
  echo "Error: --unit-only and --cross-only cannot be used together"
  exit 1
fi

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

if [ ${#TARGET_SDKS[@]} -eq 0 ]; then
  echo "Error: no SDKs selected (all may have been excluded)"
  exit 1
fi

# Build phase
if [ "$SKIP_BUILD" = false ]; then
  clean_js_build_artifacts
  BUILD_SERVICES=""
  if [ "$CROSS_ONLY" = false ]; then
    BUILD_SERVICES="$BUILD_SERVICES $(get_unit_service_names)"
  fi
  if [ "$UNIT_ONLY" = false ]; then
    BUILD_SERVICES="$BUILD_SERVICES $(get_cross_service_names)"
  fi
  echo "Building images for:$BUILD_SERVICES"
  docker compose build $BUILD_SERVICES
fi

UNIT_EXIT_CODE=0
CROSS_EXIT_CODE=0
UNIT_RESULTS_FILE="test-results/unit-results.json"
mkdir -p test-results

docker compose down --remove-orphans --volumes 2>/dev/null || true
docker compose rm -f 2>/dev/null || true

# Unit tests phase (parallel)
if [ "$CROSS_ONLY" = false ]; then
  echo ""
  echo "============================================"
  echo "  Running Unit Tests (${#TARGET_SDKS[@]} SDKs in parallel)"
  echo "============================================"

  UNIT_TMPDIR=$(mktemp -d)

  for sdk in "${TARGET_SDKS[@]}"; do
    docker compose run -T --rm "${sdk}-unit" \
      > "$UNIT_TMPDIR/${sdk}.output" 2>&1 &
    echo $! > "$UNIT_TMPDIR/${sdk}.pid"
  done

  ELAPSED=0
  TOTAL=${#TARGET_SDKS[@]}
  DONE=0
  while [ $ELAPSED -lt $UNIT_TEST_TIMEOUT ] && [ $DONE -lt $TOTAL ]; do
    for sdk in "${TARGET_SDKS[@]}"; do
      [ -f "$UNIT_TMPDIR/${sdk}.exit" ] && continue
      pid=$(cat "$UNIT_TMPDIR/${sdk}.pid")
      if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null && SDK_RC=0 || SDK_RC=$?
        echo "$SDK_RC" > "$UNIT_TMPDIR/${sdk}.exit"
        DONE=$((DONE + 1))
        REMAINING=$((TOTAL - DONE))
        echo "  $sdk: done (${ELAPSED}s) [$REMAINING remaining]"
      fi
    done
    if [ $DONE -lt $TOTAL ]; then
      sleep 1
      ELAPSED=$((ELAPSED + 1))
    fi
  done

  for sdk in "${TARGET_SDKS[@]}"; do
    [ -f "$UNIT_TMPDIR/${sdk}.exit" ] && continue
    pid=$(cat "$UNIT_TMPDIR/${sdk}.pid")
    echo "  $sdk: killing (timeout after ${UNIT_TEST_TIMEOUT}s)..."
    kill "$pid" 2>/dev/null || true
    docker compose kill "${sdk}-unit" 2>/dev/null || true
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
      echo "  $sdk: TIMEOUT (>${UNIT_TEST_TIMEOUT}s)"
      UNIT_EXIT_CODE=1
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

    ESCAPED_OUTPUT=$(echo "$OUTPUT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo '"no output"')

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

E2E_EXIT_CODE=0

# E2E tests phase
if [ "$E2E" = true ]; then
  echo ""
  echo "============================================"
  echo "  Running E2E Tests"
  echo "============================================"

  E2E_ARGS="--skip-build"
  if [ -n "$SDK_NAMES" ]; then
    E2E_ARGS="$E2E_ARGS --sdk $SDK_NAMES"
  fi
  if [ -n "$PROFILE" ]; then
    E2E_ARGS="$E2E_ARGS --profile $PROFILE"
  fi
  if [ "$VERBOSE" = true ]; then
    E2E_ARGS="$E2E_ARGS --verbose"
  fi

  ./run-e2e-tests.sh $E2E_ARGS || E2E_EXIT_CODE=$?
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
if [ "$UNIT_EXIT_CODE" -ne 0 ] || [ "$CROSS_EXIT_CODE" -ne 0 ] || [ "$E2E_EXIT_CODE" -ne 0 ]; then
  exit 1
fi
exit 0
