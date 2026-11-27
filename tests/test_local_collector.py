"""Local collector service and the blocking gate's verifier, without Docker."""
import copy
import gzip
import http.client
import itertools
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


# This release companion predates the consolidated Python e2e exception. Keep
# those two verifier regressions self-contained instead of changing lane policy.
_PYTHON_E2E = {"unsupported": {"nullPropertyValues": "reviewed test fixture"}}



class CollectorServerTest(unittest.TestCase):
    def setUp(self):
        self.server = collector.make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
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

    def test_gzip_body_is_decoded_and_bad_json_kept(self):
        raw = json.dumps({"hashed": True}).encode()
        self.request("PUT", "/context", gzip.compress(raw), {"Content-Encoding": "gzip"})
        self.request("PUT", "/context", b"{not json", {})
        _, stored = self.request("GET", "/publishes")
        first, second = stored["publishes"]
        self.assertEqual(first["json"], {"hashed": True})
        self.assertEqual(first["contentEncoding"], "gzip")
        self.assertIsNone(second["json"])
        self.assertIn("parseError", second)
        self.assertEqual([first["seq"], second["seq"]], [0, 1])

    def test_malformed_bytes_are_retained_and_rejected(self):
        for headers, body in (({}, b"\xff"), ({"Content-Encoding": "gzip"}, b"not-gzip")):
            with self.subTest(headers=headers):
                status, _ = self.request("PUT", "/context", body, headers)
                self.assertEqual(status, 400)
        _, stored = self.request("GET", "/publishes")
        self.assertEqual(len(stored["publishes"]), 2)
        self.assertTrue(all(record["json"] is None and "parseError" in record for record in stored["publishes"]))

    def test_clear_and_unknown_routes(self):
        self.request("PUT", "/context", b"{}", {})
        self.assertEqual(self.request("DELETE", "/publishes")[0], 200)
        self.assertEqual(self.request("GET", "/publishes")[1], {"publishes": []})
        self.assertEqual(self.request("GET", "/v2/context")[0], 404)
        self.assertEqual(self.request("PUT", "/events", b"{}", {})[0], 404)

    def test_malformed_content_length_and_gzip_return_400(self):
        status, _ = self.request("PUT", "/context", b"{}", {"Content-Length": "bad"})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"not-gzip", {"Content-Encoding": "gzip"})
        self.assertEqual(status, 400)


def _record(seq, body, agent="absmartly-javascript-sdk/1.14.0", app="e2e-tests", env="production"):
    body.setdefault("publishedAt", 1700000000000)
    return {"seq": seq, "receivedAt": 1700000000100, "json": body,
            "headers": [["X-Agent", agent], ["X-Application", app], ["X-Environment", env], ["X-Application-Version", "0"]]}


def _publishes_for(sdk, cases, mutate=None):
    """Independent fixture: the publishes a correct SDK sends for its plan."""
    out = []
    for case in cases:
        units = [{"type": t, "uid": gate.unit_hash(u)} for t, u in case["units"].items()]
        exposures = []
        for t in case["units"]:
            exp_id, name = ((collector.SESSION_EXPERIMENT_ID, collector.SESSION_EXPERIMENT_NAME) if t == "session_id"
                            else (collector.EXPERIMENT_ID, collector.EXPERIMENT_NAME))
            exposures.append({"id": exp_id, "name": name, "unit": t, "variant": 1, "assigned": True, "eligible": True, "exposedAt": 1700000000000,
                              "overridden": False, "fullOn": False, "custom": False,
                              "audienceMismatch": False, "ruleOverride": False})
        goals = []
        for p in case["goals"]:
            goal = {"name": "purchase", "achievedAt": 1700000000000}
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

    def test_malformed_revenue_fails_closed_without_crashing(self):
        for value in ({}, "not-a-number", "sNaN", []):
            with self.subTest(value=value):
                result = self.check(lambda out, value=value:
                                    out[0]["json"]["goals"][5]["properties"].update(amount=value))
                self.assertFalse(result["pass"])
                self.assertTrue(any("purchase.amount is not numeric" in error for error in result["errors"]))
        # Finite on its own, but the revenue sum overflows.
        result = self.check(lambda out: out[0]["json"]["goals"][5]["properties"].update(amount="1e999999999"))
        self.assertFalse(result["pass"])
        self.assertIsNone(result["revenue"])
        self.assertTrue(any("sum is not representable" in error for error in result["errors"]))

    def test_rejects_json_type_confusions(self):
        cases = self.cases
        for label, mutate in (
            ("boolean variant", lambda out: out[0]["json"]["exposures"][0].update(variant=True)),
            ("numeric wrapper object", lambda out: out[0]["json"]["goals"][0]["properties"].update(amount={"$n": "10"})),
        ):
            with self.subTest(label):
                self.assertFalse(self.check(mutate)["pass"])

        # Independent literal: md5("e2e-0c11789c-go-94") as observed on test-1.
        self.assertEqual(gate.unit_hash("e2e-0c11789c-go-94"), "OSS6pz0j4iJ-ymv9M3DHvg")

    def test_canonical_numbers_by_value(self):
        self.assertEqual(gate._canonical({"a": 10}), gate._canonical({"a": 10.0}))
        self.assertNotEqual(gate._canonical({"a": 0.1}), gate._canonical({"a": 0.10000001}))
        self.assertNotEqual(gate._canonical({"a": True}), gate._canonical({"a": 1}))
        D = gate.Decimal
        self.assertEqual(gate._canonical({"a": 10}), gate._canonical({"a": D("1e1")}))
        self.assertEqual(gate._canonical({"a": 0}), gate._canonical({"a": D("-0.0")}))
        # Past Decimal's default 28 significant digits: distinct values must stay distinct.
        self.assertNotEqual(gate._canonical({"a": D("0.1")}), gate._canonical({"a": D("0.10000000000000000000000000001")}))
        self.assertNotEqual(gate._canonical({"a": D("1e999999999")}), gate._canonical({"a": D("1e999999998")}))

    def test_exact_sum_traps_rounding(self):
        D = gate.Decimal
        self.assertEqual(gate._exact_sum([D("0.1"), D("0.2")]), D("0.3"))
        with self.assertRaises(ArithmeticError):
            gate._exact_sum([D("1e500"), D("1e-600")])

    def test_missing_application_and_environment_headers_fail(self):
        result = self.check(lambda out: out[0].update(headers=out[0]["headers"][:1]))
        self.assertFalse(result["pass"])
        self.assertTrue(any("application header None" in e for e in result["errors"]))
        self.assertTrue(any("environment header None" in e for e in result["errors"]))


