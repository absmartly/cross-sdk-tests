"""Local collector service and the blocking gate's verifier, without Docker."""
import copy
import gzip
import http.client
import json
import os
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "local-collector"))
sys.path.insert(0, os.path.join(ROOT, "orchestrator"))

import local_collector as collector  # noqa: E402
import local_collector_gate as gate  # noqa: E402


class CollectorServerTest(unittest.TestCase):
    def setUp(self):
        self.server = collector.make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, timeout=5):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, json.loads(data)

    def test_context_is_deterministic_on_both_prefixes(self):
        for path in ("/context", "/v1/context", "/v1/context?application=e2e-tests"):
            status, body = self.request("GET", path, headers={"X-Agent": "absmartly-go-sdk/1.2.0"})
            self.assertEqual(status, 200)
            self.assertEqual(body, collector.context_data())
        ids = [e["id"] for e in body["experiments"]]
        self.assertEqual(ids, [collector.EXPERIMENT_ID, collector.SESSION_EXPERIMENT_ID])
        self.assertTrue(all(e["split"] == [0, 1] and e["trafficSplit"] == [0, 1] for e in body["experiments"]))
        status, recorded = self.request("GET", "/requests")
        self.assertEqual(len(recorded["requests"]), 3)
        self.assertIn(["X-Agent", "absmartly-go-sdk/1.2.0"], recorded["requests"][0]["headers"])

    def test_publish_body_and_headers_stored_verbatim(self):
        raw = '{"hashed":true,"goals":[{"name":"purchase","properties":{"amount":0.1,"n":null,"u":"用戶🧪","x":10.0}}]}'
        headers = {"X-Agent": "absmartly-python-sdk/0.4.0", "X-API-Key": "k", "X-Application": "e2e-tests",
                   "Content-Type": "application/json"}
        status, body = self.request("PUT", "/v1/context", raw.encode("utf-8"), headers)
        self.assertEqual((status, body), (200, {}))
        _, stored = self.request("GET", "/publishes")
        record = stored["publishes"][0]
        self.assertEqual(record["rawBody"], raw)
        self.assertEqual(__import__("base64").b64decode(record["decodedBodyBytes"]), raw.encode("utf-8"))
        self.assertEqual(record["json"], json.loads(raw))
        self.assertIsNone(record["json"]["goals"][0]["properties"]["n"])
        self.assertIn(["X-API-Key", "k"], record["headers"])
        self.assertEqual(record["seq"], 0)

    def test_gzip_body_is_decoded_and_bad_json_rejected(self):
        raw = json.dumps({"hashed": True}).encode()
        self.request("PUT", "/context", gzip.compress(raw), {"Content-Encoding": "gzip"})
        bad_status, _ = self.request("PUT", "/context", b"{not json", {})
        self.assertEqual(bad_status, 400)
        _, stored = self.request("GET", "/publishes")
        first = stored["publishes"][0]
        self.assertEqual(first["json"], {"hashed": True})
        self.assertEqual(first["contentEncoding"], "gzip")
        self.assertEqual(first["seq"], 0)

    def test_clear_and_unknown_routes(self):
        self.request("PUT", "/context", b"{}", {})
        self.assertEqual(self.request("DELETE", "/publishes")[0], 200)
        self.assertEqual(self.request("GET", "/publishes")[1], {"publishes": []})
        self.assertEqual(self.request("GET", "/v2/context")[0], 404)
        self.assertEqual(self.request("PUT", "/events", b"{}", {})[0], 404)


    def test_invalid_utf8_publish_returns_400_without_storing(self):
        for payload in (b"\xff", json.dumps({"hashed": True}).encode("utf-16")):
            status, body = self.request("PUT", "/context", payload, {"Content-Length": str(len(payload))})
            self.assertEqual(status, 400)
            self.assertIn("error", body)
            self.assertEqual(self.request("GET", "/publishes")[1], {"publishes": []})

    def test_malformed_content_length_and_gzip_return_400(self):
        status, _ = self.request("PUT", "/context", b"{}", {"Content-Length": "bad"})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"", {"Content-Length": "-1"})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"", {"Content-Length": str(16 * 1024 * 1024 + 1)})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"not-gzip", {"Content-Encoding": "gzip"})
        self.assertEqual(status, 400)

