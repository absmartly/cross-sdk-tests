> **Historical — describes the original design; see code for current behavior.**

# E2E Test Plan — ABsmartly SDKs

## Overview

E2E tests validate the full round-trip: SDK → collector → analysis pipeline → metrics API.
Unit and cross-SDK tests already cover local correctness (hashing, assignment, audience evaluation).
E2E tests should focus on features that involve **server communication**.

## Current Coverage

The existing e2e runner tests:
- Context creation (real API)
- Treatment assignment (exposure generated)
- Goal tracking (`purchase` with `amount` property)
- Publish (data reaches collector)
- Metrics verification (participant count via `abs experiments metrics results`)
- Experiment lifecycle (create → start → stop → archive)

---

## Test Categories

### 1. Exposure Tracking

| Test | Description | Verification |
|------|-------------|--------------|
| 1.1 Basic exposure | `treatment()` generates exposure | `unit_count` in metrics |
| 1.2 Peek no exposure | `peek()` does NOT generate exposure | `unit_count` should be 0 for peek-only units |
| 1.3 Exposure after peek | `peek()` then `treatment()` generates exactly 1 exposure | compare peek-only vs peek+treat counts |
| 1.4 Deduplicated exposure | Multiple `treatment()` calls for same experiment generate 1 exposure | unit_count matches unique users, not calls |
| 1.5 Multiple experiments | Treat 2+ experiments in one context, each gets independent exposures | per-experiment metrics |

### 2. Goal Tracking

| Test | Description | Verification |
|------|-------------|--------------|
| 2.1 Basic goal | `track("purchase", {amount: 10})` | goal count in metrics |
| 2.2 Multiple goals | Track `purchase`, `signup`, `click` in same context | each goal's metric independently |
| 2.3 Goal before treatment | `track()` before `treatment()` — goal should still be published | goal count > 0 |
| 2.4 Goal properties | Track with numeric properties, verify they reach the server | revenue/sum metric |
| 2.5 Multiple tracks same goal | `track("purchase")` called 3x in one context | count reflects 3 |

### 3. Publishing & Finalization

| Test | Description | Verification |
|------|-------------|--------------|
| 3.1 Explicit publish | `publish()` sends pending data | events visible via `abs events list` |
| 3.2 Finalize publishes | `finalize()` flushes all pending data | same as publish |
| 3.3 No events after finalize | Operations after `finalize()` are rejected | no extra events in server |
| 3.4 Empty publish | `publish()` with no pending events | no error, no events |
| 3.5 Publish retry | Publish after transient failure | data eventually reaches server |

### 4. Variant Assignment

| Test | Description | Verification |
|------|-------------|--------------|
| 4.1 Deterministic assignment | Same unit ID always gets same variant | run same unit 2x, compare |
| 4.2 Distribution | N units split roughly 50/50 for a 50/50 experiment | `unit_count` per variant within tolerance |
| 4.3 Override | `override(exp, variant)` forces variant, exposure has `overridden=true` | events show override flag |
| 4.4 Custom assignment | `customAssignment(exp, variant)` | exposure shows correct variant |
| 4.5 Full-on experiment | Set experiment to full-on variant 1 | all units get variant 1 |
| 4.6 Zero traffic | Experiment at 0% traffic | all units get variant 0, not eligible |

### 5. Variables (Dynamic Config)

| Test | Description | Verification |
|------|-------------|--------------|
| 5.1 Variable value | `variableValue(key, default)` returns config value | correct value returned |
| 5.2 Variable exposure | `variableValue()` generates exposure for owning experiment | exposure in metrics |
| 5.3 Peek variable | `peekVariableValue()` does NOT generate exposure | no exposure |
| 5.4 Default value | Variable key not in any experiment returns default | correct default |
| 5.5 Variable keys | `variableKeys()` returns correct mapping | all keys present |

### 6. Attributes & Audience Targeting

| Test | Description | Verification |
|------|-------------|--------------|
| 6.1 Attribute publish | Set attributes, verify they appear in events | `abs events list` shows attributes |
| 6.2 Audience match | Set attributes matching audience filter, user gets assigned | exposure with `audienceMismatch=false` |
| 6.3 Audience mismatch strict | Attributes don't match, strict mode → variant 0 | exposure with `audienceMismatch=true`, variant=0 |
| 6.4 Audience mismatch non-strict | Attributes don't match, non-strict → normal assignment | exposure with `audienceMismatch=true`, variant=assigned |
| 6.5 Attribute update re-evaluation | Change attribute, call `treatment()` again → re-evaluates audience | new exposure if result changed |