class GateRunTest(unittest.TestCase):
    def test_run_drives_wrapper_and_fails_closed_on_wrapper_errors(self):
        server = collector.make_server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with mock.patch.object(gate, "drive", return_value=["case: wrapper 500"]), \
                 mock.patch.object(gate, "fetch_publishes", return_value=([], None)):
                result = gate.run({"javascript": "http://w"}, url, "e2e-tests", "production", "r1")
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(result["overall_pass"])
        self.assertIn("case: wrapper 500", result["sdks"]["javascript"]["errors"])

    @mock.patch.object(gate, "load_policy", return_value={"sdks": {"python": {"e2e": _PYTHON_E2E}}})
    def test_python_null_value_exception_follows_policy(self, _policy):
        cases = gate.plan("r", "python")
        publishes = _publishes_for("python", [c for c in cases if c["name"] != "null_value"])
        for record in publishes:
            record["headers"][0] = ["X-Agent", "absmartly-python-sdk/0.4.0"]
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=(publishes, None)), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": [_record(i, {}, agent="absmartly-python-sdk/0.4.0") for i in range(9)]})):
            result = gate.run({"python": "http://w"}, "http://c", "e2e-tests", "production", "r")
        self.assertTrue(result["overall_pass"], result["sdks"]["python"]["errors"])

    def run_python(self, mutate=None):
        cases = gate.plan("r", "python")
        null_case = next(c for c in cases if c["name"] == "null_value")
        publishes = _publishes_for("python", cases)
        # Python's reviewed exception: its null property arrives as {} rather than {"amount": null}.
        publishes[cases.index(null_case)]["json"]["goals"][0]["properties"] = {}
        for record in publishes:
            record["headers"][0] = ["X-Agent", "absmartly-python-sdk/0.4.0"]
        if mutate:
            mutate(publishes)
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=(publishes, None)), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": [_record(i, {}, agent="absmartly-python-sdk/0.4.0") for i in range(9)]})):
            return gate.run({"python": "http://w"}, "http://c", "e2e-tests", "production", "r"), cases

    def test_an_excepted_unit_cannot_ride_on_an_applicable_case_publish(self):
        names = [c["name"] for c in gate.plan("r", "python")]

        def add_mixed(out):
            mixed = copy.deepcopy(out[names.index("strings")])
            mixed["seq"] = len(out)
            mixed["json"]["units"] += out[names.index("null_value")]["json"]["units"]
            mixed["json"]["exposures"], mixed["json"]["goals"] = [], []
            out.append(mixed)
        result, _ = self.run_python(add_mixed)
        self.assertFalse(result["overall_pass"])
        self.assertTrue(any("carries units from more than one planned case" in e
                            for e in result["sdks"]["python"]["errors"]), result["sdks"]["python"]["errors"])

    @mock.patch.object(gate, "load_policy", return_value={"sdks": {"python": {"e2e": _PYTHON_E2E}}})
    def test_the_python_null_property_exception_still_checks_that_publishs_transport(self, _policy):
        result, _ = self.run_python()
        self.assertTrue(result["overall_pass"], result["sdks"]["python"]["errors"])

        def corrupt(out, name):
            index = [c["name"] for c in gate.plan("r", "python")].index(name)
            out[index]["headers"] = []
            out[index]["json"]["hashed"] = False
        for name in ("null_value", "strings"):
            with self.subTest(case=name):
                result, _ = self.run_python(lambda out, name=name: corrupt(out, name))
                self.assertFalse(result["overall_pass"])
                errors = result["sdks"]["python"]["errors"]
                for fragment in ("agent None", "application header None", "environment header None", "hashed=False"):
                    self.assertTrue(any(fragment in e for e in errors), (fragment, errors))

    def test_an_exposure_from_one_case_cannot_ride_on_another_cases_publish(self):
        names = [c["name"] for c in gate.plan("r", "javascript")]

        def move_session(out):
            source = out[names.index("multiple_units")]["json"]
            target = out[names.index("purchase_amounts")]
            mixed = copy.deepcopy(target)
            mixed["seq"] = len(out)
            mixed["json"]["units"] += [u for u in source["units"] if u["type"] == "session_id"]
            mixed["json"]["exposures"] = [e for e in source["exposures"] if e["unit"] == "session_id"]
            mixed["json"]["goals"] = []
            source["units"] = [u for u in source["units"] if u["type"] != "session_id"]
            source["exposures"] = [e for e in source["exposures"] if e["unit"] != "session_id"]
            out.append(mixed)
        result = self.run_with([], move_session)
        self.assertFalse(result["overall_pass"])
        self.assertTrue(any("carries units from more than one planned case" in e
                            for e in result["sdks"]["javascript"]["errors"]), result["sdks"]["javascript"]["errors"])

    def run_with(self, extra, mutate=None):
        cases = gate.plan("r", "javascript")
        publishes = _publishes_for("javascript", cases, mutate) + extra
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=(publishes, None)), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": [_record(i, {}) for i in range(9)]})):
            return gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r")

    def test_every_publish_must_belong_to_the_plan(self):
        self.assertTrue(self.run_with([])["overall_pass"])
        self.assertNotIn("transport_errors", self.run_with([]))
        stray = _record(99, {"hashed": True, "units": [{"type": "user_id", "uid": gate.unit_hash("stray")}],
                             "exposures": [], "goals": []})
        for label, extra in (("unplanned unit", [stray]), ("no units", [_record(99, {"hashed": True})]),
                             ("malformed json", [{"seq": 99, "json": None, "parseError": "x", "headers": []}])):
            with self.subTest(label):
                self.assertFalse(self.run_with(extra)["overall_pass"], label)

    def test_a_case_without_its_own_publish_fails_even_with_matching_totals(self):
        # Every exposure and goal still arrives, but multiple_units' session unit and exposure ride on
        # purchase_amounts' publish, so purchase_amounts has no publish carrying only its own units.
        def merge(out):
            names = [c["name"] for c in gate.plan("r", "javascript")]
            donor, target = out[names.index("multiple_units")]["json"], out[names.index("purchase_amounts")]["json"]
            session = [u for u in donor["units"] if u["type"] == "session_id"]
            donor["units"] = [u for u in donor["units"] if u["type"] != "session_id"]
            target["units"] += session
            target["exposures"] += [e for e in donor["exposures"] if e["unit"] == "session_id"]
            donor["exposures"] = [e for e in donor["exposures"] if e["unit"] != "session_id"]
        result = self.run_with([], merge)
        self.assertFalse(result["overall_pass"])
        self.assertEqual([e for e in result["sdks"]["javascript"]["errors"] if e.startswith("case ")],
                         ["case purchase_amounts: no publish carrying only this case's units"])

    def test_malformed_unit_entries_fail_with_a_diagnostic(self):
        for label, bad in (("string entry", "user_id"), ("list uid", {"type": "user_id", "uid": []}),
                           ("missing type", {"uid": "x"})):
            with self.subTest(label):
                result = self.run_with([], lambda out, bad=bad: out[0]["json"]["units"].append(bad))
                self.assertFalse(result["overall_pass"])
                self.assertTrue(any("malformed units entry" in e for e in result["transport_errors"]))
        result = self.run_with([], lambda out: out[0]["json"].update(units="user_id"))
        self.assertTrue(any("units is not an array" in e for e in result["transport_errors"]))

    def test_malformed_exposure_goal_and_header_entries_fail_with_a_diagnostic(self):
        for label, mutate, diagnostic in (
            ("exposure entry", lambda out: out[0]["json"]["exposures"].append("x"), "malformed exposures entry"),
            ("list exposure unit", lambda out: out[0]["json"]["exposures"][0].update(unit=["x"]), "malformed exposures entry"),
            ("object goal name", lambda out: out[0]["json"]["goals"][0].update(name={}), "malformed goals entry"),
            ("goals not array", lambda out: out[0]["json"].update(goals="x"), "goals is not an array"),
            ("header pair", lambda out: out[0].update(headers=[["X-Agent"]] + out[0]["headers"][1:]), "agent None"),
            ("non-dict record", lambda out: out.append(7), "malformed collector record"),
            ("record without seq", lambda out: out.append({"json": {"hashed": True}}), "malformed collector record"),
        ):
            with self.subTest(label):
                result = self.run_with([], mutate)
                self.assertFalse(result["overall_pass"])
                errors = result["sdks"]["javascript"]["errors"]
                self.assertTrue(any(diagnostic in e for e in errors), errors)

    def test_malformed_request_log_is_a_reported_failure(self):
        cases = gate.plan("r", "javascript")
        for label, body in (("null requests", {"requests": None}), ("bare list", ["x"]), ("bad row", {"requests": ["x"]})):
            with self.subTest(label):
                with mock.patch.object(gate, "drive", return_value=[]), \
                     mock.patch.object(gate, "fetch_publishes", return_value=(_publishes_for("javascript", cases), None)), \
                     mock.patch.object(gate.requests, "delete"), \
                     mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda body=body: body)):
                    result = gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r")
                self.assertFalse(result["overall_pass"])


