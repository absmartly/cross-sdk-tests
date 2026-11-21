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
# The JS-family wrappers (react/vue2/vue3/angular) build javascript-sdk from its
# pin; liquid wraps ruby-sdk. android-sdk has no wrapper.
# dotnet-sdk is on feat/holdouts (its open holdouts PR) until that PR merges, then
# back to main.
# cpp-sdk is pinned to feat/holdouts (the absmartly/cpp-sdk holdouts PR) while that
# PR is open; the pin goes back to `main` once it merges.
# dotnet-sdk is on feat/switchbacks (dotnet-sdk#16, stacked on the holdouts PR #15)
# until those PRs merge, then back to main.
# javascript-sdk is pinned to feat/switchbacks (javascript-sdk#69) only while that PR is
# open: repin it to main in the same change that merges #69, before the branch is deleted,
# or this lane's clone fails. The JS-family wrappers (react/vue2/vue3/angular) build javascript-sdk from its
# rust-sdk is pinned to feat/switchbacks (rust-sdk#12, stacked on the holdouts
# PR rust-sdk#11) for its switchback lane; the pin goes back to main once
# rust-sdk#12 merges.
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="main"
  ["cpp-sdk"]="feat/holdouts"
  ["dotnet-sdk"]="feat/switchbacks"
  # elixir-sdk#5 (holdouts, feat/holdouts) is open; re-pin to main once it merges.
  ["elixir-sdk"]="feat/holdouts"
  # Temporary: flutter-sdk#13 (switchbacks, stacked on #11 holdouts); back to main once both merge.
  ["flutter-sdk"]="feat/switchbacks"
  # go-sdk switchbacks PR (go-sdk feat/switchbacks, stacked on feat/holdouts) is
  # open; re-pin to main once both merge.
  ["go-sdk"]="feat/switchbacks"
  ["java-sdk"]="main"
  ["javascript-sdk"]="feat/switchbacks"
  ["kotlin-sdk"]="main"
  ["liquid-sdk"]="main"
  # php-sdk switchbacks PR (stacked on php-sdk#11, feat/holdouts) is open; re-pin to main once both merge.
  ["php-sdk"]="feat/switchbacks"
  # python3-sdk switchbacks PR (feat/switchbacks, stacked on python3-sdk#17's feat/holdouts) is
  # open; re-pin to main once both merge.
  ["python3-sdk"]="feat/switchbacks"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  # ruby-sdk switchbacks PR (feat/switchbacks, stacked on holdouts ruby-sdk#43) is open;
  # re-pin to main once both merge.
  ["ruby-sdk"]="feat/switchbacks"
  ["rust-sdk"]="feat/switchbacks"
  ["scala-sdk"]="main"
  # swift-sdk holdouts PR (swift-sdk feat/holdouts) is open; re-pin to main once it merges.
  ["swift-sdk"]="feat/holdouts"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
