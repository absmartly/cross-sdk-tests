#!/bin/bash
# Blocking SDK event-emission gate: every selected wrapper runs its real SDK
# client and publisher against the local collector (no test-1 credentials).
# Usage: ./run-local-collector-tests.sh --sdk swift[,javascript,...] [--skip-build]
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-csdk}"
SDKS=""; SKIP_BUILD=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --sdk) SDKS="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=true; shift ;;
    -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[ -n "$SDKS" ] || { echo "--sdk is required" >&2; exit 1; }
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.local-collector.yml)
SERVICES=""; for s in ${SDKS//,/ }; do SERVICES="$SERVICES $s-sdk"; done
[ "$SKIP_BUILD" = true ] || "${COMPOSE[@]}" build local-collector $SERVICES orchestrator
trap '"${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true' EXIT
"${COMPOSE[@]}" up -d --force-recreate local-collector $SERVICES
mkdir -p test-results
"${COMPOSE[@]}" run --rm --no-deps \
  -v "$PWD/local-collector:/local-collector:ro" -v "$PWD/test-results:/app/test-results" \
  -e LOCAL_COLLECTOR_URL=http://local-collector:8090 \
  orchestrator python3 local_collector_gate.py --sdk "$SDKS" --report test-results/local-collector.json