class FetchPublishesTest(unittest.TestCase):
    PLANNED = {("user_id", "u")}
    MINE = {"seq": 0, "json": {"units": [{"type": "user_id", "uid": "u"}]}}
    STRAY = {"seq": 1, "json": {"units": [{"type": "user_id", "uid": "stray"}]}}

    def fetch(self, reads, expected_cases, clock):
        reads = iter(reads)
        get = mock.Mock(side_effect=lambda *a, **k: mock.Mock(json=lambda: {"publishes": next(reads)}))
        with mock.patch.object(gate.requests, "get", get), mock.patch.object(gate.time, "sleep"), \
             mock.patch.object(gate.time, "monotonic", side_effect=clock):
            publishes, error = gate.fetch_publishes("http://c", expected_cases, timeout=30)
            self.assertIsNone(error)
            return len(publishes), get.call_count

    def test_waits_past_stable_reads_until_the_expected_count_arrives(self):
        m = self.MINE
        self.assertEqual(self.fetch([[], [], [], [m, m], [m, m, m]], [{("user_id", "u")}], iter([0] + [1] * 10)), (3, 5))

    def test_stray_publishes_do_not_count_toward_expected(self):
        m, s = self.MINE, self.STRAY
        m2 = {"seq": 2, "json": {"units": [{"type": "user_id", "uid": "missing"}]}}
        self.assertEqual(self.fetch([[m, s], [m, s, m], [m, s, m, m2], [m, s, m, m2]],
                                    [{("user_id", "u")}, {("user_id", "missing")}], iter([0] + [1] * 10)), (4, 4))

    def test_timeout_returns_what_arrived(self):
        m = self.MINE
        self.assertEqual(self.fetch([[], [m], [m]], [{("user_id", "u")}, {("user_id", "missing")}], iter([0, 10, 20, 31])), (1, 3))

    def test_one_publish_combining_two_cases_does_not_complete_both(self):
        both = {"seq": 0, "json": {"units": [{"type": "user_id", "uid": "u"}, {"type": "user_id", "uid": "missing"}]}}
        self.assertEqual(self.fetch([[both], [both], [both]], [{("user_id", "u")}, {("user_id", "missing")}],
                                    iter([0, 10, 20, 31])), (1, 3))

    def test_a_multi_unit_case_completes_from_its_own_publish(self):
        multi = {"seq": 0, "json": {"units": [{"type": "user_id", "uid": "mu"}, {"type": "session_id", "uid": "mu-s"}]}}
        self.assertEqual(self.fetch([[multi], [multi]], [{("user_id", "mu"), ("session_id", "mu-s")}],
                                    iter([0] + [1] * 10)), (1, 2))

    def test_malformed_unit_entries_do_not_abort_polling(self):
        bad = {"seq": 0, "json": {"units": [{"type": "user_id", "uid": []}, "user_id", {"type": "user_id", "uid": "u"}]}}
        self.assertEqual(self.fetch([[bad], [bad]], [{("user_id", "u")}], iter([0] + [1] * 10)), (1, 2))


