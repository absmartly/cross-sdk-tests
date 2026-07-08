# Cross-SDK Tests — Remaining Review-Loop Work

**Date:** 2026-07-08 · **Branch:** `feat/kotlin-wrapper` (all changes UNCOMMITTED in the working tree; some deletions staged)
**Goal:** finish the iterative fix-review loop. Stop condition: **two consecutive review rounds with zero findings.**

## State so far (context — do not redo)

Three rounds of deep review + fixes are already applied to the working tree. Everything below is DONE and verified (static review + per-language syntax/build checks):

- Orchestrator (`orchestrator/test_runner.py`, `e2e_runner.py`, `results_aggregator.py`): vacuous error scenarios fixed (expected-error + HTTP 200 now fails), down-SDK KeyError fixed, 0/0→"NO TESTS RUN" fails, capabilities-fetch failures fail loudly, cross-SDK consistency check added, aggregator honors failure flags (DOWN/CAPS FAIL/NO TESTS RUN/INCONSISTENT) and gates run-all-tests.sh via AGG_EXIT, e2e fabricated-pass removed (UNVERIFIED status + aggregate gate), segmentation-trust sanity checks, per-SDK error counts fail runs, planned-volume expectations, env resolved once (`self.environment`, default "production").
- Shell: port-race poll loops with hard fail, `verify_sdk.sh` gates on `generate_report()` and prints DOWN, pip PEP-668 handling, dead scripts deleted (`run-individual-tests.sh`, `run-all-individual.sh`, `docker-compose.matrix.yml`, `test_kotlin_local.py`, `README_EXECUTION.md`).
- CI (`.github/workflows/cross-sdk.yml`): new `cross-sdk-consistency` job (javascript+python+go together), `--loose-error-match` removed.
- Wrappers (all 21): readyError = `{"isError":true,"message":...}` everywhere; deferred/payloadThrottle data-provider failures now FAIL the context in every wrapper (js×6 `.catch(reject)` + `r.ok` check, dart `completeError`, flutter `fetchFailed`, python `set_exception`, ruby+liquid `FailingDataWrapper`, swift `seal.reject`×3, java `completeExceptionally`, go `done(…, err)`, kotlin failing provider, cpp `set_exception`; rust was already correct); treatment-after-finalize guards → 400 "Context finalized" on java/kotlin/python/dart/flutter/rust/ruby/liquid/php/scala/cpp (js-family/go/dotnet/swift/elixir throw naturally or had guards); goal-properties object validation on ruby/liquid/php/scala/cpp/elixir; elixir error tuples → 4xx; dotnet publishFail real + SDK-backed getUnit/getAttribute; rust/kotlin publish awaited; e2e forces publishDelay=-1/refreshPeriod=0 (dart/flutter use 999999999 — dart Timer fires immediately on negative); go panic→500; swift run-tests.sh rc-124 anchored on `Test Suite 'All tests' passed`.
- Docs: README rewritten (21 SDKs, 202 scenarios, real entry points, CI incl. consistency job); WRAPPER_API_SPEC.md fixed (globalCustomFieldKeys, .io endpoints, no `code` field, e2e forcing bullet, publishFail "arms" wording, **new "Required error semantics" subsection**); generator writes `test_scenarios_generated.json` (gitignored) with stale warnings; hygiene clean (no tracked junk, no secrets — verified `git log -S`).

**Round-3 results so far:** orchestrator NO FINDINGS · wrappers NO FINDINGS · docs 1 finding (already fixed: spec error-semantics section). **Round 3 is incomplete** — the two reviews below died on API rate limits. Round 3 already has ≥1 finding, so the clean-streak is 0 regardless; rounds 4 and 5 must both be clean to stop.

## TASK 1 — Finish round 3: silent-failure hunt

Sweep the whole tree for verdict-affecting silent failures (fail→pass flips, vanishing errors). Re-audit the round-2/3 fix sites listed above for completeness/regressions. Check every except/rescue/catch in orchestrator + wrappers, shell exit codes, JSON-parse fallbacks, CI failure propagation.

- HIGH bar: only real, triggerable, verdict-affecting issues.
- Do NOT report accepted debt (below) or known judgment calls: e2e mid-run 501→skipped (e2e_runner.py ~491); aggregator missing-cross-report N/A (results_aggregator.py ~582).
- Output: severity, file:line, what's swallowed, consequence, fix — or "NO FINDINGS".

## TASK 2 — Finish round 3: error-scenario × wrapper sweep

