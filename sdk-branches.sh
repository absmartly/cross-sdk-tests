#!/bin/bash
# Single source of truth for per-SDK known-good branches, used by both
# setup-sdks.sh (local runs) and .github/workflows/cross-sdk.yml (CI).
# Edit branches here only — do not duplicate this map elsewhere.
# 2026-11 feature-parity round: every non-JS SDK is pinned to its
# `feat/assignment-rules-semver-x-agent-publish` branch, which ports from
# javascript-sdk the custom assignment rules (Experiment.assignmentRules), the
# semver_* JSON-expression operators, the javascript-sdk#68 system attributes
# (always-on application/environment in audience/rule evaluation, no app_version)
# and the `X-Agent: absmartly-<lang>-sdk/<version>` request header. None are merged
# yet. javascript-sdk and java-sdk stay on feat/sdk-system-attributes
# (javascript-sdk#68 is the REFERENCE and contains main; java-sdk#22 stacks on #17's
# rules + semver). The JS-family wrappers (react/vue2/vue3/angular) build
# javascript-sdk from its pin; liquid wraps ruby-sdk. android-sdk has no wrapper.
# tools/sdk-round-2026-11/compute.js records the expected results from the
# javascript-sdk branch (see PINNED_COMMIT in the generator).
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="feat/angular-sdk-complete"
  ["cpp-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["dotnet-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["elixir-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["flutter-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["go-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["java-sdk"]="feat/sdk-system-attributes"
  ["javascript-sdk"]="feat/sdk-system-attributes"
  ["kotlin-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["liquid-sdk"]="feat/comprehensive-test-coverage"
  ["php-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["python3-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["rust-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["scala-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["swift-sdk"]="feat/assignment-rules-semver-x-agent-publish"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