class WrapperResponseTest(unittest.TestCase):
    def drive_with(self, payload):
        created = mock.Mock(status_code=200, text=str(payload), json=mock.Mock(return_value=payload))
        with mock.patch.object(gate.requests, "post", return_value=created) as post, \
             mock.patch.object(gate.requests, "delete"):
            errors = gate.drive("javascript", "http://w", gate.plan("r", "javascript")[:1])
        return errors, post.call_count

    def test_a_wrong_shaped_2xx_context_response_is_a_reported_wrapper_failure(self):
        for label, payload in (("null result", {"result": None}), ("string result", {"result": "ok"}),
                               ("list body", ["ok"]), ("missing result", {})):
            with self.subTest(label):
                errors, posts = self.drive_with(payload)
                self.assertEqual(len(errors), 1)
                self.assertIn("invalid /context response", errors[0])
                self.assertEqual(posts, 1)

    def test_malformed_json_in_a_2xx_context_response_is_a_reported_wrapper_failure(self):
        created = mock.Mock(status_code=200, text="{not json", json=mock.Mock(side_effect=ValueError("bad json")))
        with mock.patch.object(gate.requests, "post", return_value=created), mock.patch.object(gate.requests, "delete"):
            errors = gate.drive("javascript", "http://w", gate.plan("r", "javascript")[:1])
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid /context response", errors[0])


