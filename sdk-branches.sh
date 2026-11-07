#!/bin/bash
# Single source of truth for per-SDK known-good branches, used by both
# setup-sdks.sh (local runs) and .github/workflows/cross-sdk.yml (CI).
# Edit branches here only — do not duplicate this map elsewhere.
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="feat/angular-sdk-complete"
  ["cpp-sdk"]="feat/initial-sdk"
  ["dotnet-sdk"]="feat/sdk-entry-point-rename"
  ["elixir-sdk"]="feat/initial-implementation"
  ["flutter-sdk"]="fix/audit-operator-fixes"
  ["go-sdk"]="fix/all-tests-passing"
  # java-sdk#17 (feat/custom-assignment-rules-v2) is the reference-aligned head for this round:
  # it is the only non-JS SDK that implements custom assignment rules and the semver_* operators,
  # so the java lane runs the "Custom Assignment Rules" and "Semver Operators" scenario groups for
  # real (expected-capabilities.json marks java required for assignmentRules/semverOperators).
  ["java-sdk"]="feat/custom-assignment-rules-v2"
  # javascript-sdk#67 (fix/assignment-rules-edge-cases) is the JavaScript reference for this round:
  # main (the TypeScript rewrite, custom assignment rules from #66, and the semver_* operators) plus
  # the four assignment-rule edge-case fixes of #67. tools/sdk-round-2026-11/compute.js records the
  # expected results from exactly this branch. The JS-family wrappers (react/vue2/vue3/angular) build
  # javascript-sdk from this pin too, so they inherit the same behaviour.
  ["javascript-sdk"]="fix/assignment-rules-edge-cases"
  ["kotlin-sdk"]="feat/public-hashing-object-take2"
  ["liquid-sdk"]="feat/comprehensive-test-coverage"
  ["php-sdk"]="fix/audit-operator-fixes"
  ["python3-sdk"]="fix/python-sdk-all-tests-passing"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="feat/ruby-3.3-compatibility"
  ["rust-sdk"]="fix/all-tests-passing"
  ["scala-sdk"]="chore/review-follow-ups"
  ["swift-sdk"]="feat/cross-sdk-tests-and-fixes"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
