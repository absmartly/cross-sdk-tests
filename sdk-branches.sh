#!/bin/bash
# Single source of truth for per-SDK known-good branches, used by both
# setup-sdks.sh (local runs) and .github/workflows/cross-sdk.yml (CI).
# Edit branches here only — do not duplicate this map elsewhere.
# 2026-11 feature-parity round (cross-sdk-tests#21): every SDK ports from
# javascript-sdk the custom assignment rules (Experiment.assignmentRules), the
# semver_* JSON-expression operators, the javascript-sdk#68 system attributes
# (always-on application/environment in audience/rule evaluation, no app_version)
# and the `X-Agent: absmartly-<lang>-sdk/<version>` request header.
# Merged SDKs are pinned to `main` (each main has the same tree as the PR head
# CI tested). The SDKs whose PR is still open stay on their PR branch until it
# merges; docs/sdk-round-2026-11-conformance.md ("Branch pins and merge order")
# lists them and the follow-up that re-pins them to `main`.
# javascript-sdk#68 is the REFERENCE: tools/sdk-round-2026-11/compute.js records
# the expected results from its pinned commit (PINNED_COMMIT in the generator).
# The JS-family wrappers (react/vue2/vue3/angular) build javascript-sdk from its
# pin; liquid wraps ruby-sdk. android-sdk has no wrapper.
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="feat/angular-sdk-complete"
  ["cpp-sdk"]="main"
  ["dotnet-sdk"]="main"
  ["elixir-sdk"]="main"
  ["flutter-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["go-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["java-sdk"]="main"
  ["javascript-sdk"]="feat/sdk-system-attributes"
  ["kotlin-sdk"]="main"
  ["liquid-sdk"]="feat/comprehensive-test-coverage"
  ["php-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  # python3-sdk holdouts PR (feat/holdouts) is open; re-pin to main once it merges.
  ["python3-sdk"]="feat/holdouts"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="main"
  ["rust-sdk"]="main"
  ["scala-sdk"]="main"
  ["swift-sdk"]="main"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