class CollectorEndToEndTest(unittest.TestCase):
    """The plan through the real collector server: what it stores is what the gate judges."""

    def setUp(self):
        self.server = collector.make_server("127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def put(self, body_text, headers, compress=False):
        body = body_text.encode("utf-8")
        headers = dict(headers, **{"Content-Type": "application/json"})
        if compress:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("PUT", "/v1/context", body=body, headers=headers)
        conn.getresponse().read()
        conn.close()

    def run_gate(self, edit=lambda text: text, compress=False):
        gate.requests.delete(f"{self.url}/publishes", timeout=5).raise_for_status()
        cases = gate.plan("r", "javascript")
        for record in _publishes_for("javascript", cases):
            gate.requests.get(f"{self.url}/v1/context", headers=dict(record["headers"]), timeout=5).raise_for_status()
            now = int(__import__('time').time() * 1000)
            record["json"]["publishedAt"] = now
            for entry in record["json"]["exposures"]:
                entry["exposedAt"] = now
            for entry in record["json"]["goals"]:
                entry["achievedAt"] = now
            self.put(edit(json.dumps(record["json"], ensure_ascii=False)), dict(record["headers"]), compress)
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.time, "sleep"):
            return gate.run({"javascript": "http://w"}, self.url, "e2e-tests", "production", "r")

    def test_the_unchanged_plan_passes_through_the_real_collector(self):
        for compress in (False, True):
            with self.subTest(gzip=compress):
                result = self.run_gate(compress=compress)
                self.assertTrue(result["overall_pass"], result)
                self.assertEqual(result["sdks"]["javascript"]["revenue"], "1000021.6")

    def test_a_numerically_distinct_wire_amount_fails(self):
        # 0.1 and 0.100000000000000001 are the same float; only the raw body distinguishes them.
        # 0.10000000000000000000000000001 differs from 0.1 past Decimal's default 28 digits.
        for token in ("0.100000000000000001", "0.10000000000000000000000000001"):
            for compress in (False, True):
                with self.subTest(token=token, gzip=compress):
                    result = self.run_gate(lambda text: text.replace('"amount": 0.1}', f'"amount": {token}}}'), compress)
                    self.assertFalse(result["overall_pass"])
                    errors = result["sdks"]["javascript"]["errors"]
                    self.assertTrue(any("purchase.amount sum" in e for e in errors), errors)
                    self.assertTrue(any(e.startswith("goal purchase") for e in errors), errors)

    def test_an_out_of_range_wire_number_is_a_reported_failure(self):
        # Each is a distinct value from the planned 10, compared exactly; the last cannot even be
        # represented, so its publish is reported unparseable (and its case never completes).
        for token, diagnostic in (("1e999999999", "goal purchase"), ("1e1000000", "goal purchase"),
                                  ("-1e99999999", "goal purchase"), ("1e9999999999999999999", "raw body does not parse")):
            with self.subTest(token=token):
                with mock.patch.object(gate.time, "monotonic", side_effect=itertools.count(0, 100)):
                    result = self.run_gate(lambda text, token=token: text.replace('"amount": 10}', f'"amount": {token}}}'))
                self.assertFalse(result["overall_pass"])
                errors = result["sdks"]["javascript"]["errors"]
                self.assertTrue(any(diagnostic in e for e in errors), errors)
                json.dumps(result)

    def test_an_equivalent_number_spelling_still_matches(self):
        result = self.run_gate(lambda text: text.replace('"amount": 10}', '"amount": 10.0}'))
        self.assertTrue(result["overall_pass"], result["sdks"]["javascript"]["errors"])


