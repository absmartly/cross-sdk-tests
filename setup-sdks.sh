#!/bin/bash
set -e

cd "$(dirname "$0")/.."

BRANCH_OVERRIDE="${BRANCH:-}"

usage() {
  cat <<EOF
Usage: ./cross-sdk-tests/setup-sdks.sh [OPTIONS]

Clone/update all SDK repositories needed for cross-SDK testing.

Options:
  -b, --branch NAME   Use this branch for ALL SDKs (overrides defaults)
  -h, --help          Show this help message

Environment:
  BRANCH=name          Same as --branch

Examples:
  ./cross-sdk-tests/setup-sdks.sh                          # Use default branches per SDK
  ./cross-sdk-tests/setup-sdks.sh -b main                  # Use 'main' for all SDKs
  ./cross-sdk-tests/setup-sdks.sh -b feat/my-feature       # Use a feature branch everywhere
  BRANCH=main ./cross-sdk-tests/setup-sdks.sh              # Same via env var

Branch configuration:
  Edit the SDK_BRANCHES map in this script to change default per-SDK branches.
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    -b|--branch) BRANCH_OVERRIDE="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

declare -A SDK_BRANCHES=(
  ["angular-sdk"]="feat/angular-sdk-complete"
  ["cpp-sdk"]="feat/initial-sdk"
  ["dotnet-sdk"]="fix/audit-operator-fixes"
  ["elixir-sdk"]="feat/initial-implementation"
  ["flutter-sdk"]="fix/audit-operator-fixes"
  ["go-sdk"]="fix/audit-operator-fixes"
  ["java-sdk"]="fix/all-tests-passing"
  ["javascript-sdk"]="fix/all-tests-passing-268-268"
  ["kotlin-sdk"]="fix/audit-operator-fixes"
  ["liquid-sdk"]="feat/comprehensive-test-coverage"
  ["php-sdk"]="fix/audit-operator-fixes"
  ["python3-sdk"]="fix/python-sdk-all-tests-passing"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="feat/ruby-3.3-compatibility"
  ["rust-sdk"]="fix/all-tests-passing"
  ["scala-sdk"]="fix/all-tests-passing"
  ["swift-sdk"]="feat/cross-sdk-tests-and-fixes"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)

clone_or_update() {
  local repo="$1"
  local branch="$2"
  local url="https://github.com/absmartly/${repo}.git"

  if [ -d "$repo" ]; then
    cd "$repo"
    git fetch origin "$branch" --depth 1 2>/dev/null || { echo "  WARN $repo: branch '$branch' not found on remote"; cd ..; return 1; }
    git checkout "$branch" 2>/dev/null || git checkout FETCH_HEAD 2>/dev/null
    git reset --hard "origin/$branch" 2>/dev/null || true
    cd ..
    echo "  UPDATE $repo ($branch)"
  else
    if git clone -b "$branch" --depth 1 "$url" "$repo" 2>/dev/null; then
      echo "  CLONE $repo ($branch)"
    else
      echo "  FAIL $repo: could not clone branch '$branch'"
      return 1
    fi
  fi
}

if [ -n "$BRANCH_OVERRIDE" ]; then
  echo "Setting up SDK repositories (branch: $BRANCH_OVERRIDE)"
else
  echo "Setting up SDK repositories (per-SDK branches)"
fi
echo "Working directory: $(pwd)"
echo ""

FAILED=()
for repo in $(echo "${!SDK_BRANCHES[@]}" | tr ' ' '\n' | sort); do
  branch="${BRANCH_OVERRIDE:-${SDK_BRANCHES[$repo]}}"
  if ! clone_or_update "$repo" "$branch"; then
    FAILED+=("$repo")
  fi
done

if [ ! -e "dart-sdk" ]; then
  echo "  LINK dart-sdk -> flutter-sdk/packages/dart_sdk"
  ln -sf flutter-sdk/packages/dart_sdk dart-sdk
elif [ -L "dart-sdk" ]; then
  echo "  OK   dart-sdk (symlink)"
fi

echo ""
TOTAL=$(ls -d *-sdk 2>/dev/null | wc -l | tr -d ' ')
echo "Done. $TOTAL SDK directories ready."

if [ ${#FAILED[@]} -gt 0 ]; then
  echo "Failed: ${FAILED[*]}"
  exit 1
fi
