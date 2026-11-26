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
#   cpp-sdk        feat/switchbacks  cpp-sdk#6 (stacked on #5 holdouts), at the SDK_REVISIONS commit
#   dotnet-sdk     feat/switchbacks  dotnet-sdk#16 (stacked on #15 holdouts); contains #17 (1.4.1)
#   elixir-sdk     feat/switchbacks  elixir-sdk#6 (stacked on #5 holdouts)
#   flutter-sdk    feat/switchbacks  flutter-sdk#13 (on #11 holdouts)
#   go-sdk         feat/switchbacks  go-sdk#17 (on #15 holdouts)
#   javascript-sdk main @ 95fe105    javascript-sdk#69 MERGED; the SDK_REVISIONS commit is its merge commit. It
#                                    carries the java-sdk shared-variable-key rule, so the JS-family lanes
#                                    require `sharedVariableKeys` (340-368).
#   php-sdk        feat/switchbacks  php-sdk#13 (on #11 holdouts)
#   python3-sdk    feat/switchbacks  python3-sdk#18 (on #17 holdouts)
#   ruby-sdk       feat/switchbacks  ruby-sdk#47 (holdouts ruby-sdk#43 merged)
#   rust-sdk       feat/switchbacks  rust-sdk#12 (holdouts rust-sdk#11 merged)
#   swift-sdk      main @ d58e2f17 (swift-sdk#13 merged; 2.0 now on main). The `swift` lane runs the 2.x wrapper.
#   swift-sdk-v1   main @ f64883c    swift-sdk#12 (holdouts, on the 1.5.0 line) MERGED: the SDK_REVISIONS
#                                    commit is its merge commit on main. The `swift-v1` lane
#                                    (swift-v1-wrapper, cloned from absmartly/swift-sdk) requires holdouts.
#                                    swift-sdk main is now 2.0; keep the exact 1.x commit until release/1.x is cut,
#                                    then re-pin the v1 lane to it.
# Not every switchbacks branch contains its holdouts PR's latest head: cpp, elixir and dotnet
# feat/switchbacks are behind their feat/holdouts, so those lanes test switchback over an older
# holdouts port. Merge holdouts into switchbacks in those SDK repos and re-run the lanes before merge.
# SDK_REVISIONS pins a repo to an exact commit, so a later push to its branch cannot change
# what this commit tests.
declare -A SDK_REVISIONS=(
  ["cpp-sdk"]="52125e0c02b817f92818038fd052497c30866f1a"
  ["javascript-sdk"]="95fe105b3a1855b2bba37171c2d4cdd212ab8c4f"
  ["swift-sdk-v1"]="f64883ceb755aaf94443465f0d4d2da6555d8a7a"
  ["swift-sdk"]="d58e2f17eb2e1e920b2067ae0db6f7b7ae0d71fd"
)

# Checkout directories that are not repository names: the directory a wrapper's Dockerfile COPYs, mapped to the
# absmartly/<repo> it is cloned from. Directories not listed are cloned from the repo of the same name.
declare -A SDK_REPO_SOURCES=(
  ["java-sdk-v1"]="java-sdk"
  ["swift-sdk-v1"]="swift-sdk"
)
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="main"
  ["cpp-sdk"]="feat/switchbacks"
  ["dotnet-sdk"]="feat/switchbacks"
  ["elixir-sdk"]="feat/switchbacks"
  ["flutter-sdk"]="feat/switchbacks"
  ["go-sdk"]="feat/switchbacks-v1"
  # java-sdk 2.0 (the open java-sdk next/major -> main PR): the `java` lane runs the 2.x wrapper on
  # next/major until it merges, then on main.
  ["java-sdk"]="next/major"
  # The 1.x line, for the `java-v1` lane (java-v1-wrapper): release/1.x once the owner cuts it, until then the
  # last 1.x tag. v1.9.0 predates switchback (on main since java-sdk#15), so java-v1 lists it as unsupported.
  ["java-sdk-v1"]="v1.9.0"
  ["javascript-sdk"]="main"
  ["kotlin-sdk"]="main"
  ["liquid-sdk"]="main"
  ["php-sdk"]="feat/switchbacks"
  ["python3-sdk"]="feat/switchbacks"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="feat/switchbacks"
  ["rust-sdk"]="feat/switchbacks"
  ["scala-sdk"]="main"
  ["swift-sdk"]="main"
  ["swift-sdk-v1"]="main"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
