# Shared variable key conformance group — work report

Branch: `feat/shared-variable-key-conformance` (from main 4734dcc, 229 scenarios).

## Status
- Completed: real JS SDK reference helper (npm 1.13.4 pinned + lockfile), generator, 16 ungated scenarios 230–245, --check, byte-identical re-run, independent replay / mutants / digest / numbering tests, README and CI.
- Required wrappers: JavaScript, Python and Ruby pass all 16 new scenarios. Full-suite results below.
- Completed: required and optional Go/PHP wrapper runs, final review. Swift not run (optional; avoided another large toolchain build). Cleanup recorded below.

## Design and numbering
- Every expectation is recorded by `tools/shared-variable-keys/compute.js` from the real npm `@absmartly/javascript-sdk@1.13.4`; only nondeterministic exposure timestamps are dropped, and ready data is projected to id/name. Every step explicitly pins result/events, including exposure order and all stable exposure fields.
- Splits [0,1] make treatment B deterministic for any unit/seeds (no probabilistic seed search). The unit is `svk-unit-1`; missing-unit experiments use `user_id`, which is never set. Traffic-ineligible experiments are **assigned:true**, eligible:false, variant A in JS, not unassigned. Hence actual unassignment uses missing units and strict audience mismatch, not trafficSplit alone.
- No holdouts, new endpoint, `requires`, wrapper changes or SDK changes.
- Main's last scenario was 229, so this group is 230–245. PR #13 independently appends Switchback 230–259. **Whichever merges second renumbers**, regenerates its group and updates counts/ranges and its non-group digest. No #13 files were added/modified.

## Scenarios and discrimination
Each has two distinct keys/values: the focal color rule and a size fall-through probe. Values are unique per experiment/variant; the auxiliary probe makes even the both-assigned baseline distinguish first-only/last-only models. An unassigned SIZE guard ensures every scenario discriminates stopping on unassigned / ignoring assignment.

| Base | Peek twin | What it pins |
|---|---|---|
| 230 | 238 | Both assigned, sorted payload: color returns 11 B, exposing only 11; size skips unassigned 10 and 11 B lacking size to 12 B, exposing [10,12]. |
| 231 | 239 | Identical eligibility/config shape in reversed payload: still returns lowest id, exposure order by id. |
| 232 | 240 | Missing-unit first experiment 21 unassigned, both its variants define color: next 22 wins, exposures [21 assigned:false,22]. Size skips 22's served B to 23. |
| 233 | 241 | Strict-audience mismatch first experiment 31 unassigned; next 32 wins, exposures [31 assigned:false audienceMismatch:true,32]. Size falls through 32 to 33. |
| 234 | 242 | Traffic-ineligible 41 IS assigned, returns its A color value over 42; size falls through 40 guard and 41 A lacking size to 42. |
| 235 | 243 | Override B on missing-unit experiment 51 returns its value, assigned:false overridden:true; size still checks the overridden config's keys, skips guard 50 and 51 to 52. |
| 236 | 244 | Assigned 61 B lacks color (only A defines it), falls through to 62, exposes both. Size skips guard 60 and returns 61 B. |
| 237 | 245 | Color is defined **only** by unassigned 71 and 72: default with [71 missing unit,72 strict mismatch] exposed in order. Size visits 73 assigned B null config, falls through to 74, exposes [73,74]. |

238–245 do peek for both keys, assert events:[] and pending 0, then variableValue on the same context (same values, full exposures), then pending count. All scenarios replay every read/value/exposure under each wrong model, preserving de-duplication across steps.

All 16 kill: last-in-payload, first-in-payload, lowest-id-only, no-fallthrough-on-unassigned, first-assigned-without-key-check, expose-only-the-winner, ignore-assigned. Payload-order-walk is additionally killed by every unsorted-payload scenario (14 of 16); on 230/238's intentionally sorted baseline, a payload walk is identical to the correct id walk, so it is explicitly excluded there. The five requested wrong models are killed by **every** scenario.

