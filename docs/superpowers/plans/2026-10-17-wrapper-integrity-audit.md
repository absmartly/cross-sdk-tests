# Wrapper Integrity Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**PR Title Format:** `fix(wrappers): exercise real SDK paths instead of wrapper-side substitutes`
**Base:** stacked on absmartly/cross-sdk-tests#12 (`feat/holdout-wrapper-capability`)

**Goal:** Remove every wrapper-side substitute that fakes behaviour the
conformance suite attributes to the SDK, so the suite reports real SDK
behaviour (including real SDK bugs).

**Architecture:** Each `*-wrapper/` is an HTTP adapter over one SDK. For each
audit finding: verify against the pinned SDK source (`sdk-branches.sh`), then
either (a) route the operation through the real SDK API, (b) withdraw the
`/capabilities` flag / mark the operation unsupported so the scenario is skipped
with a reason, or (c) let the scenario fail. Never re-mask an SDK gap. Every
SDK gap exposed is listed in the PR body as a needed SDK fix.

**Tech Stack:** Go, Dart/Flutter, Ruby (Liquid), PHP, C++, Scala, Elixir, Rust,
Java, .NET, Node (JS/Angular/React/Vue2/Vue3/TypeScript).

## Out of scope (spec-authorized, documented as coverage gaps only)

- WRAPPER_API_SPEC.md "Required error semantics": wrapper-side finalized checks,
  non-object track-properties validation, blank/already-set UID checks.
- Scenario 201 not-ready defaults.
- E2E forcing `publishDelay=-1` / `refreshPeriod=0`.
- Pass-through skips (excluded from tested totals).

## Tasks

### Priority 1 (highest confidence)
1. Liquid `hashUnit`: call Ruby SDK `Hashing.hash_unit`; check whether
   `passThroughOperations` hides diagnostics.
2. Go: delete `CustomVariableParser` and all `VariableParser_` assignments.
3. Go `publishFail`: arm a one-shot failure in the per-context publisher and
   call `Context.Publish`; update spec note if Go preserves pending.
4. Dart/Flutter: drop custom variable parser and `_normalizeContextData`.
5. PHP: withdraw `variableKeysMap` capability (SDK returns key→single exp).
6. C++: `customFieldValueType` via `Context::custom_field_value_type`.
7. Go: withdraw `readyError` capability (SDK has no ReadyError).

### Priority 2
8. `publishFail` in Scala, Elixir, C++, Rust: inject through the SDK publisher
   where possible; otherwise withdraw capability or let 193 fail.
9. Fabricated lifecycle events: Java, Scala, Elixir, Rust, .NET.
10. JS family: public `refresh()`; React/Vue variant capture; Vue SDK
    lifecycle; TypeScript diagnostic shims unsupported.

Anything that cannot be done correctly and verified in this PR is deferred and
listed in the PR body with the reason.

## Verification

Per changed wrapper: compile/syntax check with the native toolchain; where
cheap, run the wrapper locally against its pinned SDK and curl the affected
endpoint. CI runs the full Docker matrix.

## Closing

Mandatory review over the diff, commit, push, open stacked PR against
`feat/holdout-wrapper-capability`.
