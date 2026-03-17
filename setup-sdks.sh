#!/bin/bash
set -e

cd "$(dirname "$0")/.."

GITHUB_TOKEN="${GITHUB_TOKEN:-}"

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

PRIVATE_REPOS="kotlin-sdk"

clone_repo() {
  local repo="$1"
  local branch="$2"
  local url

  if echo "$PRIVATE_REPOS" | grep -qw "$repo"; then
    if [ -z "$GITHUB_TOKEN" ]; then
      echo "  SKIP $repo (private — set GITHUB_TOKEN to clone)"
      return 1
    fi
    url="https://${GITHUB_TOKEN}@github.com/absmartly/${repo}.git"
  else
    url="https://github.com/absmartly/${repo}.git"
  fi

  if [ -d "$repo" ]; then
    echo "  UPDATE $repo"
    cd "$repo"
    git fetch origin "$branch" --depth 1 2>/dev/null
    git checkout "$branch" 2>/dev/null || git checkout FETCH_HEAD 2>/dev/null
    git reset --hard "origin/$branch" 2>/dev/null || true
    cd ..
  else
    echo "  CLONE $repo ($branch)"
    git clone -b "$branch" --depth 1 "$url" "$repo" 2>/dev/null
  fi
}

echo "Setting up SDK repositories..."
echo "Working directory: $(pwd)"
echo ""

FAILED=()
for repo in "${!SDK_BRANCHES[@]}"; do
  branch="${SDK_BRANCHES[$repo]}"
  if ! clone_repo "$repo" "$branch"; then
    FAILED+=("$repo")
  fi
done

if [ ! -e "dart-sdk" ]; then
  echo "  LINK dart-sdk -> flutter-sdk/packages/dart_sdk"
  ln -sf flutter-sdk/packages/dart_sdk dart-sdk
elif [ ! -L "dart-sdk" ]; then
  echo "  WARN dart-sdk exists but is not a symlink"
fi

echo ""
TOTAL=$(ls -d *-sdk 2>/dev/null | wc -l | tr -d ' ')
echo "Done. $TOTAL SDK directories ready."

if [ ${#FAILED[@]} -gt 0 ]; then
  echo ""
  echo "Failed to fetch: ${FAILED[*]}"
  echo "For private repos, set GITHUB_TOKEN and re-run:"
  echo "  GITHUB_TOKEN=ghp_xxx ./cross-sdk-tests/setup-sdks.sh"
  exit 1
fi
