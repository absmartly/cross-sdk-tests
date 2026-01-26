# Complete 131-Scenario Test Suite - Quick Start Guide

## TL;DR - Run Tests Now

```bash
cd /Users/joalves/git_tree/sdks/cross-sdk-tests
./RUN_TESTS_NOW.sh
```

This script will:
1. Check Docker is running
2. Build all 10 SDK containers (~10-15 min)
3. Run 80 testable scenarios across all SDKs (~15-20 min)
4. Generate results report
5. Display summary

**Total time:** ~30-40 minutes

## What Gets Tested

### 131 Total Scenarios
- **80 testable scenarios** - Run via HTTP API wrappers
- **51 unit test scenarios** - Internal functions (not run via wrappers)

### 10 SDK Implementations
1. **JavaScript** (port 3001) - Reference implementation
2. **Python** (port 3002) - Flask wrapper
3. **Ruby** (port 3003) - Sinatra wrapper
4. **Java** (port 3004) - Spring Boot wrapper
5. **PHP** (port 3005) - ReactPHP wrapper
6. **Go** (port 3006) - net/http wrapper
7. **Liquid** (port 3007) - Template-based wrapper
8. **Flutter/Dart** (port 3008) - Shelf wrapper
9. **Swift** (port 3010) - Vapor wrapper
10. **.NET** (port 3009) - ASP.NET Core wrapper

### Total Test Executions
80 scenarios × 10 SDKs = **800 test executions**

## Expected Results

Based on recent bug fixes and previous test runs:

### Tier 1: Excellent (95-100% expected)
- **JavaScript:** 77-80/80 (96-100%)
- **Ruby:** 77-80/80 (96-100%)
- **Go:** 78-80/80 (98-100%) ← Bug fixed
- **Python:** 76-80/80 (95-100%)
- **Liquid:** 76-78/80 (95-98%)

### Tier 2: Good (90-95% expected)
- **Java:** 76-78/80 (95-98%) ← Bug fixed
- **PHP:** 72-76/80 (90-95%) ← Multiple bugs fixed

### Tier 3: Moderate (85-95% expected)
- **Flutter:** 71-75/80 (89-94%) ← SDK bug blocks ~13 scenarios
- **.NET:** 68-72/80 (85-90%) ← Improvements made

### Tier 4: Needs Work (<85% expected)
- **Swift:** 36-40/80 (45-50%) ← JSON serialization issues

## Recent Improvements

### Bug Fixes Applied
- ✅ **Java SDK:** Variable access exposure queueing bug fixed (scenario 23)
- ✅ **Go SDK:** Variable access exposure queueing bug fixed (scenario 23)
- ✅ **PHP SDK:** Multiple fixes (custom fields, variable access, event serialization)
- ✅ **.NET SDK:** JSON deserialization, unit type preservation, error handling

### Expected Improvements
- **Java:** 97% → 95-98% (on expanded scenarios)
- **Go:** 97% → 98-100% (on expanded scenarios)
- **PHP:** 82.6% → 90-95% (significant improvement)
- **.NET:** 89.1% → 90%+ (moderate improvement)

## Known Issues

### Test Expectation Bugs (Affect All SDKs)
These 3 scenarios will fail on most SDKs due to incorrect expected values:
- **116** - SDK Config - Unit Type Coercion
- **126** - Publish - Auto Timestamp
- **131** - Context - Is Finalized

**Note:** These are test bugs, not SDK bugs. Actual pass rates should be ~3 points higher.

### SDK Implementation Bugs
- **Flutter:** RangeError on fullOnVariant = -1 (blocks ~13 scenarios)
- **Swift:** JSON serialization crash (blocks ~18 scenarios)
- **.NET:** Index out of range in audience matching (blocks ~8-12 scenarios)

## Manual Execution (Alternative to Script)

If you prefer to run commands manually:

### Step 1: Build SDKs
```bash
cd /Users/joalves/git_tree/sdks/cross-sdk-tests
docker compose build
```

### Step 2: Run Tests
```bash
docker compose run --rm --no-deps orchestrator
```

### Step 3: View Results
```bash
cat test-results/report.json | python3 -m json.tool | less
```

## Results Analysis

After test execution completes, results are saved to:
- **test-results/report.json** - Full detailed results

### Quick Summary Script
```bash
python3 << 'EOF'
import json

with open('test-results/report.json') as f:
    report = json.load(f)

print("\nSDK Test Results (80 scenarios):")
print("="*60)

for sdk_name in sorted(report['sdks'].keys()):
    sdk = report['sdks'][sdk_name]
    passed = sdk['passed']
    failed = sdk['failed']
    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0
    print(f"{sdk_name:15} {passed:2}/{total:2} ({pct:5.1f}%)")
EOF
```

## Troubleshooting

### Docker Not Running
```bash
# Start Docker Desktop
open -a Docker

# Wait 30-60 seconds, then check
docker ps
```

### Build Failures
```bash
# Clean up and rebuild
docker compose down -v
docker system prune -f
docker compose build --no-cache
```

### Service Not Ready
```bash
# Check logs for specific service
docker compose logs javascript-sdk
docker compose logs orchestrator
```

### Test Hangs
```bash
# Stop all services
docker compose down

# Check for hung containers
docker ps -a

# Restart
docker compose up -d
docker compose run --rm --no-deps orchestrator
```

## File Locations

```
/Users/joalves/git_tree/sdks/cross-sdk-tests/
├── RUN_TESTS_NOW.sh                    ← Execute this!
├── test_scenarios_complete.json         ← 131 scenarios (80 testable)
├── docker-compose.yml                   ← 10 SDKs + orchestrator
├── orchestrator/test_runner.py          ← Test execution logic
├── test-results/report.json             ← Results (after run)
├── FINAL_STATUS_REPORT.md               ← Detailed status
├── COMPLETE_TEST_EXECUTION_PLAN.md      ← Full execution plan
└── README_EXECUTION.md                  ← This file
```

## Success Criteria

### Minimum
- ✅ 80 scenarios run successfully
- ✅ 6+ SDKs with 85%+ pass rate
- ✅ Results report generated

### Target
- ✅ 80 scenarios on all 10 SDKs
- ✅ 5+ SDKs at 95%+ pass rate
- ✅ 3+ SDKs at 90%+ pass rate
- ✅ Bug fixes show improvement

### Stretch
- ✅ 8+ SDKs at 90%+ pass rate
- ✅ All SDKs complete without crashes
- ✅ Comprehensive failure analysis

## Support Documentation

1. **FINAL_STATUS_REPORT.md** - Complete status, known issues, expected results
2. **COMPLETE_TEST_EXECUTION_PLAN.md** - Detailed execution plan with all scenarios
3. **EXECUTION_READY_REPORT.md** - Ready-to-execute verification
4. Context file updates after execution

## Next Steps After Execution

1. **Review results:** Check test-results/report.json
2. **Compare to expected:** Review FINAL_STATUS_REPORT.md expectations
3. **Analyze failures:** Categorize by test bug vs SDK bug
4. **Update context:** Document actual results in session context file
5. **Generate report:** Create comprehensive SDK comparison report
6. **File bugs:** Document SDK bugs found during testing
7. **Fix tests:** Correct test expectation bugs (scenarios 116, 126, 131)

## Ready to Run?

```bash
cd /Users/joalves/git_tree/sdks/cross-sdk-tests
./RUN_TESTS_NOW.sh
```

**That's it!** The script handles everything and provides a summary when complete.

---

**Status:** ✅ READY - All preparations complete, just run the script!
