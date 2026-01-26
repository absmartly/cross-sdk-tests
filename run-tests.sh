#!/bin/bash
set -e

cd "$(dirname "$0")"

SDK_FILTER=""
BUILD_ONLY=false
SKIP_BUILD=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk)
      SDK_FILTER="--sdk $2"
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

if [ "$VERBOSE" = true ]; then
  set -x
fi

if [ "$SKIP_BUILD" = false ]; then
  echo "Building containers..."
  docker-compose build
fi

if [ "$BUILD_ONLY" = true ]; then
  echo "Build complete."
  exit 0
fi

echo "Starting services..."
docker-compose up -d

echo "Waiting for services to be ready..."
sleep 5

echo "Running tests..."
docker-compose run --rm orchestrator python3 test_runner.py $SDK_FILTER

echo "Done."
