#!/bin/bash
# Single source of truth for per-SDK known-good branches, used by both
# setup-sdks.sh (local runs) and .github/workflows/cross-sdk.yml (CI).
# Edit branches here only — do not duplicate this map elsewhere.
# 2026-11 system-attributes round (§5 of Márcio's brief): every SDK that
# evaluates audiences locally is pinned to its `feat/sdk-system-attributes`
# branch, which makes `application` + `environment` always-on attributes in the
# audience/rule evaluation map and drops `app_version`. These 14 branches are the
# Phase-1 reference-aligned heads (javascript-sdk#68 is the REFERENCE; java #22,
# python3 #13, dotnet #11, go #13, kotlin #3, php #9, ruby #40, swift #8,
# flutter #9, scala #3, rust #8, elixir #2, cpp #2). None are merged yet.
# tools/sdk-round-2026-11/compute.js records the expected results from the
# javascript-sdk branch (see PINNED_COMMIT in the generator). Each
# feat/sdk-system-attributes branch is a descendant of that SDK's prior
# cross-SDK-passing pin, so this round's audience/app_version behaviour is added
# on top of the behaviours earlier rounds already pinned — except php (see note).
declare -A SDK_BRANCHES=(
  ["angular-sdk"]="feat/angular-sdk-complete"
  ["cpp-sdk"]="feat/sdk-system-attributes"
  # dotnet-sdk#11 (feat/sdk-system-attributes) stacks the system-attributes change
  # on top of #8 (fix/audit-operator-fixes, the JSON custom-field fix for scenarios
  # 16, 54-57), so the dotnet lane keeps that fix and gains always-on
  # application/environment. The wrapper constructs the #8 entry point (class
  # ABsmartly, ABsmartlyConfig); ABSdk/ABSdkConfig remain as [Obsolete] aliases.
  ["dotnet-sdk"]="feat/sdk-system-attributes"
  ["elixir-sdk"]="feat/sdk-system-attributes"
  ["flutter-sdk"]="feat/sdk-system-attributes"
  ["go-sdk"]="feat/sdk-system-attributes"
  # java-sdk#22 (feat/sdk-system-attributes) stacks the system-attributes change
  # on top of #17's custom assignment rules + semver_* operators, so the java lane
  # still runs the "Custom Assignment Rules" and "Semver Operators" groups for real
  # (expected-capabilities.json marks java required for assignmentRules/semverOperators)
  # and now also the ungated "System Attributes" group.
  ["java-sdk"]="feat/sdk-system-attributes"
  # javascript-sdk#68 (feat/sdk-system-attributes) is the JavaScript reference for
  # this round: main (the TypeScript rewrite, custom assignment rules from #66, the
  # semver_* operators, and the four #67 edge-case fixes, all already on main) plus
  # the always-on application/environment audience attributes and app_version removal.
  # tools/sdk-round-2026-11/compute.js records the expected results from exactly this
  # branch (PINNED_COMMIT). The JS-family wrappers (react/vue2/vue3/angular) build
  # javascript-sdk from this pin too, so they inherit the same behaviour.
  ["javascript-sdk"]="feat/sdk-system-attributes"
  ["kotlin-sdk"]="feat/sdk-system-attributes"
  ["liquid-sdk"]="feat/comprehensive-test-coverage"
  # php-sdk#9 (feat/sdk-system-attributes) implements §5, and per the brief the php lane
  # is pinned to it so the group runs against the new code. BUT #9 branches off php-sdk
  # `main`, NOT off the prior cross-SDK pin fix/audit-operator-fixes, and php main uses a
  # different, synchronous SDK architecture (ContextEventHandler + a single synchronous
  # ContextDataProvider; no ContextPublisher / AsyncContextDataProvider /
  # createContextPending). The current php-wrapper is written against
  # fix/audit-operator-fixes (React async publisher and data-provider model), so it does
  # not even load against #9 ("Class ContextPublisher not found") and the php lane fails
  # to start. This is a php-sdk-side coordination issue (reported per §5 step 5): #9 must
  # be rebased onto fix/audit-operator-fixes, or the php-wrapper ported to main's
  # architecture (a separate, large task), before the php lane can run. See the PR.
  ["php-sdk"]="feat/sdk-system-attributes"
  ["python3-sdk"]="feat/sdk-system-attributes"
  ["react-sdk"]="feat/react-sdk-fixes-and-tests"
  ["ruby-sdk"]="feat/sdk-system-attributes"
  ["rust-sdk"]="feat/sdk-system-attributes"
  ["scala-sdk"]="feat/sdk-system-attributes"
  ["swift-sdk"]="feat/sdk-system-attributes"
  ["vue2-sdk"]="fix/all-tests-passing"
  ["vue3-sdk"]="fix/jest-config-vue3"
)
