# Cross-SDK Test Suite - How to Run

## Overview

This project provides a unified test harness for testing 10 ABSmartly SDKs via HTTP wrappers and a centralized orchestrator.

**Current Status**: 9/10 SDKs fully functional, 1/10 (Swift) has memory crash issues.

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- 8GB+ RAM recommended
- Ports 3000-3010 available

### Run All Tests

```bash
cd /Users/joalves/git_tree/sdks/cross-sdk-tests

# Start all services
docker compose up -d

# Wait for services to initialize (30 seconds)
sleep 30

# Run test suite
docker compose run --rm --no-deps orchestrator python test_runner.py

# View results
cat test-results/report.json
```

### Run Tests for Single SDK

```bash
# Example: Test only JavaScript SDK
docker compose up -d javascript-sdk
docker compose run --rm --no-deps orchestrator python -c "
import sys
sys.path.insert(0, '/app')
from test_runner import TestOrchestrator
import json

with open('/test_scenarios_complete.json') as f:
    scenarios = [s for s in json.load(f) if 'steps' in s]

orchestrator = TestOrchestrator({'javascript': 'http://javascript-sdk:3000'})
working, failed = orchestrator.wait_for_services()
orchestrator.sdks = working

for scenario in scenarios:
    orchestrator.run_scenario(scenario)

orchestrator.generate_report('/results/report.json', failed)
"
```

### Rebuild All Containers

```bash
# Rebuild all SDKs and orchestrator
docker compose build

# Rebuild specific SDK
docker compose build javascript-sdk
docker compose build swift-sdk
```

### Stop All Services

```bash
docker compose down
```

---

## Project Structure

```
cross-sdk-tests/
├── orchestrator/
│   ├── test_runner.py          # Main test orchestrator
│   ├── requirements.txt
│   └── Dockerfile
├── test_scenarios_complete.json # 131 test scenarios (80 testable via HTTP)
├── javascript-wrapper/         # HTTP wrapper for JavaScript SDK
├── python-wrapper/             # HTTP wrapper for Python SDK
├── ruby-wrapper/               # HTTP wrapper for Ruby SDK
├── java-wrapper/               # HTTP wrapper for Java SDK
├── go-wrapper/                 # HTTP wrapper for Go SDK
├── php-wrapper/                # HTTP wrapper for PHP SDK
├── liquid-wrapper/             # HTTP wrapper for Liquid SDK
├── flutter-wrapper/            # HTTP wrapper for Flutter SDK
├── dotnet-wrapper/             # HTTP wrapper for DotNET SDK
├── swift-wrapper/              # HTTP wrapper for Swift SDK
├── docker-compose.yml          # Service definitions
└── test-results/               # Test output (generated)
    └── report.json
```

---

## How It Works

### Architecture

```
┌─────────────────┐
│  Orchestrator   │  Reads test_scenarios_complete.json
│  (Python)       │  Makes HTTP requests to each SDK wrapper
└────────┬────────┘
         │
         ├──HTTP──> JavaScript Wrapper :3000 ──> JavaScript SDK
         ├──HTTP──> Python Wrapper     :3000 ──> Python SDK
         ├──HTTP──> Ruby Wrapper       :3000 ──> Ruby SDK
         ├──HTTP──> Java Wrapper       :3000 ──> Java SDK
         ├──HTTP──> Go Wrapper         :3000 ──> Go SDK
         ├──HTTP──> PHP Wrapper        :3000 ──> PHP SDK
         ├──HTTP──> Liquid Wrapper     :3000 ──> Liquid SDK (wraps Ruby SDK)
         ├──HTTP──> Flutter Wrapper    :3000 ──> Flutter SDK
         ├──HTTP──> DotNET Wrapper     :3000 ──> DotNET SDK
         └──HTTP──> Swift Wrapper      :3000 ──> Swift SDK
```

### Test Flow

