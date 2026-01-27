---
name: create-new-sdk
description: |
  Create a new ABsmartly SDK in a programming language or framework. Use when implementing
  SDKs in languages like Rust, Kotlin, Elixir, Scala, R, C++, or any language not yet supported.
  Covers SDK architecture, core algorithms (Murmur3, MD5, variant assignment), test wrapper
  creation for the cross-sdk-tests framework, and validation against 135 test scenarios.
---

# Creating a New ABsmartly SDK

## Pre-Implementation Checklist

Before starting, confirm:

1. **Target Language**: Which language? (Rust, Kotlin, Elixir, etc.)
2. **Package Manager**: cargo, maven, hex, CRAN, etc.
3. **Async Support**: Does the language have async/await?
4. **HTTP Client**: Preferred HTTP library?
5. **Testing Framework**: Which test framework?

## Reference Documentation

Read these files first - they contain critical implementation details:

- `SDK_IMPLEMENTATION_GUIDE.md` - Complete 10-phase implementation guide
- `SDK_QUICK_REFERENCE.md` - Code snippets for multiple languages
- `NEW_SDK_PROPOSALS.md` - Proposals with skeleton code
- `cross-sdk-tests/WRAPPER_API_SPEC.md` - Test wrapper API specification

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      Application                         │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                    SDK Class                             │
│  - createContext() (async)                               │
│  - createContextWith() (sync)                            │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Client     │ │  Publisher   │ │  Provider    │
│  (HTTP)      │ │ (Events)     │ │ (Data)       │
└──────────────┘ └──────────────┘ └──────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                  Context Class                           │
│  - treatment(), peek()                                   │
│  - variableValue(), peekVariableValue()                  │
│  - track(), publish(), finalize()                        │
└───────────┬─────────────┬─────────────┬─────────────────┘
            │             │             │
            ▼             ▼             ▼
    ┌──────────────┐ ┌────────────┐ ┌────────────────┐
    │   Assigner   │ │  Matcher   │ │ JSON Evaluator │
    │  (Murmur3)   │ │ (Audience) │ │  (Targeting)   │
    └──────────────┘ └────────────┘ └────────────────┘
```

## Implementation Phases

### Phase 1: Project Structure & Core Types

Create project with package manager:

```bash
cargo new absmartly-sdk --lib           # Rust
mix new absmartly                        # Elixir
sbt new scala/scala-seed.g8              # Scala
```

Define core data structures:
- `SDKConfig` - endpoint, apiKey, application, environment, retries, timeout
- `ContextData` - experiments array from API
- `ExperimentData` - id, name, unitType, iteration, seeds, split, variants, audience
- `Assignment` - cached variant assignment with flags
- `Exposure` - exposure event for publishing
- `Goal` - goal achievement event

### Phase 2: Algorithms (CRITICAL)

**Murmur3_32 Hash - Must match exactly:**

> **Note:** You don't need to implement Murmur3 or MD5 from scratch if reliable libraries exist for your language. The JavaScript SDK implemented them manually because it needs to be bundled for browsers with zero dependencies. For server-side SDKs (Rust, Go, Python, etc.), use well-tested libraries like `murmur3` crate, `md-5` crate, etc. Just ensure the output matches the test vectors below.

```
Constants (if implementing manually):
  C1 = 0xcc9e2d51
  C2 = 0x1b873593
  C3 = 0xe6546b64

Key Requirements:
  - All arithmetic is UNSIGNED 32-bit
  - Byte order is LITTLE-ENDIAN
  - Test with canonical vectors before proceeding
```

**Test Vectors (validate these first):**

```
murmur3_32("", 0) = 0
murmur3_32("absmartly.com", 0) = 0x6D02F2B7
murmur3_32("bleh@absmartly.com", 0) = 0x1498CA89
```

**Variant Assignment:**

```
Input: unit (string), split[], seedHi, seedLo
Output: variant index (0, 1, 2, ...)