## Commands and verification
- Read scenarios 23/25/43/48/119/228 and `orchestrator/test_runner.py`: event data subset matching, strict list length/order, bool≠int, every response must have events; unlisted events mean none. Read the Switchback generator on remote branch as model, and holdout payload style.
- `npm ci --prefix tools/shared-variable-keys --ignore-scripts` installs the pinned oracle.
- `python3 generate_shared_variable_key_scenarios.py` twice: identical final JSON sha256 `60542e545e388fd6ac6d19f30ecd90a4bd5f4bc6522453d5612f5e921571cfda`.
- `python3 generate_shared_variable_key_scenarios.py --check`: up to date, 16 scenarios.
- `/var/tmp/xsk/venv/bin/python -m pytest tests orchestrator/tests -q`: **116 passed, 142 subtests passed**.
- Non-group digest: sha256(json.dumps(kept, sort_keys=True)) = `8ddb8e702cf86ddf9346560071e8ba75ca6b58901ad90ebe8f2acb9ac4b25e86` (229 scenarios). Canonical diff is an append only.

### Deliberately broken inputs (all outside repo in /var/tmp/xsk/broken)
- M1 generator returns same value for every experiment/variant; regenerate: mutant replay fails (2 subtests), correct generator --check rejects it.
- M2 generator guard defines no key; regenerate: mutant replay fails (26 subtests), including expose-only-winner and stop-on-unassigned.
- M3 hand-edit scenario 230 color expected to higher id's value: JS-rule replay fails (1 subtest), --check rejects it.
- M4 hand-edit old scenario 23 expected result: non-group digest test fails. --check correctly passes because it owns only the new group; digest guards old scenarios.
- M5 rename 236 to 237 (duplicate/gap): contiguous-numbering test fails; --check rejects it.
- M6 move group scenario 230 into the middle: contiguous-numbering/group-tail test fails.
- Command used: `SVK_SCENARIOS_PATH=<broken-json> /var/tmp/xsk/venv/bin/python -m pytest orchestrator/tests/test_shared_variable_key_scenarios.py -q`. Broken generators were copied to /var/tmp with helper and pinned SDK so regeneration used the real JS SDK, not hand-written answers.

## CI and docs
- `harness-unit-tests` retains `unittest discover -s tests`, adds `pytest orchestrator/tests`, Node 22 + npm ci + generator --check.
- README count 245, range 230–245, JS reference rule, generation note and harness instructions. WRAPPER_API_SPEC unchanged: no endpoint added.

## Wrapper integrity inspection (before trusting passes)
Grep/read handlers: JS `javascript-wrapper/server.js:362/379`, Python `python-wrapper/server.py:418/435`, Ruby `ruby-wrapper/server.rb:317/318` and `with_context:150`, Go `go-wrapper/server.go:615/623`, PHP `php-wrapper/server.php:658/681`, Swift `swift-wrapper/Sources/App/routes.swift:635/644`. All call the SDK variableValue/peek method directly and return its value + actual event-window; no shared-key resolution, config merging or short-circuit translation in wrappers.

## SDK runs
Cloned exact requested branches in `/var/tmp/xsk/ctx`; built unmodified repo wrapper Dockerfiles with that directory as context, repo copied under `cross-sdk-tests/`. Docker is backed by Podman here. Used an `xsk-net` network with SDK service aliases and the repo orchestrator Dockerfile, canonical JSON mounted read-only.

First host-port run was invalid for Python/Ruby: SDK live payload fetches were handed host-local ports not reachable inside their containers. Discarded those results and reran on a shared network. JS had passed all 245 even in that first run.

Valid full-network command: `docker run --rm --network xsk-net -e SDK_SERVICES=javascript,python,ruby -v "$PWD/test_scenarios_complete.json:/test_scenarios_complete.json:ro" -v /var/tmp/xsk/res:/results xsk-orchestrator` (tee log). Harness drives these scenarios through real live fetch, despite createContextWith in scenario action (existing harness policy); the oracle uses literal createContextWith.

| SDK | Requested source branch / SHA | New group | Full 245 suite (pass/fail/skip) |
|---|---|---|---|
| javascript | main / 7fbfa80 | 16/16 pass | 245 / 0 / 0 |
| python | fix/python-sdk-all-tests-passing / 4758d5a | 16/16 pass | 206 / 15 / 24 |
| ruby | feat/ruby-3.3-compatibility / 1c34745 | 16/16 pass | 215 / 5 / 25 |

