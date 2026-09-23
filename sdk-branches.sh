#!/bin/bash
# Single source of truth for per-SDK known-good branches, used by both
# setup-sdks.sh (local runs) and .github/workflows/cross-sdk.yml (CI).
# Edit branches here only — do not duplicate this map elsewhere.
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="feat/angular-sdk-complete"
  ["cpp-sdk"]="feat/initial-sdk"
  ["dotnet-sdk"]="fix/audit-operator-fixes"
  ["elixir-sdk"]="feat/initial-implementation"
  ["flutter-sdk"]="fix/audit-operator-fixes"
  ["go-sdk"]="fix/audit-operator-fixes"
  ["java-sdk"]="fix/all-tests-passing"
  ["javascript-sdk"]="feat/holdouts"
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