### 7. Context Lifecycle

| Test | Description | Verification |
|------|-------------|--------------|
| 7.1 Context ready | Context loads experiment data from API | `experiments()` returns list |
| 7.2 Context refresh | Start new experiment, `refresh()` picks it up | new experiment visible after refresh |
| 7.3 Deferred context | `createContextWith()` with promise, operations before ready are queued | events sent after ready |
| 7.4 Failed context | Context with bad endpoint fails gracefully | `isFailed()=true`, treatment returns 0 |
| 7.5 Multiple contexts | Create N contexts concurrently, each publishes independently | all events arrive |

### 8. Units

| Test | Description | Verification |
|------|-------------|--------------|
| 8.1 Single unit | Context with `user_id` only | events have correct unit type |
| 8.2 Multiple unit types | Context with `user_id` + `session_id` | both unit types in events |
| 8.3 Unit hashing | Verify unit UID in events matches expected MD5 hash | `abs events list` uid matches |

### 9. Custom Fields

| Test | Description | Verification |
|------|-------------|--------------|
| 9.1 Text field | `customFieldValue(exp, field)` returns text | correct value |
| 9.2 JSON field | Custom field with JSON value parsed correctly | correct parsed object |
| 9.3 All field keys | `customFieldKeys()` returns complete list | all keys present |

### 10. Cross-SDK Consistency

| Test | Description | Verification |
|------|-------------|--------------|
| 10.1 Same assignment | All SDKs assign same variant for same unit | compare variants across SDKs |
| 10.2 Same exposure count | N units through each SDK → same total participants | metrics match |
| 10.3 Same goal count | Same track calls → same goal counts | metrics match |
| 10.4 Attribute roundtrip | All SDKs send attributes the same way | events match |

---

## Implementation Notes

### Experiment Setup Per Test

Each test category should create its own experiment(s) to avoid interference:
- `e2e-{run_id}-exposure` for exposure tests
- `e2e-{run_id}-goals` for goal tests
- `e2e-{run_id}-variables` with variant configs for variable tests
- `e2e-{run_id}-audience` with audience filter for targeting tests
- `e2e-{run_id}-override` for override/full-on tests

### Metrics Configuration

Create dedicated metrics per test:
- `e2e_exposure_count` (goal_unique_count on any goal) — for participant/exposure counts
- `e2e_purchase_count` (goal_unique_count on `purchase`) — already exists
- `e2e_signup_count` (goal_unique_count on `signup`) — for multi-goal tests
- `e2e_purchase_sum` (goal_property on `purchase.amount`) — for revenue verification

### Verification Methods

1. **Metrics API**: `abs experiments metrics results {id}` — primary verification for counts
2. **Events API**: `abs events list --event-name {exp} --event-type exposure` — for detailed event inspection (flags, attributes, unit UIDs)
3. **Context response**: Wrapper returns events array — for immediate client-side verification

### Parallelism

- Tests within a category can share an experiment (different units)
- Different categories should use separate experiments
- SDKs can run in parallel against the same experiment
- Use `{run_id}-{sdk_name}-{test}-{unit_index}` as unit ID pattern to avoid collisions

### Timing

- Poll context until experiment visible (already implemented)
- `abs experiments request-update {id}` after publishing
- Poll metrics with 5s interval, 60s timeout
- For event-level checks, events are available immediately via `abs events list`

---

## Priority

**P0 — Must have (validates data flows correctly):**
- 1.1, 1.2, 1.5 (exposure tracking)
- 2.1, 2.2 (goal tracking)
- 3.1, 3.2 (publishing)
- 4.2 (distribution)
- 10.1, 10.2, 10.3 (cross-SDK consistency)

**P1 — Should have (validates SDK features work end-to-end):**
- 1.3, 1.4 (exposure edge cases)
- 2.3, 2.4, 2.5 (goal edge cases)
- 4.1, 4.3, 4.4 (assignment features)
- 5.1, 5.2 (variables)
- 6.1, 6.2, 6.3 (attributes/audience)
- 7.1, 7.2 (context lifecycle)

**P2 — Nice to have (edge cases):**
- 3.3, 3.4, 3.5 (publish edge cases)
- 4.5, 4.6 (full-on, zero traffic)
- 5.3, 5.4, 5.5 (variable edge cases)
- 6.4, 6.5 (audience edge cases)
- 7.3, 7.4, 7.5 (context edge cases)
- 8.1, 8.2, 8.3 (units)
- 9.1, 9.2, 9.3 (custom fields)