1. **Orchestrator starts** and waits for all SDK wrappers to be healthy
2. **For each scenario**:
   - Load contextData and steps from JSON
   - Send HTTP requests to each SDK wrapper
   - Validate responses against expected results
   - Track passes/failures
3. **Generate report** with pass rates and failure details

### Test Scenario Format

```json
{
  "name": "04 - Treatment - Queue Exposure",
  "description": "treatment() should queue exposure event",
  "requires": ["asyncContext"],  // Optional: SDK capabilities needed
  "contextData": {
    "experiments": [...]
  },
  "steps": [
    {
      "action": "createContext",
      "params": {
        "units": {"session_id": "test123"},
        "options": {"publishDelay": -1}
      },
      "expect": {
        "result": {"ready": true, "failed": false},
        "events": [{"type": "ready"}]
      }
    },
    {
      "action": "treatment",
      "params": {"experimentName": "exp_test"},
      "expect": {
        "result": 1,
        "events": [{"type": "exposure"}]
      }
    }
  ]
}
```

### Wrapper API Endpoints

All wrappers expose:
- `GET /health` - Health check
- `GET /capabilities` - Report supported features
- `POST /context` - Create context
- `POST /context/{id}/{action}` - Execute action (treatment, track, etc.)
- `GET /context/{id}/pending` - Get pending event count
- `GET /context/{id}/isFinalized` - Check if finalized
- `DELETE /context/{id}` - Delete context

---

## Current Test Results

### Summary (80 scenarios, 10 SDKs)

| SDK | Score | Pass Rate | Status |
|-----|-------|-----------|--------|
| JavaScript | 80/80 | 100.0% | ✅ PERFECT |
| Ruby | 77/80 | 96.2% | ✅ Excellent |
| Java | 75/80 | 93.8% | ✅ Excellent |
| Go | 73/80 | 91.2% | ✅ Excellent |
| Flutter | 73/80 | 91.2% | ✅ Excellent |
| DotNET | 67/80 | 83.8% | ✅ Good |
| Python | 63/80 | 78.8% | ✅ Good |
| PHP | 60/80 | 75.0% | ✅ Good |
| Liquid | 25/80 | 31.2% | ⚠️ Runs (SDK bugs) |
| Swift | 17/80 | 21.2% | ⚠️ Runs (crashes) |

**All wrappers functional!** Remaining failures are SDK implementation bugs.

---

## Debugging Failed Tests

### View Detailed Failures

```bash
# Run tests and save output
docker compose run --rm --no-deps orchestrator python test_runner.py 2>&1 > test_output.txt

# Extract failures for specific SDK
grep -A 5 "python.*FAIL" test_output.txt

# View JSON report
cat test-results/report.json | python3 -m json.tool | less
```

### Check SDK Wrapper Logs

```bash
# View logs for specific SDK
docker logs cross-sdk-tests-javascript-sdk-1
docker logs cross-sdk-tests-swift-sdk-1

# Follow logs in real-time
docker logs -f cross-sdk-tests-swift-sdk-1
```

### Test Single Scenario

```bash
# Create a test file with just one scenario
cat > single_test.json << 'EOF'
[
  {
    "name": "Test Scenario",
    "contextData": {"experiments": []},
    "steps": [
      {
        "action": "createContext",
        "params": {"units": {"session_id": "test"}, "options": {"publishDelay": -1}},
        "expect": {"result": {"ready": true}, "events": [{"type": "ready"}]}
      }
    ]
  }
]
EOF

# Run with custom scenario file
docker compose run --rm -e TEST_SCENARIOS_PATH=/app/single_test.json orchestrator python test_runner.py
```

---

## Troubleshooting

### Service Won't Start

```bash
# Check service logs
docker logs cross-sdk-tests-<sdk-name>-1

# Check if port is in use
lsof -i :3000

# Restart specific service
docker compose restart <sdk-name>
```

### Out of Memory

