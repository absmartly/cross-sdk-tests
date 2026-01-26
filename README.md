# Cross-SDK Testing Infrastructure

## Overview

This directory contains the unified testing infrastructure for validating all ABsmartly SDKs against canonical expected results.

## What's Included

### 1. test_scenarios.json
**33 test scenarios** covering:
- ✅ Context creation and lifecycle (1 scenario)
- ✅ Unit management (1 scenario)
- ✅ Attribute management (1 scenario)
- ✅ Treatment assignment (5 scenarios)
- ✅ Peek operations (1 scenario)
- ✅ Override handling (1 scenario)
- ✅ Custom assignment (1 scenario)
- ✅ Full-on variants (1 scenario)
- ✅ Traffic split filtering (1 scenario)
- ✅ Unknown experiments (1 scenario)
- ✅ Goal tracking (3 scenarios)
- ✅ Custom fields (2 scenarios)
- ✅ Publishing (2 scenarios)
- ✅ Finalization (1 scenario)
- ✅ Audience matching (3 scenarios)
- ✅ Variable access (3 scenarios)
- ✅ **Cache invalidation (6 scenarios)** - Critical!

Each scenario includes:
- Input: contextData, units, parameters
- Sequence of actions: createContext → treatment → track → publish
- **Expected results**: exact return values
- **Expected events**: exact event structure and data

### 2. WRAPPER_API_SPEC.md
Complete API specification for wrapper services:
- 20 endpoints with request/response formats
- EventCollector implementation pattern
- Context storage pattern
- Event return pattern
- Error handling
- Testing instructions with curl commands

### 3. CROSS_SDK_TESTING.md (parent directory)
Complete testing infrastructure design:
- Architecture diagrams
- Docker setup
- Orchestrator implementation
- Validation logic
- Test execution flow

## Test Scenario Format

```json
{
  "name": "Descriptive test name",
  "description": "What this validates",
  "contextData": {
    "experiments": [...]
  },
  "steps": [
    {
      "action": "createContext|treatment|track|publish|...",
      "params": {...},
      "expect": {
        "result": <expected value>,
        "events": [
          {
            "type": "exposure|goal|publish|...",
            "data": {
              "field1": "expected value",
              "field2": 123
            }
          }
        ]
      }
    }
  ]
}
```

## Test Categories

### Core Functionality (19 scenarios)
1. Context creation with data
2. Unit management (set, get)
3. Attribute management (set, get, last value)
4. Treatment assignment with exposure
5. Treatment only queues exposure once
6. Peek without exposure
7. Override variant
8. Custom assignment
9. Full-on variant (100% traffic)
10. Traffic split filtering (not eligible)
11. Unknown experiment (base variant)
12-16. Custom fields (string, number, boolean, JSON parsing)
17-18. Publishing (with events, empty queue)
19. Finalization

### Advanced Features (11 scenarios)
20. Audience match (non-strict)
21. Audience mismatch (non-strict)
22. Audience mismatch (strict mode → variant 0)
23. Variable access with exposure
24. Variable default value
25. Variable peek without exposure

### Cache Invalidation (6 scenarios) - CRITICAL!
26. Experiment stopped → cache cleared, new exposure
27. Experiment started → cache cleared, new exposure
28. FullOnVariant changed → cache cleared, new exposure
29. TrafficSplit changed → cache cleared, new exposure
30. Iteration changed → cache cleared, new exposure
31. ID changed → cache cleared, new exposure
32. No changes → cache retained, NO new exposure
33. Override set → cache retained, NO new exposure

## Running Tests

### Prerequisites

```bash
cd cross-sdk-tests
```

### Build Containers

```bash
docker-compose up --build
```

### Run Tests

```bash
docker-compose run orchestrator python test_runner.py
```

### Expected Output

```
=== Running: 01 - Context Creation - Ready with Data ===
    Context should be ready immediately when created with data
  Testing javascript... ✓ PASS
  Testing python... ✓ PASS
  Testing java... ✓ PASS
  Testing ruby... ✓ PASS

=== Running: 26 - Cache Invalidation - Experiment Stopped ===
    Cache should be cleared when experiment stops
  Testing javascript... ✓ PASS
  Testing python... ✓ PASS
  Testing java... ✗ FAIL (1 failure)
  Testing ruby... ✓ PASS

...

============================================================
                       TEST SUMMARY
============================================================

Total Scenarios: 33

SDK Results:
------------------------------------------------------------
  javascript           ✓ PASS   (33/33 passed, 100.0%)
  python               ✓ PASS   (33/33 passed, 100.0%)
  java                 ✗ FAIL   (32/33 passed, 97.0%)
  ruby                 ✓ PASS   (33/33 passed, 100.0%)
============================================================

Detailed report: /results/report.json
```