class PublishLogFailureTest(unittest.TestCase):
    def run_with_reads(self, reads):
        reads = iter(reads)
        cases = gate.plan("r", "javascript")
        good = _publishes_for("javascript", cases)

        def get(url, **_):
            if url.endswith("/requests"):
                return mock.Mock(json=lambda: {"requests": [_record(i, {}) for i in range(9)]})
            outcome = next(reads)
            if isinstance(outcome, Exception):
                raise outcome
            return mock.Mock(raise_for_status=mock.Mock(), json=lambda outcome=outcome: outcome)

        with mock.patch.object(gate, "drive", return_value=[]), mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", side_effect=get), mock.patch.object(gate.time, "sleep"):
            return gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r"), good

    def test_an_unavailable_publish_log_is_a_reported_failure_on_either_read(self):
        good = {"publishes": _publishes_for("javascript", gate.plan("r", "javascript"))}
        for label, reads in (("first read times out", [gate.requests.ReadTimeout("slow")]),
                             ("settle read times out", [good, gate.requests.ReadTimeout("slow")]),
                             ("no publishes key", [{"oops": []}]),
                             ("http 503", [gate.requests.HTTPError("503")])):
            with self.subTest(label):
                result, _ = self.run_with_reads(reads)
                self.assertFalse(result["overall_pass"])
                self.assertTrue(any("collector publish log unavailable" in e for e in result["transport_errors"]))
                json.dumps(result)


class ReportWriteTest(unittest.TestCase):
    def test_a_lone_surrogate_in_a_property_still_writes_a_parseable_failing_report(self):
        import subprocess
        import tempfile
        server = collector.make_server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        try:
            cases = gate.plan("abcd", "javascript")
            records = _publishes_for("javascript", cases)
            for record in records:
                text = json.dumps(record["json"], ensure_ascii=True).replace('"EUR"', '"\\ud800"')
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("PUT", "/v1/context", body=text.encode("ascii"), headers=dict(record["headers"]))
                conn.getresponse().read()
                conn.close()
            with tempfile.TemporaryDirectory() as tmp:
                report = os.path.join(tmp, "report.json")
                code = (
                    "import sys, runpy; from unittest import mock; sys.path[:0] = sys.argv[1:3];"
                    "import local_collector_gate as g;"
                    "mock.patch.object(g, 'drive', return_value=[]).start();"
                    "mock.patch.object(g.requests, 'delete').start();"
                    "mock.patch.object(g.os, 'urandom', return_value=bytes.fromhex('abcd')).start();"
                    "mock.patch.object(g.time, 'sleep').start();"
                    "sys.argv = ['gate', '--sdk', 'javascript', '--collector', sys.argv[3], '--report', sys.argv[4]];"
                    "g.main()")
                done = subprocess.run([sys.executable, "-c", code, os.path.join(ROOT, "orchestrator"),
                                       os.path.join(ROOT, "local-collector"), f"http://127.0.0.1:{port}", report],
                                      capture_output=True, text=True, timeout=120)
                self.assertEqual(done.returncode, 1, done.stderr)
                with open(report, encoding="utf-8") as src:
                    written = json.load(src)
            self.assertFalse(written["overall_pass"])
            self.assertTrue(any("\ud800" in e for e in written["sdks"]["javascript"]["errors"]))
            self.assertIn("\\ud800", done.stdout)
        finally:
            server.shutdown()
            server.server_close()


class EmptySelectionTest(unittest.TestCase):
    """A release-gate run that selects no SDK must fail, never write a passing report."""

    def test_the_cli_rejects_a_separator_only_selection(self):
        import subprocess
        import tempfile
        for selection in (",", " , ", ",,"):
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as tmp:
                report = os.path.join(tmp, "report.json")
                done = subprocess.run([sys.executable, os.path.join(ROOT, "orchestrator", "local_collector_gate.py"),
                                       "--sdk", selection, "--collector", "http://127.0.0.1:9", "--report", report],
                                      capture_output=True, text=True, timeout=60)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("selects no SDK", done.stderr)
                self.assertFalse(os.path.exists(report))

    def test_the_launcher_rejects_a_separator_only_selection_before_starting_anything(self):
        import shutil
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as bin_dir:
            calls = os.path.join(bin_dir, "calls")
            stub = os.path.join(bin_dir, "docker")
            with open(stub, "w") as out:
                out.write(f"#!/bin/sh\necho \"$@\" >> {calls}\n")
            os.chmod(stub, 0o755)
            for selection in (",", " , "):
                with self.subTest(selection=selection):
                    done = subprocess.run([shutil.which("bash"), os.path.join(ROOT, "run-local-collector-tests.sh"),
                                           "--sdk", selection, "--skip-build"], capture_output=True, text=True,
                                          timeout=60, env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}"))
                    self.assertEqual(done.returncode, 1, done.stderr)
                    self.assertIn("selects no SDK", done.stderr)
                    self.assertFalse(os.path.exists(calls), "docker must not be called")

    def test_run_refuses_an_empty_sdk_map(self):
        with self.assertRaises(ValueError):
            gate.run({}, "http://c", "e2e-tests", "production", "r")


