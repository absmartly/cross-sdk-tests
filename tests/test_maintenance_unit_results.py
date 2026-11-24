"""Maintenance lanes use their SDK family's unit parser, including CLI failures."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import results_aggregator


# Verbatim excerpts of `swift test` on swift-sdk 2.x (feat/swift-2.0, c19460a) in
# swift:6.4-jammy. Linux runs every XCTest bundle first (empty here: the suite is
# all swift-testing), then one swift-testing run per test target.
XCTEST_EMPTY = """\
Test Suite 'All tests' started at 2026-11-24 14:04:55.465
Test Suite 'Debug-linux-aarch64.xctest' started at 2026-11-24 14:04:55.466
Test Suite 'Debug-linux-aarch64.xctest' passed at 2026-11-24 14:04:55.466
\t Executed 0 tests, with 0 failures (0 unexpected) in 0.0 (0.0) seconds
Test Suite 'All tests' passed at 2026-11-24 14:04:55.466
\t Executed 0 tests, with 0 failures (0 unexpected) in 0.0 (0.0) seconds
"""
SWIFT_TESTING_UI_TARGET = """\
\u25c7 Test run started.
\u21b3 Testing Library Version: 6.4.3 (ba0861fb203553c)
\u21b3 Target Platform: aarch64-unknown-linux-gnu
\u25c7 Suite DocSnippetTests started.
\u25c7 Test errors() started.
\u2714 Test errors() passed after 0.006 seconds.
\u2714 Test creatingContexts() passed after 0.007 seconds.
\u2714 Test stateAndEvents() passed after 0.008 seconds.
\u2714 Test readsAndOverrides() passed after 0.006 seconds.
\u2714 Suite DocSnippetTests passed after 0.008 seconds.
\u2714 Test run with 4 tests in 1 suite passed after 0.009 seconds.
"""
SWIFT_TESTING_SUCCESS = XCTEST_EMPTY + SWIFT_TESTING_UI_TARGET + """\
\u25c7 Test run started.
\u2714 Test chooseVariant() passed after 0.278 seconds.
\u2714 Test unknownFieldsPreserveFetchedContext(level:shape:) with 15 test cases passed after 4.364 seconds.
\u2714 Test matches150(fixture:) with 7 test cases passed after 8.078 seconds.
\u2714 Suite ParityTests passed after 8.167 seconds.
\u2714 Test run with 566 tests in 37 suites passed after 8.167 seconds.
"""
SWIFT_TESTING_FAILURE = XCTEST_EMPTY + SWIFT_TESTING_UI_TARGET + """\
\u25c7 Test run started.
\u2714 Test chooseVariant() passed after 0.278 seconds.
\u2718 Test setTimeoutNoRaceCondition() recorded an issue at ContextTests+Publishing.swift:550:3: Expectation failed: await eventually(timeout: 5) { env.publishCount == 1 }
\u21b3 await eventually(timeout: 5) { env.publishCount == 1 } \u2192 <not evaluated>
\u2718 Test setTimeoutNoRaceCondition() failed after 6.049 seconds with 1 issue.
\u2718 Test applicationVersionHeaderIsNormalisedOnGetAndPut(configured:expected:) with 16 test cases failed after 6.128 seconds with 15 issues.
\u2718 Suite ConnectionTests failed after 6.245 seconds with 21 issues.
\u2718 Test run with 566 tests in 37 suites failed after 9.244 seconds with 33 issues.
Note: Some test targets reported failures:
  - ABsmartlyTests (Swift Testing)
