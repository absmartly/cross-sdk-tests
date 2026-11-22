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
#
# Holdouts / switchbacks / shared-variable-key round (one consolidated cross-sdk-tests PR):
# every lane that now requires `holdouts`/`holdout_arms` and `switchback` builds its SDK's
# open switchback PR branch (stacked on, or containing, that SDK's holdouts PR). Re-pin each
# to `main` in the change that merges its SDK PR(s), before the branch is deleted, or the
# lane's clone fails:
#   cpp-sdk        feat/switchbacks  cpp-sdk#6 (on #5 holdouts), at the SDK_REVISIONS commit
#   dotnet-sdk     feat/switchbacks  dotnet-sdk#16 (on #15 holdouts); contains #17 (1.4.1)
#   elixir-sdk     feat/switchbacks  elixir-sdk#6 (on #5 holdouts)
#   flutter-sdk    feat/switchbacks  flutter-sdk#13 (on #11 holdouts)
#   go-sdk         feat/switchbacks  go-sdk#17 (on #15 holdouts)
#   javascript-sdk feat/switchbacks  javascript-sdk#69 (holdouts already on main)
#   php-sdk        feat/switchbacks  php-sdk#13 (on #11 holdouts)
#   python3-sdk    feat/switchbacks  python3-sdk#18 (on #17 holdouts)
#   ruby-sdk       feat/switchbacks  ruby-sdk#47 (holdouts ruby-sdk#43 merged)
#   rust-sdk       feat/switchbacks  rust-sdk#12 (holdouts rust-sdk#11 merged)
#   swift-sdk      feat/holdouts     swift-sdk#12 (its lane requires holdouts only)
# SDK_REVISIONS pins a repo to an exact commit, so a later push to its branch cannot change
# what this commit tests.
declare -A SDK_REVISIONS=(
  ["cpp-sdk"]="45ed81223e594d491ec74dc9529e055ea04e6606"
)
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="main"
  ["cpp-sdk"]="feat/switchbacks"
  ["dotnet-sdk"]="feat/switchbacks"
  ["elixir-sdk"]="feat/switchbacks"
  ["flutter-sdk"]="feat/switchbacks"
  ["go-sdk"]="feat/switchbacks"
  ["java-sdk"]="main"
  ["javascript-sdk"]="feat/switchbacks"
  ["kotlin-sdk"]="main"
  ["liquid-sdk"]="main"
  ["php-sdk"]="feat/switchbacks"
  ["python3-sdk"]="feat/switchbacks"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="feat/switchbacks"
  ["rust-sdk"]="feat/switchbacks"
  ["scala-sdk"]="main"
  ["swift-sdk"]="feat/holdouts"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