The orchestrator now fails any expected-error step that returns HTTP 200. Verify the guard rollout is COMPLETE:

1. Enumerate every scenario in `test_scenarios_complete.json` whose `expect` contains `error`/`errorContains` (at least: 34, 35, 37, 38, 39, 147, 148, 149, 188, 189 — confirm the full list with grep/jq).
2. For EACH of the 21 wrappers × each error scenario: confirm the wrapper actually returns an error (SDK throws naturally OR wrapper guard exists) whose message passes strict `error_matches` (`orchestrator/test_runner.py` ~571-604: containment either direction, or ≥2-keyword subset).
3. Confirm guards don't break scenario 201 (not-ready treatment → default 0, HTTP 200) or scenario 190 (override-after-finalize ALLOWED, returns 200).
4. Report any wrapper × scenario cell that would return 200 or a non-matching message, with file:line + fix.

## TASK 3 — Runtime validation (the biggest gap — nothing has actually RUN yet)

All verification so far is static. Execute the suite for real:

```bash
cd /Users/joalves/git_tree/sdks/cross-sdk-tests
./run-tests.sh --sdk javascript   # then python, then go — or one combined run if supported
```

- The new CI consistency job runs exactly javascript+python+go; make those three pass locally FIRST (this validates the strict-error path, the 11 finalized guards, and the goal-properties validation end-to-end).
- If time permits, run all 21 (`./run-all-tests.sh`) — memory says all 21 previously passed 202/202, and rounds 1-3 changed wrapper behavior, so regressions are possible.
- Watch specifically: scenarios 34/35/37/38/39/147/148/149/188/189 (must now genuinely error), 201 and 190 (must still pass), 67/184 (deferred providers must still go ready on the happy path), 200 (failLoad → readyError).
- Known infra gotchas: filtered `--sdk` runs previously had a compose-port race (now fixed with poll loops — if a port still fails, that's a real finding); wrappers build against sibling SDK repos via `..` build context (e.g. `../javascript-sdk`, `../python3-sdk`, `../go-sdk` must exist).
- Any failure here = a finding: fix the wrapper/orchestrator (NOT the scenario, unless the expectation is genuinely wrong — e.g. if a treatment-after-finalize SDK behavior is intended, say so and discuss).

## TASK 4 — Loop protocol until done

1. Fix all findings from Tasks 1-3.
2. Run a full review round: 5 parallel reviews (orchestrator/scripts · 21 wrappers · silent failures · coverage · docs+hygiene) over the CURRENT working tree, same scopes as above, high bar, "NO FINDINGS" allowed.
3. If ANY finding → fix → new round. If ZERO findings → run ONE more full round. Two consecutive clean rounds → STOP and summarize.
4. Syntax gates to run after every fix batch: `python3 -m py_compile orchestrator/*.py` · `bash -n run-*.sh verify_sdk.sh swift-wrapper/run-tests.sh` · `node --check` on the 6 JS wrappers · `ruby -c` ruby+liquid · `php -l` · `dart analyze` dart+flutter · `go build ./...` (needs `replace` to `../go-sdk`) · `cargo check` in rust-wrapper · cpp: `cmake --build cpp-wrapper/build` · elixir: `mix compile` in elixir-wrapper. JVM (java/kotlin/scala) and swift builds only resolve in Docker — careful review is acceptable, say so.

## Accepted debt — do NOT fix, do NOT re-report (follow-up tickets)

1. No harness unit tests (pytest for validate_step / values_match / error_matches / consistency voting / aggregator parsers) — highest-value ticket.
2. No per-SDK expected-capabilities pinning (a wrapper can shed scenarios by under-declaring `/capabilities`).
3. Scenarios 68-74 named "retry/timeout/abort" but inject no faults (rename or add `failTimes=N` to the payload endpoint).
4. `generate_scenarios.py` is stale (192 vs 202, drifted content) — protected but not reconciled.
5. Thin coverage: mid-range traffic splits, multi-experiment contexts, unicode on the real assignment path, concurrency, publishDelay>0 batching.

## Rules

- Never commit — leave everything uncommitted (deletions already staged are fine).
- Don't touch sibling SDK repos (`../javascript-sdk` etc.) — wrapper/orchestrator fixes only.
- `test_scenarios_complete.json` is the canonical oracle — don't regenerate it.
- When a fix conflicts with a scenario expectation, the scenario wins unless it's provably wrong; flag disagreements instead of changing expectations silently.
