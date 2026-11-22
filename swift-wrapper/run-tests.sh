#!/bin/bash
# Swift SDK 2.x unit tests (swift-testing and XCTest). No PromiseKit run loop, so the process exits
# on its own; the timeout only guards against a hang, which is a failure.
set -o pipefail
timeout -k 5 600 swift test --skip-build 2>&1