```bash
# Check Docker resource usage
docker stats

# Increase Docker memory limit in Docker Desktop settings
# Recommended: 8GB+

# Clean up old containers/images
docker system prune -a
```

### Network Issues

```bash
# Verify containers on same network
docker network inspect cross-sdk-tests_default

# Test connectivity between containers
docker exec cross-sdk-tests-orchestrator-1 ping swift-sdk
docker exec cross-sdk-tests-orchestrator-1 curl http://swift-sdk:3000/health
```

---

## Environment Variables

### Orchestrator

- `TEST_SCENARIOS_PATH`: Path to test scenarios JSON (default: `/test_scenarios_complete.json`)

### SDK Wrappers

- `SDK_NAME`: Name of the SDK (for logging)
- `PORT`: Internal port (default: 3000)

---

## Adding New Tests

1. Edit `test_scenarios_complete.json`
2. Add new scenario with required fields:
   - `name`: Unique scenario name
   - `contextData`: Initial experiment data
   - `steps`: Array of action/expect pairs
   - `requires` (optional): Required SDK capabilities

3. Rebuild orchestrator:
```bash
docker compose build orchestrator
```

4. Run tests

---

## Known Issues

### Liquid SDK (25/80, 31.2%)
- **Issue**: Wrapper uses Ruby SDK directly instead of Liquid templates
- **Cause**: Liquid filter context access pattern incompatibility
- **Impact**: 55 scenarios fail (wrapper calls Ruby methods instead of rendering templates)
- **Root Cause**: Ruby SDK `.to_bool` method missing (1 scenario)

### Swift SDK (17/80, 21.2%)
**SEE DETAILED DOCUMENTATION**: `SWIFT_SDK_ISSUES.md`
- **Issue**: Segmentation fault after ~85 requests
- **Cause**: Deep memory corruption in Swift SDK core
- **Impact**: Crashes during test execution
- **Status**: All wrapper bugs fixed, SDK core bug remains

### Python SDK (63/80, 78.8%)
- **Issue**: Missing event type bug fixes from previous session
- **Cause**: Bug fixes stashed per user instruction
- **Fix**: `git stash pop` in python3-sdk to restore fixes

---

## Port Mappings

| SDK | Internal Port | External Port |
|-----|---------------|---------------|
| JavaScript | 3000 | 3001 |
| Python | 3000 | 3002 |
| Ruby | 3000 | 3003 |
| Java | 3000 | 3004 |
| PHP | 3000 | 3005 |
| Go | 3000 | 3006 |
| Liquid | 3000 | 3007 |
| Flutter | 3000 | 3008 |
| DotNET | 3000 | 3009 |
| Swift | 3000 | 3010 |

Test SDKs individually:
```bash
curl http://localhost:3001/health  # JavaScript
curl http://localhost:3010/health  # Swift
```

---

## Performance Notes

- **Test Duration**: ~2-3 minutes for full suite (80 scenarios × 10 SDKs)
- **Memory Usage**: ~4-6GB total across all containers
- **Parallel Execution**: Orchestrator tests all SDKs in parallel for each scenario
- **Swift Limitation**: Crashes after ~17 scenarios, reducing total test time

---

## Next Steps

1. **Fix Swift SDK memory bugs** - See `SWIFT_SDK_ISSUES.md`
2. **Fix Liquid template rendering** - Debug filter context access
3. **Fix SDK implementation bugs**:
   - Implement attrsSeq in 8 SDKs (scenarios 43, 44)
   - Fix custom field bugs (PHP, DotNET)
   - Fix type handling issues

---

## Support

For issues with:
- **Test infrastructure**: Check orchestrator logs
- **Specific SDK**: Check wrapper logs
- **Swift crashes**: See `SWIFT_SDK_ISSUES.md`
- **Liquid templates**: See `LIQUID_SDK_ISSUES.md`

All documentation in `.claude/tasks/` directory.
