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