"""
# The same run with a two-test XCTestCase added to the test target.
MIXED_SUCCESS = """\
Test Suite 'All tests' started at 2026-11-24 14:10:08.375
Test Suite 'Debug-linux-aarch64.xctest' started at 2026-11-24 14:10:08.376
Test Suite 'ZZSampleXCTests' started at 2026-11-24 14:10:08.376
Test Case 'ZZSampleXCTests.testSamplePassOne' started at 2026-11-24 14:10:08.376
Test Case 'ZZSampleXCTests.testSamplePassOne' passed (0.001 seconds)
Test Case 'ZZSampleXCTests.testSamplePassTwo' started at 2026-11-24 14:10:08.377
Test Case 'ZZSampleXCTests.testSamplePassTwo' passed (0.0 seconds)
Test Suite 'ZZSampleXCTests' passed at 2026-11-24 14:10:08.377
\t Executed 2 tests, with 0 failures (0 unexpected) in 0.001 (0.001) seconds
Test Suite 'Debug-linux-aarch64.xctest' passed at 2026-11-24 14:10:08.377
\t Executed 2 tests, with 0 failures (0 unexpected) in 0.001 (0.001) seconds
Test Suite 'All tests' passed at 2026-11-24 14:10:08.377
\t Executed 2 tests, with 0 failures (0 unexpected) in 0.001 (0.001) seconds
""" + SWIFT_TESTING_UI_TARGET + """\
\u25c7 Test run started.
\u2714 Test matches150(fixture:) with 7 test cases passed after 8.242 seconds.
\u2714 Suite ParityTests passed after 8.246 seconds.
\u2714 Test run with 566 tests in 37 suites passed after 8.246 seconds.
"""
XCTEST_SUCCESS = "Test Case 'ExampleTests.works' passed (0.001 seconds).\n"


class MaintenanceUnitResultsTest(unittest.TestCase):
    OUTPUTS = {
        "java-v1": ("ExampleTest > works() PASSED\nBUILD SUCCESSFUL\n",
                    "ExampleTest > works() FAILED\n1 test completed, 1 failed\n"),
        "swift-v1": ("Test Case 'ExampleTests.works' passed (0.001 seconds).\n",
                     "Test Case 'ExampleTests.works' failed (0.001 seconds).\n"),
    }

    def aggregate(self, results):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "unit.json"
            report.write_text(json.dumps(results))
            return subprocess.run(
                [sys.executable, str(ROOT / "orchestrator/results_aggregator.py"),
                 str(report), "/dev/null"], capture_output=True, text=True)

    def test_successful_maintenance_suites_pass_the_real_cli(self):
        results = {sdk: {"output": output[0], "exit_code": 0}
                   for sdk, output in self.OUTPUTS.items()}
        result = self.aggregate(results)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("NO TESTS DETECTED", result.stdout)
        for sdk, (output, _) in self.OUTPUTS.items():
            with self.subTest(sdk=sdk):
                self.assertEqual(results_aggregator.parse_unit_test_output(sdk, output), (1, 0, 1))

    def test_empty_failed_or_nonzero_exit_suites_fail_the_real_cli(self):
        for sdk, (passing, failing) in self.OUTPUTS.items():
            for output, exit_code in (("", 0), (failing, 0), (failing, 1), (passing, 1)):
                with self.subTest(sdk=sdk, output=output, exit_code=exit_code):
                    result = self.aggregate({sdk: {"output": output, "exit_code": exit_code}})
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    if not output:
                        self.assertIn("NO TESTS DETECTED", result.stdout)


class SwiftUnitResultsTest(unittest.TestCase):
    """`swift test` output: swift-testing (Swift SDK 2.x) and XCTest, alone or together."""

    aggregate = MaintenanceUnitResultsTest.aggregate

    def assert_cli(self, sdk, output, exit_code, expected_returncode):
        result = self.aggregate({sdk: {"output": output, "exit_code": exit_code}})
        self.assertEqual(result.returncode, expected_returncode, result.stdout + result.stderr)
        return result

    def test_swift_testing_success_passes(self):
        self.assertEqual(results_aggregator.parse_unit_test_output("swift", SWIFT_TESTING_SUCCESS),
                         (570, 0, 570))
        result = self.assert_cli("swift", SWIFT_TESTING_SUCCESS, 0, 0)
        self.assertNotIn("NO TESTS DETECTED", result.stdout)

    def test_swift_testing_failure_fails(self):
        # Two failed tests; the run summary's 566 + the UI target's 4 is the total.
        self.assertEqual(results_aggregator.parse_unit_test_output("swift", SWIFT_TESTING_FAILURE),
                         (568, 2, 570))
        self.assert_cli("swift", SWIFT_TESTING_FAILURE, 0, 1)
        self.assert_cli("swift", SWIFT_TESTING_FAILURE, 1, 1)

    def test_failed_run_without_a_failed_test_line_still_fails(self):
        output = "\u2718 Test run with 3 tests in 1 suite failed after 0.1 seconds with 1 issue.\n"
        self.assertEqual(results_aggregator.parse_unit_test_output("swift", output), (2, 1, 3))

    def test_failures_without_run_summary_count_failed_tests_not_issues(self):
        # Truncated output (no "Test run with" summaries): only the per-test
        # lines count. The issue lines' messages quote "failed after N seconds",
        # so they would be miscounted as failed tests if not excluded.
        output = (
            "✔ Test chooseVariant() passed after 0.278 seconds.\n"
            "✘ Test retriesTimeout() recorded an issue at RetryTests.swift:40:5: "
            "Expectation failed: (error.message → \"publish failed after 3 seconds\") == \"\"\n"
            "✘ Test retriesTimeout() recorded an issue at RetryTests.swift:41:5: "
            "Expectation failed: (log → \"refresh failed after 2 seconds\") == \"\"\n"
            "✘ Test retriesTimeout() failed after 6.049 seconds with 2 issues.\n"
            "✘ Test backoff(count:) with 4 test cases recorded an issue at RetryTests.swift:70:3: "
            "Expectation failed: (state → \"failed after 1 seconds\") == \"ok\"\n"
            "✘ Test backoff(count:) with 4 test cases failed after 1.2 seconds with 1 issue.\n"
        )
        self.assertEqual(results_aggregator.parse_unit_test_output("swift", output), (1, 2, 3))

    def test_empty_output_fails(self):
        result = self.assert_cli("swift", "", 0, 1)
        self.assertIn("NO TESTS DETECTED", result.stdout)
        # Empty XCTest bundles alone are not a test result either.
        result = self.assert_cli("swift", XCTEST_EMPTY, 0, 1)
        self.assertIn("NO TESTS DETECTED", result.stdout)

    def test_nonzero_exit_fails(self):
        self.assert_cli("swift", SWIFT_TESTING_SUCCESS, 1, 1)

    def test_xctest_success_still_passes(self):
        for sdk in ("swift", "swift-v1"):
            with self.subTest(sdk=sdk):
                self.assertEqual(results_aggregator.parse_unit_test_output(sdk, XCTEST_SUCCESS), (1, 0, 1))
                self.assert_cli(sdk, XCTEST_SUCCESS, 0, 0)

    def test_mixed_xctest_and_swift_testing_success_combines_counts(self):
        # 2 XCTest cases + 4 + 566 swift-testing tests; per-test lines are not
        # added on top of the run summaries.
        self.assertEqual(results_aggregator.parse_unit_test_output("swift", MIXED_SUCCESS),
                         (572, 0, 572))
        result = self.assert_cli("swift", MIXED_SUCCESS, 0, 0)
        self.assertIn("572", result.stdout)


class SwiftSkippedTestsTest(unittest.TestCase):
    def test_skipped_swift_testing_tests_are_not_counted_as_passed(self):
        output = (
            "\u2714 Test a() passed after 0.001 seconds.\n"
            "\u2794 Test b() skipped.\n"
            "\u2714 Test run with 2 tests in 1 suite passed after 0.01 seconds.\n"
        )
        self.assertEqual(results_aggregator.parse_unit_test_output("swift", output), (1, 0, 2))