class LauncherStaleReportTest(unittest.TestCase):
    def test_a_failed_launch_never_leaves_an_earlier_pass_report(self):
        # The launcher runs from a temporary checkout copy, so the real release report is never touched.
        import shutil
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as checkout, tempfile.TemporaryDirectory() as bin_dir:
            launcher = os.path.join(checkout, "run-local-collector-tests.sh")
            shutil.copy2(os.path.join(ROOT, "run-local-collector-tests.sh"), launcher)
            report = os.path.join(checkout, "test-results", "local-collector.json")
            os.makedirs(os.path.dirname(report))
            with open(report, "w") as out:
                json.dump({"run_id": "previous-run", "overall_pass": True}, out)
            stub = os.path.join(bin_dir, "docker")
            with open(stub, "w") as out:
                out.write("#!/bin/sh\ncase \"$*\" in *\" up \"*) exit 37;; esac\nexit 0\n")
            os.chmod(stub, 0o755)
            done = subprocess.run([shutil.which("bash"), launcher, "--sdk", "swift", "--skip-build"],
                                  capture_output=True, text=True, timeout=60,
                                  env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}"))
            self.assertEqual(done.returncode, 37, done.stderr)
            self.assertFalse(os.path.exists(report), "the earlier PASS report must be gone")


class CollectorResetTest(unittest.TestCase):
    def test_a_failed_reset_writes_a_fresh_failing_report_and_drives_no_sdk(self):
        import subprocess
        import tempfile
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Unavailable(BaseHTTPRequestHandler):
            def do_DELETE(self):
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_GET = do_POST = do_PUT = do_DELETE

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Unavailable)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = os.path.join(tmp, "report.json")
                with open(report, "w") as out:
                    json.dump({"overall_pass": True, "sdks": {"javascript": {"pass": True}}}, out)
                url = f"http://127.0.0.1:{server.server_address[1]}"
                done = subprocess.run([sys.executable, os.path.join(ROOT, "orchestrator", "local_collector_gate.py"),
                                       "--sdk", "javascript", "--collector", url, "--report", report],
                                      capture_output=True, text=True, timeout=60,
                                      env=dict(os.environ, SDK_URLS_OVERRIDE=f"javascript={url}"))
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                with open(report) as src:
                    written = json.load(src)
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(written["overall_pass"])
        self.assertEqual(written["sdks"], {})
        self.assertTrue(any("collector reset failed" in e for e in written["transport_errors"]))

    def test_drive_preserves_primary_case_error_when_cleanup_also_fails(self):
        cases = gate.plan("r", "javascript")[:1]
        response = mock.Mock(status_code=200, text='{"result":{"contextId":"c","ready":true,"failed":false}}',
                             json=lambda: {"result": {"contextId": "c", "ready": True, "failed": False}})
        def post(url, **kwargs):
            if url.endswith("/context"):
                return response
            bad = mock.Mock()
            bad.raise_for_status.side_effect = requests.HTTPError("treatment failed")
            return bad
        import requests
        with mock.patch.object(gate.requests, "post", side_effect=post), \
             mock.patch.object(gate.requests, "delete", side_effect=requests.ReadTimeout("cleanup slow")):
            errors = gate.drive("javascript", "http://w", cases)
        self.assertTrue(any("wrapper call failed" in e and "treatment failed" in e for e in errors))
        self.assertTrue(any("cleanup failed" in e for e in errors))

    def test_run_drives_no_sdk_when_the_reset_fails(self):
        for error in (gate.requests.ConnectionError("down"), ValueError("bad json"), TypeError("bad response")):
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(gate.requests, "delete", side_effect=error), \
                 mock.patch.object(gate, "drive") as drive:
                result = gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r")
                drive.assert_not_called()
                self.assertFalse(result["overall_pass"])
                self.assertTrue(any("collector reset failed" in e for e in result["transport_errors"]))



