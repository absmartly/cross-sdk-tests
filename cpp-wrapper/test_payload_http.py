"""HTTP regressions for the running C++ wrapper (also run by the C++ CI lane).

CPP_WRAPPER_URL=http://127.0.0.1:3000 python3 cpp-wrapper/test_payload_http.py -v
"""

import json
import os
from pathlib import Path
import time
import unittest
from urllib.request import Request, urlopen
from uuid import uuid4


class PayloadHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = os.environ.get("CPP_WRAPPER_URL", "http://127.0.0.1:3000").rstrip("/")
        for attempt in range(60):
            try:
                with urlopen(cls.base_url + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if attempt == 59:
                raise RuntimeError(f"C++ wrapper did not become ready at {cls.base_url}")
            time.sleep(1)

        scenarios = json.loads((Path(__file__).resolve().parents[1] / "test_scenarios_complete.json").read_text())
        cls.payload = next(s["contextData"] for s in scenarios if s["name"].startswith("413 - "))
        null_experiment = next(e for e in cls.payload["experiments"] if e["name"] == "exp_sb_allow_null")
        if "switchbackClusterValues" not in null_experiment or null_experiment["switchbackClusterValues"] is not None:
            raise AssertionError("Scenario 413 must supply an explicit null allow-list")
        # Non-lexical order makes a switch to sorted JSON visible on either serving route.
        null_experiment["switchbackClusters"] = {"store_id": "zone_null", "customer_id": "zone_customer"}
        cls.payload_path = "/context_payload/http-regression-" + uuid4().hex
        request = Request(
            cls.base_url + cls.payload_path,
            data=json.dumps({"data": cls.payload}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(request, timeout=5) as response:
            if response.status != 200 or json.load(response) != {"success": True}:
                raise AssertionError("Payload PUT failed")

    def get_payload(self, suffix):
        with urlopen(self.base_url + self.payload_path + suffix, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_collector_retains_explicit_null(self):
        # Absent and null serve identically, so treatment assertions cannot catch this loss.
        served = self.get_payload("/context")
        experiment = next(e for e in served["experiments"] if e["name"] == "exp_sb_allow_null")
        self.assertIn("switchbackClusterValues", experiment)
        self.assertIsNone(experiment["switchbackClusterValues"])

    def test_both_get_routes_preserve_payload_and_cluster_order(self):
        for suffix in ("", "/context"):
            with self.subTest(route=self.payload_path + suffix):
                served = self.get_payload(suffix)
                self.assertEqual(served, self.payload)
                for expected, actual in zip(self.payload["experiments"], served["experiments"]):
                    self.assertEqual(list(actual["switchbackClusters"]), list(expected["switchbackClusters"]))


if __name__ == "__main__":
    unittest.main()
