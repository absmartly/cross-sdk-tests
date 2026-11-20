#!/bin/bash
# Single source of truth for per-SDK known-good branches, used by both
# setup-sdks.sh (local runs) and .github/workflows/cross-sdk.yml (CI).
# Edit branches here only — do not duplicate this map elsewhere.
# 2026-11 feature-parity round (cross-sdk-tests#21): every SDK ports from
# javascript-sdk the custom assignment rules (Experiment.assignmentRules), the
# semver_* JSON-expression operators, the javascript-sdk#68 system attributes
# (always-on application/environment in audience/rule evaluation, no app_version)
# and the `X-Agent: absmartly-<lang>-sdk/<version>` request header. Every parity
# PR is merged, so those SDKs are pinned to `main`; java-sdk main also carries
# switchback (java-sdk#15). javascript-sdk main has the same tree as PINNED_COMMIT
# in generate_sdk_round_2026_11_scenarios.py, the reference compute.js records.
# react-sdk, vue2-sdk and vue3-sdk stay on their open PR branches (react-sdk#31,
# vue2-sdk#17, vue3-sdk#13), which carry the wrapper fixes their lanes need.
# dotnet-sdk is pinned to fix/variable-strict-audience-fallback (dotnet-sdk 1.4.1) until it merges: the
# Shared Variable Key group (340-368) requires sharedVariableKeys for dotnet, and main (1.4.0) leaks
# the first candidate's control value when no candidate is assigned.
# The JS-family wrappers (react/vue2/vue3/angular) build javascript-sdk from its
# pin; liquid wraps ruby-sdk. android-sdk has no wrapper.
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="main"
  ["cpp-sdk"]="main"
  ["dotnet-sdk"]="fix/variable-strict-audience-fallback"
  ["elixir-sdk"]="main"
  ["flutter-sdk"]="main"
  ["go-sdk"]="main"
  ["java-sdk"]="main"
  ["javascript-sdk"]="main"
  ["kotlin-sdk"]="main"
  ["liquid-sdk"]="main"
  ["php-sdk"]="main"
  ["python3-sdk"]="main"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="main"
  ["rust-sdk"]="main"
  ["scala-sdk"]="main"
  ["swift-sdk"]="main"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)

# Stacked conformance dependency: absmartly/dotnet-sdk#17. Do not merge it for this
# suite; build the approved immutable head while the SDK PR remains open.
# SDK_BRANCHES names the branch to clone; SDK_COMMITS optionally pins its HEAD.
declare -A SDK_COMMITS=(
  ["dotnet-sdk"]="82809280c96563ef6c1e7adbc1c1876c9798f554"
)