1. unitHash = murmur3_32(unit.toBytes(), 0)
2. buffer = [seedLo (4 bytes LE), seedHi (4 bytes LE), unitHash (4 bytes LE)]
3. hash = murmur3_32(buffer, 0)
4. probability = hash / 0xFFFFFFFF  # NOT 0x100000000!
5. Return first variant where cumulative split >= probability
```

### Phase 3: SDK & Context Core

**SDK Class - BOTH methods are REQUIRED:**

| Method | Type | Description |
|--------|------|-------------|
| `createContext(units, options)` | **ASYNC** | Fetches data from API endpoint, returns context |
| `createContextWith(units, data, options)` | **SYNC** | Uses pre-fetched data, returns context immediately |

**CRITICAL: You MUST implement BOTH methods!**
- `createContext` makes HTTP request to `{endpoint}/context` to fetch experiment data
- `createContextWith` uses data already fetched (for SSR, pre-fetching, etc.)

Constructor validates config: endpoint, apiKey, application, environment

**Context Class State:**
- `ready`, `failed`, `finalized`, `finalizing` flags
- `units` map, `attributes` list
- `assignments` cache, `exposures`, `goals` queues

**ALL Required Context Methods (must implement ALL of these):**

| Method | Singular | Plural | Description |
|--------|----------|--------|-------------|
| Units | `setUnit(type, uid)` | `setUnits(units)` | Add units to context |
| Units | `getUnit(type)` | `getUnits()` | Get unit(s) |
| Attributes | `setAttribute(name, value)` | `setAttributes(attrs)` | Set targeting attributes |
| Attributes | `getAttribute(name)` | `getAttributes()` | Get attribute(s) |
| Override | `setOverride(exp, variant)` | `setOverrides(overrides)` | Force specific variants |
| Custom Assign | `setCustomAssignment(exp, variant)` | `setCustomAssignments(assigns)` | Custom assignments |
| Treatment | `treatment(experimentName)` | - | Get variant, queue exposure |
| Treatment | `peek(experimentName)` | - | Get variant WITHOUT exposure |
| Variables | `variableValue(key, default)` | `variableKeys()` | Get variable value |
| Variables | `peekVariableValue(key, default)` | - | Get variable WITHOUT exposure |
| Custom Fields | `customFieldValue(exp, key)` | `customFieldKeys()` | Get custom field values |
| Goals | `track(goalName, properties)` | - | Queue goal (numeric props only!) |
| Lifecycle | `publish()` | - | Send queued events |
| Lifecycle | `finalize()` | - | Publish and seal |
| Lifecycle | `refresh(newData)` | - | Update context data |
| State | `isReady()`, `isFailed()`, `isFinalized()`, `isFinalizing()` | - | Check state |
| State | `pending()` | - | Count pending events |
| State | `data()` | - | Get context data |
| State | `experiments()` | - | List experiment names |

**CRITICAL: Implement BOTH singular AND plural versions for units, attributes, overrides, and custom assignments!**

### Phase 4: JSON Expression Evaluator

Implement 13 operators for audience targeting:

| Operator | Purpose | Args |
|----------|---------|------|
| `and` | All truthy | Array |
| `or` | Any truthy | Array |
| `not` | Negate | Single expr |
| `null` | Is null | Single expr |
| `var` | Extract variable | Path string |
| `value` | Literal value | Any |
| `eq` | Equals | [lhs, rhs] |
| `gt`, `gte`, `lt`, `lte` | Comparisons | [lhs, rhs] |
| `in` | Contains | [needle, haystack] |
| `match` | Regex match | [text, pattern] |

### Phase 5: Variables & Custom Fields

**Variable Index:**
- Build map: `variableKey → [experiments]`
- Handle overlapping experiments (latest non-control wins)

**Custom Fields:**
- Parse based on type: "string", "number", "boolean", "json"

### Phase 6: Events & Publishing

**Exposure Event:**
```json
{
  "id": 1, "name": "exp_test_ab", "unit": "session_id",
  "variant": 1, "exposedAt": 1705347600000,
  "assigned": true, "eligible": true, "overridden": false,
  "fullOn": false, "custom": false, "audienceMismatch": false
}
```

**Goal Event - ONLY numeric properties:**
```json
{
  "name": "purchase",
  "achievedAt": 1705347600000,
  "properties": {"amount": 99.99}
}
```

### Phase 7: Cache Invalidation (CRITICAL!)

**Clear cache when ANY of these change:**
1. Experiment stopped (was running, now missing)
2. Experiment started (was missing, now running)
3. `fullOnVariant` changed
4. `trafficSplit` array changed
5. `iteration` changed
6. Experiment `id` changed

**Keep cache when:**
7. No experiment changes
8. Has override set

This is the most common source of SDK bugs!

### Phase 8: HTTP Client

**Retry logic:**
- Retry on: network errors, 5xx status
- Don't retry on: 4xx status
- Exponential backoff: 50ms × 2^attempt

## Unit Test Requirements (CRITICAL)

**Target: ~130 unit tests matching JavaScript SDK coverage**

The SDK must have comprehensive unit tests before creating the test wrapper. Use the JavaScript SDK as the reference for expected test values and behavior.

### Test Categories

#### 1. Murmur3 Tests (~10 tests)

> **Note:** Even when using a library, these tests are essential to verify the library produces correct output. Some libraries have different defaults (e.g., big-endian vs little-endian).

Test vectors that MUST pass:

```
murmur3_32("", 0) = 0x00000000
murmur3_32(" ", 0) = 0x7ef49b98
murmur3_32("t", 0) = 0xca87df4d
murmur3_32("te", 0) = 0xedb8ee1b
murmur3_32("tes", 0) = 0x0bb90e5a
murmur3_32("test", 0) = 0xba6bd213
murmur3_32("test", 0xdeadbeef) = 0xaa22d41a
murmur3_32("test", 1) = 0x99c02ae2
murmur3_32("The quick brown fox jumps over the lazy dog", 0) = 0x2e4ff723
```

#### 2. Utils Tests (~15 tests)

**hashUnit (MD5 + base64url):**

> Use MD5 library if available. Verify output format: lowercase hex → base64url encoding without padding.
```
hashUnit("bleh@absmartly.com") = "V2hlbiB0aGVyZSBhcmUgbm8..."  // MD5 → base64url no padding
```

**chooseVariant:**
```
chooseVariant([0.0, 1.0], 0.0) = 0
chooseVariant([0.0, 1.0], 0.5) = 1
chooseVariant([0.0, 1.0], 1.0) = 1
chooseVariant([0.5, 0.5], 0.0) = 0
chooseVariant([0.5, 0.5], 0.25) = 0
chooseVariant([0.5, 0.5], 0.49999999) = 0
chooseVariant([0.5, 0.5], 0.5) = 1
chooseVariant([0.5, 0.5], 0.50000001) = 1
chooseVariant([0.5, 0.5], 0.75) = 1
chooseVariant([0.33, 0.33, 0.34], 0.0) = 0
chooseVariant([0.33, 0.33, 0.34], 0.33) = 1
chooseVariant([0.33, 0.33, 0.34], 0.66) = 2
chooseVariant([0.33, 0.33, 0.34], 1.0) = 2
```

#### 3. Assigner Tests (~10 tests)

**IMPORTANT: Unit must be hashed with hashUnit() BEFORE creating VariantAssigner**

```javascript
// JavaScript pattern
const hashedUnit = hashUnit("bleh@absmartly.com");
const assigner = new VariantAssigner(hashedUnit);
```

Test cases:
```
hashUnit("bleh@absmartly.com") → assign([0.5, 0.5], 0, 0) = 0
hashUnit("bleh@absmartly.com") → assign([0.5, 0.5], 0, 1) = 1
hashUnit("123456789") → assign([0.5, 0.5], 0, 0) = 1
hashUnit("123456789") → assign([0.5, 0.5], 0, 1) = 0
hashUnit("bleh@absmartly.com") → assign([0.33, 0.33, 0.34], 0, 1) = 2
```

#### 4. JSON Expression Evaluator Tests (~50 tests)

**Type Coercion:**
```
booleanConvert(null) = null
booleanConvert(true) = true
booleanConvert(false) = false
booleanConvert(0) = false
booleanConvert(1) = true
booleanConvert("") = false
booleanConvert("abc") = true
booleanConvert([]) = true
booleanConvert({}) = true