All new observations agree cross-SDK. Full run exits nonzero from pre-existing failures/consistency (not the group): Python finalize emits extra `close` event (19,131,147–149,186,188–192,228), duplicate-unit error wording (35), invalid track properties accepted (39), failed-context refresh emits error (229). Ruby duplicate-unit error wording (35), diagnostics TypeUtils missing (127/128), variableKeysMap gives strings instead of arrays (194/202). These were recorded, not fixed. Reports/logs under `/var/tmp/xsk/res/` pending final summary.

## Optional SDK evidence and final verification
- Go `fix/all-tests-passing` / da993b0: **0/16 pass, all 230–245 fail**. Full suite 185 pass / 31 fail / 29 skip (15 old failures).
- PHP `fix/audit-operator-fixes` / 6ed079a: **0/16 pass, all 230–245 fail**. Full suite 183 pass / 36 fail / 26 skip (20 old failures).
- Swift branch was cloned (f08521b) and wrapper handlers inspected, but no image built/run (optional).
- Go/PHP full run used the same shared-network orchestrator command with SDK_SERVICES=go,php. Full reports were saved as `jpr-network.json` and `gp-network.json` in scratch.
- Final review made (g) literal: color exists only in unassigned experiments; the assigned null-config check is on size. Regenerated, never hand-edited JSON. Re-ran the final 16 scenarios against **all five wrappers** on the shared network: JS/Python/Ruby 16/16 each, Go/PHP 0/16 each, zero group skips. Full-suite totals above are unchanged by this refinement.
- Added oracle cross-check: for every variableValue/peekVariableValue call, the SDK's actual `_exposures` queue delta must equal its logged exposure events (timestamps stripped). Thus expected events prove the queued list, not just callbacks.
- Tightened mutant assertions to compare read steps only (value AND exposure list); pending differences alone cannot kill a mutant.

| Scenarios (peek twins same issue) | Go observed wrong behaviour | PHP observed wrong behaviour |
|---|---|---|
| 230/238 | size returns unassigned guard 10 A; one-experiment index | color returns higher id 12 B; size drops guard/visited exposure |
| 231/239 | color returns payload-first 12 B, only exposes it | size returns payload-last guard 10 A |
| 232/240 | color value right but omits 21 exposure; size defaults at 22 B | color returns payload-last 21 A (also incorrectly fullOn/assigned for missing unit) |
| 233/241 | color value right but omits strict-mismatch 31 exposure; size defaults at 32 B | variableValue color defaults at 31; peek color incorrectly serves 31 A |
| 234/242 | color returns 42 B instead of ineligible 41 A; size only exposes 42 | size defaults at 41 A lacking size; misses guard/42 exposures |
| 235/243 | color returns 52 B instead of overridden 51 B; omits visited exposures | size returns guard 50 A, not 52 B |
| 236/244 | color value right but skips exposure 61; size omits guard 60 | color defaults at assigned 61 B lacking key; size returns guard 60 A |
| 237/245 | color serves strict-unassigned 72 A; size exposes only 74 | color serves missing-unit 71 A; size defaults at 73 B lacking key |

No SDKs or wrappers were fixed: these failures are expected conformance evidence. Other pre-existing failures remain outside scope. JSON reports retain full step observations in scratch; the table records the evidence needed after scratch removal.

## Final checks and cleanup
- Final `npm ci --prefix tools/shared-variable-keys --ignore-scripts`: 7 packages, 0 vulnerabilities.
- Final generator run twice + --check: identical hash above; 16 scenarios up to date.
- Final `python3 -m pytest tests orchestrator/tests` (scratch venv Python): **116 passed, 142 subtests passed**. `git diff --check` clean.
- Removed every created container (`xsk-{javascript,python,ruby,go,php}-c`), all six built images, all pulled base images and intermediate build layers; removed `xsk-net`. Final `docker ps -a` and `docker images -a` empty; only pre-existing default `podman` network remains.
- No open implementation issues. Unrelated old SDK/wrapper failures above remain deliberately unfixed; optional Swift was not run.
- Work committed on the work branch; no PR opened or push performed.