def _record(seq, body, agent="absmartly-javascript-sdk/1.14.0", app="e2e-tests", env="production"):
    return {"seq": seq, "json": body, "headers": [["X-Agent", agent], ["X-Application", app], ["X-Environment", env]]}


def _publishes_for(sdk, cases, mutate=None):
    """Independent fixture: the publishes a correct SDK sends for its plan."""
    out = []
    for case in cases:
        units = [{"type": t, "uid": gate.unit_hash(u)} for t, u in case["units"].items()]
        exposures = []
        for t in case["units"]:
            exp_id, name = ((collector.SESSION_EXPERIMENT_ID, collector.SESSION_EXPERIMENT_NAME) if t == "session_id"
                            else (collector.EXPERIMENT_ID, collector.EXPERIMENT_NAME))
            exposures.append({"id": exp_id, "name": name, "unit": t, "variant": 1, "assigned": True, "eligible": True})
        goals = []
        for p in case["goals"]:
            goal = {"name": "purchase", "achievedAt": 1}
            if p is not None:
                goal["properties"] = copy.deepcopy(p)
            goals.append(goal)
        out.append(_record(len(out), {"hashed": True, "units": units, "exposures": exposures, "goals": goals}))
    if mutate:
        mutate(out)
    return out


class GateVerifyTest(unittest.TestCase):
    def setUp(self):
        self.cases = gate.plan("abcd", "javascript")

    def check(self, mutate=None, versioned=False):
        return gate.verify("javascript", self.cases, _publishes_for("javascript", self.cases, mutate),
                           "e2e-tests", "production", versioned)

    def test_intact_publishes_pass_with_received_revenue(self):
        result = self.check()
        self.assertTrue(result["pass"], result["errors"])
        self.assertEqual(result["revenue"], "1000021.6")
        self.assertEqual(result["planned_revenue"], "1000021.6")
        self.assertEqual(result["exposures"], len(self.cases) + 1)

    def test_other_sdks_publishes_are_ignored(self):
        other = _publishes_for("go", gate.plan("abcd", "go"))
        result = gate.verify("javascript", self.cases, _publishes_for("javascript", self.cases) + other,
                             "e2e-tests", "production", False)
        self.assertTrue(result["pass"], result["errors"])

    def test_mutations_fail(self):
        def goals_of(name):
            return lambda out: out[[c["name"] for c in self.cases].index(name)]["json"]["goals"]
        mutations = {
            "missing goal": lambda out: goals_of("repeated")(out).pop(),
            "duplicate goal": lambda out: goals_of("strings")(out).append(dict(goals_of("strings")(out)[0])),
            "changed amount": lambda out: goals_of("purchase_amounts")(out)[2]["properties"].update(amount=0.10001),
            "nested changed": lambda out: goals_of("nested")(out)[0]["properties"]["order"].update(total=10),
            "bool flipped": lambda out: goals_of("booleans")(out)[0]["properties"].update(gift=False),
            "unicode mangled": lambda out: goals_of("strings")(out)[2]["properties"].update(label="Gr??e"),
            "null dropped": lambda out: goals_of("null_value")(out)[0].update(properties={}),
            "number as string": lambda out: goals_of("purchase_amounts")(out)[0]["properties"].update(amount="10"),
            "missing exposure": lambda out: out[0]["json"]["exposures"].clear(),
            "duplicate exposure": lambda out: out[1]["json"]["exposures"].append(dict(out[1]["json"]["exposures"][0])),
            "wrong variant": lambda out: out[0]["json"]["exposures"][0].update(variant=0),
            "wrong experiment": lambda out: out[0]["json"]["exposures"][0].update(id=1),
            "session exposure missing": lambda out: out[-1]["json"]["exposures"].pop(),
            "unhashed": lambda out: out[0]["json"].update(hashed=False),
            "wrong agent": lambda out: out[0].update(headers=[["X-Agent", "absmartly-go-sdk/1.0"]]),
            "wrong application": lambda out: out[0]["headers"].__setitem__(1, ["X-Application", "other"]),
            "wrong environment": lambda out: out[0]["headers"].__setitem__(2, ["X-Environment", "dev"]),
            "whole publish lost": lambda out: out.pop(0),
            "bad json": lambda out: out[0].update(json=None, parseError="x"),
            "unicode unit rehashed": lambda out: out[-2]["json"]["units"][0].update(uid=gate.unit_hash("ascii")),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertFalse(self.check(mutate)["pass"], label)

    def test_versioned_agent_required_unless_excepted(self):
        unversioned = lambda out: [r.update(headers=[["X-Agent", "absmartly-javascript-sdk"]] + r["headers"][1:])
                                   for r in out]
        self.assertTrue(self.check(unversioned, versioned=False)["pass"])
        self.assertFalse(self.check(unversioned, versioned=True)["pass"])

    def test_revenue_is_summed_from_received_amounts(self):
        # Same goal count, different amount: a count-based revenue would still pass.
        result = self.check(lambda out: out[0]["json"]["goals"][5]["properties"].update(amount=999999))
        self.assertFalse(result["pass"])
        self.assertEqual(result["revenue"], "1000020.6")

    def test_unit_hash_is_utf8_md5_base64url(self):
        # Independent literal: md5("e2e-0c11789c-go-94") as observed on test-1.
        self.assertEqual(gate.unit_hash("e2e-0c11789c-go-94"), "OSS6pz0j4iJ-ymv9M3DHvg")

    def test_canonical_numbers_by_value(self):
        self.assertEqual(gate._canonical({"a": 10}), gate._canonical({"a": 10.0}))
        self.assertNotEqual(gate._canonical({"a": 0.1}), gate._canonical({"a": 0.10000001}))
        self.assertNotEqual(gate._canonical({"a": True}), gate._canonical({"a": 1}))


    def test_gate_requires_application_and_environment_headers(self):
        # Missing (not just wrong) headers on a planned publish fail the gate.
        for name in ("X-Application", "X-Environment"):
            def drop(out, name=name):
                out[0]["headers"] = [h for h in out[0]["headers"] if h[0] != name]
            with self.subTest(name):
                result = self.check(drop)
                self.assertFalse(result["pass"])
                self.assertTrue(any(name[2:].lower() + " header" in e for e in result["errors"]), result["errors"])

class GateRunTest(unittest.TestCase):
    def test_run_drives_wrapper_and_fails_closed_on_wrapper_errors(self):
        server = collector.make_server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with mock.patch.object(gate, "drive", return_value=["case: wrapper 500"]), \
                 mock.patch.object(gate, "fetch_publishes", return_value=[]):
                result = gate.run({"javascript": "http://w"}, url, "e2e-tests", "production", "r1")
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(result["overall_pass"])
        self.assertIn("case: wrapper 500", result["sdks"]["javascript"]["errors"])

    def test_python_null_value_exception_follows_policy(self):
        cases = gate.plan("r", "python")
        publishes = _publishes_for("python", [c for c in cases if c["name"] != "null_value"])
        for record in publishes:
            record["headers"][0] = ["X-Agent", "absmartly-python-sdk/0.4.0"]
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=publishes), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": []})):
            result = gate.run({"python": "http://w"}, "http://c", "e2e-tests", "production", "r")
        self.assertTrue(result["overall_pass"], result["sdks"]["python"]["errors"])

if __name__ == "__main__":
    unittest.main()