numberConvert(null) = null
numberConvert(true) = 1
numberConvert(false) = 0
numberConvert(0) = 0
numberConvert(1.5) = 1.5
numberConvert("") = 0
numberConvert("123") = 123
numberConvert("abc") = null

stringConvert(null) = null
stringConvert(true) = "true"
stringConvert(false) = "false"
stringConvert(0) = "0"
stringConvert(1.5) = "1.5"
stringConvert("abc") = "abc"
```

**Compare function (used by eq, gt, gte, lt, lte):**
```
compare(null, null) = 0
compare(null, 0) = null
compare(0, null) = null
compare(0, 0) = 0
compare(1, 0) = 1
compare(0, 1) = -1
compare("a", "a") = 0
compare("a", "b") = -1
compare("b", "a") = 1
compare(true, true) = 0
compare(true, false) = 1
compare(false, true) = -1
compare([], []) = 0  // Arrays/objects compare by JSON representation
```

**Operators to test:**

| Operator | Key Tests |
|----------|-----------|
| `and` | Empty array → true, all truthy → true, any falsy → false |
| `or` | Empty array → false, any truthy → true, all falsy → false |
| `not` | Inverts boolean result |
| `null` | Returns true only for null |
| `var` | Extracts nested path "a/b/c" from context |
| `value` | Returns literal value |
| `eq` | null == null, type coercion, arrays |
| `gt/gte/lt/lte` | Numeric, string, null handling |
| `in` | String contains, array contains, null handling |
| `match` | Regex matching, null handling |

#### 5. Matcher Tests (~20 tests)

**AudienceMatcher - evaluate() function:**
```
// Null/empty handling
evaluate(null) = null
evaluate({}) = true  // Empty filter matches all