## Next Steps

### To Add More Scenarios

1. Examine `javascript-sdk/src/__tests__/context.test.js`
2. Identify test case to replicate
3. Create scenario in `test_scenarios.json`:
   - Define contextData
   - Define action sequence
   - **Generate expected results from JavaScript SDK**
4. Run tests to validate

### To Generate Expected Results

Use JavaScript SDK to generate canonical expected results:

```javascript
const absmartly = require('@absmartly/javascript-sdk');

const eventCollector = {
  events: [],
  handleEvent(context, eventName, data) {
    this.events.push({
      type: eventName,
      data: JSON.parse(JSON.stringify(data)),
      timestamp: Date.now()
    });
  }
};

const sdk = new absmartly.SDK({
  endpoint: 'http://dummy',
  apiKey: 'dummy',
  application: 'test',
  environment: 'test',
  eventLogger: eventCollector.handleEvent.bind(eventCollector)
});

const context = sdk.createContextWith(
  { units: { session_id: "test123" } },
  { experiments: [...] },
  { publishDelay: -1 }
);

console.log('Result:', context.treatment('exp_test'));
console.log('Events:', eventCollector.events);
```

Copy the output as expected results in the scenario.

### To Implement Wrapper Service

1. Follow pattern in `WRAPPER_API_SPEC.md`
2. Implement all 20 endpoints
3. Use EventCollector to capture events
4. Return events with each response
5. Test locally with curl before Docker
6. Add to docker-compose.yml

## Current Status

**Scenarios Created:** 33
**Wrapper Services Implemented:** 0
**Next:** Implement JavaScript wrapper service first

## File Structure

```
cross-sdk-tests/
├── README.md (this file)
├── WRAPPER_API_SPEC.md
├── test_scenarios.json (33 scenarios)
├── orchestrator/
│   ├── test_runner.py (to be created)
│   └── requirements.txt (to be created)
├── javascript-wrapper/
│   ├── Dockerfile (to be created)
│   ├── server.js (to be created)
│   └── package.json (to be created)
└── docker-compose.yml (to be created)
```

## Key Design Decisions

1. **Synchronous event return**: Events in same HTTP response (no WebSockets)
2. **Canonical validation**: Each SDK tested against hardcoded expected results (not cross-comparison)
3. **Stateful contexts**: Each context stored by ID, maintains state across requests
4. **Independent SDKs**: Each SDK container is completely isolated
5. **Test-driven**: Scenarios define expected behavior, SDKs must match exactly

## Critical Test Areas

### Cache Invalidation (Scenarios 26-33)
**Most common SDK bug!** These scenarios validate that:
- Cache is cleared when experiments change (6 scenarios)
- Cache is retained when nothing changes (2 scenarios)
- Each scenario expects a **new exposure event** or **no event** (cache hit)

### Event Ordering
All event types must be generated in correct order:
1. `ready` - on context initialization
2. `error` - on failures
3. `exposure` - on treatment() or variableValue()
4. `goal` - on track()
5. `publish` - on publish() or finalize()
6. `refresh` - on refresh()
7. `finalize` - on finalize()

### Property Filtering
Goal properties must filter non-numeric values (scenario 09).

## Validation Criteria

Each SDK must:
- ✅ Pass all 33 scenarios (100%)
- ✅ Return exact result values
- ✅ Generate exact event types
- ✅ Include expected event data fields
- ✅ Handle cache invalidation correctly
- ✅ Filter non-numeric goal properties
- ✅ Support all API methods

## Benefits

- **Confidence**: Know all SDKs behave identically
- **Regression Detection**: Catch bugs before release
- **Cross-Language Validation**: Verify deterministic behavior
- **Release Gate**: Automated pass/fail for CI/CD
- **Documentation**: Scenarios serve as behavior specification
