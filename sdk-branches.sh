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
# swift-sdk 2.0 (swift-sdk#13) is on feat/swift-2.0 until it merges; then re-pin "swift-sdk"
# to main. "swift-sdk-v1" is the 1.x lane (the swift-v1 wrapper) and clones absmartly/swift-sdk into
# swift-sdk-v1/. It is pinned to release/1.x (cut from the v1.5.0 tag), never main, because main
# becomes 2.0 when swift-sdk#13 merges. A value of 40 hex digits is a commit SHA; anything else is
# a branch or tag.
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="main"
  ["cpp-sdk"]="main"
  ["dotnet-sdk"]="main"
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
  ["swift-sdk"]="feat/swift-2.0"
  ["swift-sdk-v1"]="release/1.x"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)

# Checkout directories that are not repository names: <dir> -> absmartly/<repo>. The swift-v1 lane
# builds swift-sdk 1.x from swift-sdk-v1/ next to the 2.x checkout in swift-sdk/.
declare -A SDK_REPOS=(
  ["swift-sdk-v1"]="swift-sdk"
)

# Clones absmartly/<repo> (mapped through SDK_REPOS) at <ref> into ./<dir>. <ref> is a branch, a
# tag, or a full commit SHA, which `git clone --branch` cannot take.
clone_sdk() {
  local dir="$1" ref="$2"
  local url="https://github.com/absmartly/${SDK_REPOS[$dir]:-$dir}.git"
  if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
    git init -q "$dir" && git -C "$dir" remote add origin "$url" \
      && git -C "$dir" fetch -q --depth 1 origin "$ref" && git -C "$dir" checkout -q FETCH_HEAD
  else
    git clone -q --depth 1 --branch "$ref" "$url" "$dir"
  fi
}