// Nested filter evaluation
evaluate({"filter": [{"value": true}]}) = true
evaluate({"filter": [{"value": false}]}) = false
evaluate({"filter": [{"and": [{"value": true}, {"value": true}]}]}) = true
evaluate({"filter": [{"and": [{"value": true}, {"value": false}]}]}) = false
```

#### 6. Context Tests (~40 tests)

**Treatment/Peek:**
- Returns 0 for non-existent experiments
- Returns correct variant based on assignment
- Queues exposure event for treatment() but not peek()
- Respects full-on variant
- Respects traffic split
- Handles audience mismatch

**Variables:**
- variableValue returns correct value based on experiment assignment
- peekVariableValue doesn't queue exposure
- Handles missing variables (returns default)
- Handles overlapping experiments (latest non-control wins)

**Track:**
- Queues goal event with correct timestamp
- Filters non-numeric properties
- Handles null properties

**Override & Custom Assignment:**
- Override returns specified variant regardless of assignment
- Custom assignment skips normal assignment logic
- Override/custom clear on context refresh

**Publish:**
- Collects all queued exposures and goals
- Clears queues after publish
- Sets hashed unit in published events

**Finalize:**
- Publishes remaining events
- Prevents further operations after finalize

**Refresh:**
- Updates context data
- Clears assignments that changed (cache invalidation rules)
- Keeps assignments for unchanged experiments

### Test File Organization

```
src/
├── murmur3.rs          # with murmur3 tests
├── utils.rs            # with hashUnit, chooseVariant tests
├── assigner.rs         # with assignment tests
├── jsonexpr/
│   ├── evaluator.rs    # with type coercion, compare tests
│   └── operators/
│       ├── mod.rs
│       ├── and.rs      # with and operator tests
│       ├── or.rs       # with or operator tests
│       ├── eq.rs       # with equals tests
│       ├── gt.rs       # with comparison tests
│       ├── in.rs       # with contains tests
│       ├── match.rs    # with regex tests
│       └── ...
├── matcher.rs          # with audience matcher tests
└── context.rs          # with comprehensive context tests
```

### Reference JavaScript Test Files

Use these JavaScript SDK test files as the authoritative source:

- `javascript-sdk/src/__tests__/murmur3_32.test.js`
- `javascript-sdk/src/__tests__/utils.test.js`
- `javascript-sdk/src/__tests__/assigner.test.js`
- `javascript-sdk/src/__tests__/jsonexpr/evaluator.test.js`
- `javascript-sdk/src/__tests__/jsonexpr/operators/*.test.js`
- `javascript-sdk/src/__tests__/matcher.test.js`
- `javascript-sdk/src/__tests__/context.test.js`

### Running SDK Unit Tests

Before creating the wrapper, ensure all SDK unit tests pass:

```bash
cargo test          # Rust
npm test            # JavaScript
mix test            # Elixir
sbt test            # Scala
```

**Target: 100% of JavaScript SDK test scenarios covered**

## Creating the Test Wrapper

### Wrapper Location

Create: `cross-sdk-tests/<language>-wrapper/`

### Wrapper API (20 Endpoints)

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check (return 200) |
| `GET /capabilities` | Return `{"asyncContext": bool}` |
| `POST /context` | Create context with data |
| `DELETE /context/{id}` | Delete context |
| `POST /context/{id}/treatment` | Get treatment |
| `POST /context/{id}/peek` | Peek treatment |
| `POST /context/{id}/track` | Track goal |
| `POST /context/{id}/publish` | Publish events |
| `POST /context/{id}/finalize` | Finalize context |

**Response Format (ALL endpoints):**

```json
{
  "result": <SDK method return value>,
  "events": [
    {"type": "exposure|goal|publish|ready|refresh|error", "data": {...}}
  ]
}
```

### Event Collection Pattern

```javascript
class EventCollector {
  events = [];

  handleEvent(context, eventType, data) {
    this.events.push({
      type: eventType,
      data: deepCopy(data)  // Always deep copy!
    });
  }

  getEventsSince(previousCount) {
    return this.events.slice(previousCount);
  }
}
```

### Docker Configuration

Add to `docker-compose.yml`:

```yaml
  <language>-sdk:
    build:
      context: ..
      dockerfile: cross-sdk-tests/<language>-wrapper/Dockerfile
    ports:
      - "30XX:3000"
    environment:
      - SDK_NAME=<language>
```

**Note:** The build context is the parent directory (`..`) containing both the SDK and cross-sdk-tests. The Dockerfile path is relative to the cross-sdk-tests directory.

## Building and Testing Wrappers

### Building a Single Wrapper

From the `cross-sdk-tests` directory:

```bash
cd cross-sdk-tests

# Build a specific wrapper
docker-compose build <sdk-name>

# Examples:
docker-compose build javascript-sdk
docker-compose build vue3-sdk
docker-compose build rust-sdk

# Build with no cache (forces fresh build)
docker-compose build --no-cache <sdk-name>
```

### Building All Wrappers

```bash
docker-compose build
```

### Running Tests

```bash
# Run tests for a specific SDK
./run-tests.sh --sdk <sdk-name>

# Examples:
./run-tests.sh --sdk javascript-sdk
./run-tests.sh --sdk vue3-sdk
./run-tests.sh --sdk rust-sdk

# Run tests for all SDKs
./run-tests.sh
```

The test script will:
1. Build the wrapper Docker image
2. Start the wrapper service
3. Run the test orchestrator against it
4. Report pass/fail results

**Minimum requirement: 232+ tests passing**

## Pre-Build Verification (CRITICAL)

Before the Docker build will succeed, the underlying SDK must pass its own build/lint/test steps. The Dockerfile typically runs these during the build phase.

### Common Build Failures

#### 1. ESLint/Lint Errors (JavaScript/TypeScript SDKs)

The Docker build runs `npm run build` which often includes linting. Common failures:

**Unused imports:**
```javascript
// ERROR: 'h' is defined but never used
import { defineComponent, h, nextTick } from "vue";

// FIX: Remove unused import
import { defineComponent, nextTick } from "vue";
```

**Unused variables:**
```javascript
// ERROR: 'firstContext' is assigned but never used
const firstContext = localVue.prototype.$absmartly;

// FIX: Either remove or add an assertion
expect(firstContext).toBeDefined();
```

#### 2. Node.js OpenSSL Legacy Provider

For older Vue/Webpack builds on Node 18+:
```dockerfile
RUN npm install && NODE_OPTIONS=--openssl-legacy-provider npm run build
```

#### 3. Type Errors (TypeScript SDKs)

Ensure all type definitions are correct before building.

### Verifying SDK Locally Before Docker Build

Always test the SDK build locally first:

```bash
# JavaScript/TypeScript SDKs
cd ../javascript-sdk  # or vue3-sdk, react-sdk, etc.
npm install
npm run build
npm test

# Python SDK
cd ../python-sdk
pip install -e .
pytest

# Rust SDK
cd ../rust-sdk
cargo build
cargo test

# Go SDK
cd ../go-sdk
go build ./...
go test ./...
```

### Debugging Docker Build Failures

When a build fails, get the full error output:

```bash
# Show full build output with progress
docker-compose build --progress=plain <sdk-name> 2>&1 | tail -100

# Save full log for analysis
docker-compose build --no-cache --progress=plain <sdk-name> 2>&1 | tee build.log
```

### Wrapper Service Ports

Each wrapper runs on a unique port:

| SDK | Port |
|-----|------|
| javascript-sdk | 3001 |
| python-sdk | 3002 |
| ruby-sdk | 3003 |
| java-sdk | 3004 |
| php-sdk | 3005 |
| go-sdk | 3006 |
| liquid-sdk | 3007 |
| flutter-sdk | 3008 |
| dotnet-sdk | 3009 |
| swift-sdk | 3010 |
| dart-sdk | 3011 |
| react-sdk | 3012 |
| vue2-sdk | 3013 |
| vue3-sdk | 3014 |
| rust-sdk | 3015 |

When adding a new SDK, use the next available port (3016+).

## Common Pitfalls

1. **Wrong byte order**: Murmur3 uses LITTLE-ENDIAN
2. **Wrong denominator**: Use `hash / 0xFFFFFFFF` not `0x100000000`
3. **Signed vs unsigned**: All hash ops are UNSIGNED 32-bit
4. **Non-numeric goal props**: Must filter out non-numbers
5. **Unit hashing**: Must MD5 hash UIDs before publishing
6. **Cache invalidation**: Must clear on experiment changes

## Validation Checklist

- [ ] Murmur3 matches test vectors exactly
- [ ] MD5 outputs lowercase hex
- [ ] Variant assignment is deterministic
- [ ] Cache invalidation handles all 8 scenarios
- [ ] Events include all required fields
- [ ] Goal properties filter non-numerics
- [ ] HTTP client retries correctly
- [ ] Wrapper returns events in response
- [ ] All 232+ tests pass