class ReleaseGateRegressionTest(unittest.TestCase):
    def run_rows(self, rows, publishes=None):
        cases = gate.plan('regression', 'dart')
        if publishes is None:
            publishes = _publishes_for('dart', cases)
            for record in publishes:
                record['headers'][0] = ['X-Agent', 'absmartly-dart-sdk/2.2.0']
        with mock.patch.object(gate, 'drive', return_value=[]), \
             mock.patch.object(gate, 'fetch_publishes', return_value=(publishes, None)), \
             mock.patch.object(gate.requests, 'delete'), \
             mock.patch.object(gate.requests, 'get', return_value=mock.Mock(json=lambda: {'requests': rows})):
            return gate.run({'dart': 'http://w'}, 'http://c', 'e2e-tests', 'production', 'regression')

    def rows(self):
        return [_record(i, {}, agent='absmartly-dart-sdk/2.2.0') for i in range(9)]

    def test_realistic_headers_pass(self):
        self.assertTrue(self.run_rows(self.rows())['overall_pass'])

    def test_get_missing_duplicate_wrong_headers_and_empty_log_fail(self):
        self.assertFalse(self.run_rows([])['overall_pass'])
        for header, bad in [('X-Agent', 'wrong/9'), ('X-Application', 'wrong'),
                            ('X-Environment', 'staging'), ('X-Application-Version', '2.2.0')]:
            for mode in ['missing', 'duplicate', 'wrong']:
                with self.subTest(header=header, mode=mode):
                    rows = self.rows()
                    for row in rows:
                        if mode == 'missing':
                            row['headers'] = [p for p in row['headers'] if p[0] != header]
                        elif mode == 'duplicate':
                            row['headers'].append([header, bad])
                        else:
                            row['headers'] = [[k, bad if k == header else v] for k, v in row['headers']]
                    self.assertFalse(self.run_rows(rows)['overall_pass'])

    def test_each_wire_timestamp_and_false_flag_is_checked(self):
        for field in ['publishedAt', 'exposedAt', 'achievedAt', 'overridden', 'fullOn',
                      'custom', 'audienceMismatch', 'ruleOverride', 'applicationVersion']:
            with self.subTest(field=field):
                records = _publishes_for('dart', gate.plan('regression', 'dart'))
                for row in records:
                    row['headers'][0] = ['X-Agent', 'absmartly-dart-sdk/2.2.0']
                    if field == 'applicationVersion':
                        row['headers'][-1] = ['X-Application-Version', '2.2.0']
                    elif field == 'publishedAt': row['json'][field] = 1
                    elif field == 'achievedAt':
                        for entry in row['json']['goals']: entry[field] = 1
                    else:
                        for entry in row['json']['exposures']: entry[field] = 1 if field == 'exposedAt' else True
                self.assertFalse(self.run_rows(self.rows(), records)['overall_pass'])

    def test_docker_gate_wiring_is_complete(self):
        compose = open(os.path.join(ROOT, 'docker-compose.yml')).read()
        overlay = open(os.path.join(ROOT, 'docker-compose.local-collector.yml')).read()
        dockerfile = open(os.path.join(ROOT, 'orchestrator/Dockerfile')).read()
        self.assertIn('  local-collector:', compose)
        self.assertIn('local_collector_gate.py', dockerfile)
        self.assertIn('e2e_cases.py', dockerfile)
        self.assertNotIn('java-v1-sdk:', overlay)
        self.assertNotIn('swift-v1-sdk:', overlay)

    def test_local_and_ci_share_immutable_checkout(self):
        for path in ['setup-sdks.sh', '.github/workflows/cross-sdk.yml']:
            self.assertIn('checkout-sdk.sh', open(os.path.join(ROOT, path)).read())
        helper = open(os.path.join(ROOT, 'checkout-sdk.sh')).read()
        self.assertIn('checkout --detach', helper)
        self.assertIn('rev-parse HEAD', helper)
        self.assertNotIn('clone --branch', helper)

class AgentPolicyRegressionTest(unittest.TestCase):
    def test_reviewed_unversioned_get_and_put_agents_pass(self):
        cases = gate.plan('policy', 'javascript')
        records = _publishes_for('javascript', cases)
        rows = [_record(i, {}) for i in range(9)]
        for record in records + rows:
            record['headers'][0] = ['X-Agent', 'absmartly-javascript-sdk']
        with mock.patch.object(gate, 'drive', return_value=[]), \
             mock.patch.object(gate, 'fetch_publishes', return_value=(records, None)), \
             mock.patch.object(gate.requests, 'delete'), \
             mock.patch.object(gate.requests, 'get', return_value=mock.Mock(json=lambda: {'requests': rows})):
            result = gate.run({'javascript': 'http://w'}, 'http://c', 'e2e-tests', 'production', 'policy')
        self.assertTrue(result['overall_pass'], result)


if __name__ == "__main__":
    unittest.main()
