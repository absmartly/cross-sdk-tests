#!/bin/bash
# Swift test process hangs after completion due to PromiseKit run loop.
# Tests complete in ~120s (HTTP client tests make real network requests).
# Use timeout to force exit after tests finish.
#
# rc 124 (timed out) is only treated as success if the captured output proves
# the suite actually ran to completion: at least one passed test case, zero
# failed ones, AND the terminal XCTest summary line. A suite that deadlocks
# mid-run has passing cases so far but never prints that line, so requiring it
# rejects a partial run as the failure it is.
out=$(timeout -k 5 180 swift test --skip-build 2>&1)
rc=$?
echo "$out"

if [ $rc -eq 124 ]; then
    # XCTest prints "Test Case '...' passed" / "Test Case '...' failed".
    passed_count=$(printf '%s\n' "$out" | grep -c "Test Case.*passed")
    failed_count=$(printf '%s\n' "$out" | grep -c "Test Case.*failed")
    # Terminal summary only: XCTest prints "Test Suite '<class>' passed" and an
    # "Executed N tests..." line after EVERY class, so those match mid-run too.
    # "Test Suite 'All tests' passed" is printed once, at the very end, so it is
    # the only line that proves the whole run completed.
    completed=$(printf '%s\n' "$out" | grep -c "Test Suite 'All tests' passed")
    if [ "$passed_count" -ge 1 ] && [ "$failed_count" -eq 0 ] && [ "$completed" -ge 1 ]; then
        echo "run-tests: suite timed out after completing; treating as success ($passed_count passed, 0 failed, summary seen)"
        exit 0
    fi
    echo "run-tests: test suite hung (rc=124, passed=$passed_count, failed=$failed_count, completed=$completed) — no completed run detected" >&2
    exit 1
fi

exit $rc
