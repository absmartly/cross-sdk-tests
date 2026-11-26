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

    def assertOnlyRejectedStored(self, count=None):
        """Rejected bodies are retained for the gate to fail on, but none is accepted as JSON."""
        status, stored = self.request("GET", "/publishes")
        self.assertEqual(status, 200)
        for record in stored["publishes"]:
            self.assertIsNone(record["json"])
            self.assertIn("parseError", record)
        if count is not None:
            self.assertEqual(len(stored["publishes"]), count)

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

    def test_malformed_bytes_are_retained_and_rejected(self):
        for headers, body in (({}, b"\xff"), ({"Content-Encoding": "gzip"}, b"not-gzip")):
            with self.subTest(headers=headers):
                status, _ = self.request("PUT", "/context", body, headers)
                self.assertEqual(status, 400)
        _, stored = self.request("GET", "/publishes")
        self.assertEqual(len(stored["publishes"]), 2)
        self.assertTrue(all(record["json"] is None and "parseError" in record for record in stored["publishes"]))
        self.assertEqual([__import__("base64").b64decode(r["rawBodyBytes"]) for r in stored["publishes"]],
                         [b"\xff", b"not-gzip"])

    def test_clear_and_unknown_routes(self):
        self.request("PUT", "/context", b"{}", {})
        self.assertEqual(self.request("DELETE", "/publishes")[0], 200)
        self.assertEqual(self.request("GET", "/publishes")[1], {"publishes": []})
        self.assertEqual(self.request("GET", "/v2/context")[0], 404)
        self.assertEqual(self.request("PUT", "/events", b"{}", {})[0], 404)

    def test_invalid_utf8_publish_returns_400_and_is_not_accepted(self):
        for payload in (b"\xff", json.dumps({"hashed": True}).encode("utf-16")):
            status, body = self.request("PUT", "/context", payload, {"Content-Length": str(len(payload))})
            self.assertEqual(status, 400)
            self.assertIn("error", body)
            self.assertOnlyRejectedStored()

    def test_content_length_must_be_plain_digits(self):
        for value in ("1_0", "+2", " 2", "2 ", "0x2", "2.0", "-0", "\u0662"):
            with self.subTest(value=value):
                self.assertIsNone(collector.parse_content_length([value]))
                self.assertEqual(collector.body_length_error(collector.parse_content_length([value])),
                                 "invalid Content-Length")
        self.assertEqual(collector.parse_content_length(["10"]), 10)
        self.assertIsNone(collector.parse_content_length([]))
        self.assertEqual(collector.body_length_error(collector.parse_content_length([])), "invalid Content-Length")
        self.assertIsNone(collector.parse_content_length([""]))
        self.assertIsNone(collector.parse_content_length(["", "2"]))
        # Duplicate lines: identical values are one length, conflicting values are ambiguous.
        self.assertEqual(collector.parse_content_length(["2", "2"]), 2)
        self.assertIsNone(collector.parse_content_length(["2", "100"]))
        self.assertIsNone(collector.parse_content_length(["100", "2"]))
        status, _ = self.request("PUT", "/context", b"{}", {"Content-Length": "1_0"})
        self.assertEqual(status, 400)
        self.assertOnlyRejectedStored()

    def test_truncated_body_returns_400_and_is_not_accepted(self):
        import socket
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{}")
            sock.shutdown(socket.SHUT_WR)
            reply = b""
            while chunk := sock.recv(4096):
                reply += chunk
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertOnlyRejectedStored()

    def test_deeply_nested_json_returns_400(self):
        payload = b"[" * 100000 + b"]" * 100000
        status, _ = self.request("PUT", "/context", payload, {"Content-Length": str(len(payload))})
        self.assertEqual(status, 400)
        self.assertOnlyRejectedStored()

    def raw_put(self, head, body=b"", half_close=True):
        """Send a hand-built request; returns the raw reply bytes."""
        import socket
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
            sock.sendall(head + b"\r\n\r\n" + body)
            if half_close:
                sock.shutdown(socket.SHUT_WR)
            reply = b""
            while chunk := sock.recv(4096):
                reply += chunk
        return reply

    def test_missing_content_length_returns_400_and_is_not_accepted(self):
        reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x", b"{}")
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertOnlyRejectedStored()

    def test_conflicting_duplicate_content_length_returns_400(self):
        for first, second in ((b"2", b"100"), (b"100", b"2")):
            with self.subTest(order=(first, second)):
                reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: " + first
                                     + b"\r\nContent-Length: " + second, b"{}")
                self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertOnlyRejectedStored()

    def test_stalled_body_times_out_instead_of_hanging(self):
        # Declared 100 bytes, sent 2, socket left open: the handler must give up.
        with mock.patch.object(collector, "REQUEST_TIMEOUT_S", 1):
            server = collector.make_server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            import socket
            with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=15) as sock:
                sock.sendall(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{}")
                reply = b""
                while chunk := sock.recv(4096):
                    reply += chunk
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(reply == b"" or reply.startswith(b"HTTP/1.1 400"), reply[:80])

    def test_gzip_bomb_is_rejected_without_full_inflation(self):
        bomb = gzip.compress(b"\0" * (collector.MAX_BODY_BYTES + 1))
        with mock.patch.object(collector.zlib, "decompressobj", wraps=collector.zlib.decompressobj) as inflater:
            status, _ = self.request("PUT", "/context", bomb, {"Content-Encoding": "gzip"}, timeout=30)
        self.assertEqual(status, 400)
        self.assertTrue(inflater.called)
        self.assertOnlyRejectedStored()
        with self.assertRaisesRegex(ValueError, "size limit"):
            collector.gunzip_capped(bomb)
        self.assertEqual(collector.gunzip_capped(gzip.compress(b"{}")), b"{}")

    def test_gzip_inflation_stops_at_the_cap(self):
        # The inflater is asked for at most MAX_BODY_BYTES + 1 bytes, never the whole bomb.
        calls = []
        real = collector.zlib.decompressobj

        class Spy:
            def __init__(self, *a):
                self.inner = real(*a)

            def decompress(self, data, max_length=0):
                calls.append(max_length)
                return self.inner.decompress(data, max_length)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with mock.patch.object(collector.zlib, "decompressobj", Spy):
            with self.assertRaises(ValueError):
                collector.gunzip_capped(gzip.compress(b"\0" * (collector.MAX_BODY_BYTES + 1)))
        self.assertEqual(calls, [collector.MAX_BODY_BYTES + 1])

    def test_truncated_gzip_stream_is_rejected(self):
        whole = gzip.compress(b'{"hashed":true}')
        with self.assertRaisesRegex(ValueError, "truncated gzip"):
            collector.gunzip_capped(whole[:-8])
        status, _ = self.request("PUT", "/context", whole[:-8], {"Content-Encoding": "gzip"})
        self.assertEqual(status, 400)
        self.assertOnlyRejectedStored()

    def test_json_nested_too_deep_is_rejected_and_store_stays_readable(self):
        # 985 levels parses on 3.11 but then breaks json.dumps of /publishes.
        for depth in (collector.MAX_JSON_DEPTH + 1, 985):
            payload = b'{"hashed":true,"x":' + b"[" * depth + b"]" * depth + b"}"
            with self.subTest(depth=depth):
                status, _ = self.request("PUT", "/context", payload)
                self.assertEqual(status, 400)
        self.request("PUT", "/context", b'{"hashed":true,"units":[]}')
        status, stored = self.request("GET", "/publishes")
        self.assertEqual(status, 200)
        self.assertEqual([r["json"] for r in stored["publishes"]], [None, None, {"hashed": True, "units": []}])
        self.assertIsNone(collector.json_depth_error({"a": [[1]]}))
        self.assertIsNone(collector.json_depth_error(["x"] * 5, limit=1))
        self.assertIsNotNone(collector.json_depth_error([[1]], limit=1))

    def test_gzip_members_and_trailing_bytes(self):
        one, two = gzip.compress(b'{"hashed":'), gzip.compress(b'true}')
        # Concatenated members are one body, as gzip.decompress reads them.
        self.assertEqual(collector.gunzip_capped(one + two), b'{"hashed":true}')
        status, _ = self.request("PUT", "/context", one + two, {"Content-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.request("DELETE", "/publishes")
        # Bytes after the last member that are not a member, or a second member that
        # makes the JSON invalid, are rejected and not accepted.
        for body in (gzip.compress(b'{"hashed":true}') + b"JUNK",
                     gzip.compress(b'{"hashed":true}') + gzip.compress(b'{"evil":1}')):
            with self.subTest(body=body[-12:]):
                status, _ = self.request("PUT", "/context", body, {"Content-Encoding": "gzip"})
                self.assertEqual(status, 400)
                self.assertOnlyRejectedStored()

    def test_bad_framing_closes_the_connection_so_nothing_is_smuggled(self):
        smuggled = b'{"hashed":true,"units":[]}'
        pipelined = (b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: "
                     + str(len(smuggled)).encode() + b"\r\n\r\n" + smuggled)
        reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n"
                             b"Content-Length: 100", b"{}" + pipelined)
        self.assertEqual(reply.count(b"HTTP/1.1 "), 1, reply)
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"))
        self.assertOnlyRejectedStored(count=1)

    def test_body_on_a_route_that_ignores_it_closes_the_connection(self):
        smuggled = b'{"hashed":true,"units":[]}'
        pipelined = (b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: "
                     + str(len(smuggled)).encode() + b"\r\n\r\n" + smuggled)
        for method, path in ((b"PUT", b"/events"), (b"GET", b"/health"), (b"DELETE", b"/nope")):
            with self.subTest(method=method, path=path):
                reply = self.raw_put(method + b" " + path + b" HTTP/1.1\r\nHost: x\r\nContent-Length: "
                                     + str(len(pipelined)).encode(), pipelined)
                self.assertEqual(reply.count(b"HTTP/1.1 "), 1, reply)
                self.assertIn(b"Connection: close", reply)
        self.assertEqual(self.request("GET", "/publishes")[1], {"publishes": []})
        # A bodiless request, and a publish whose body was read, keep the connection reusable.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        for method, path, body in (("GET", "/health", None), ("PUT", "/context", smuggled),
                                   ("PUT", "/context", smuggled)):
            conn.request(method, path, body=body)
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
            self.assertIsNone(response.getheader("Connection"))
        conn.close()
        self.assertEqual(len(self.request("GET", "/publishes")[1]["publishes"]), 2)

    def test_numbers_overflowing_to_infinity_are_rejected(self):
        for body in (b'{"amount":1e400}', b'{"amount":-1e400}', b'{"a":[1.5e999]}'):
            with self.subTest(body=body):
                status, _ = self.request("PUT", "/context", body)
                self.assertEqual(status, 400)
        status, stored = self.request("GET", "/publishes")
        self.assertEqual(status, 200)
        self.assertOnlyRejectedStored(count=3)
        self.request("DELETE", "/publishes")
        self.assertEqual(self.request("PUT", "/context", b'{"amount":1.7976931348623157e308}')[0], 200)

    def test_transfer_encoding_is_rejected(self):
        reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
                             b"Content-Length: 2", b"{}")
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertOnlyRejectedStored()

    def test_non_standard_json_constants_are_rejected(self):
        for body in (b'{"amount":NaN}', b'{"amount":Infinity}', b'{"amount":-Infinity}'):
            with self.subTest(body=body):
                status, _ = self.request("PUT", "/context", body)
                self.assertEqual(status, 400)
        self.assertOnlyRejectedStored()

    def test_trickling_body_hits_the_whole_request_deadline(self):
        import socket, time as clock
        with mock.patch.object(collector, "REQUEST_DEADLINE_S", 1):
            with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
                sock.sendall(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 50\r\n\r\n")
                started = clock.monotonic()
                reply = b""
                for _ in range(10):
                    try:
                        sock.sendall(b" ")
                    except OSError:
                        break
                    clock.sleep(0.4)
                    sock.settimeout(0.01)
                    try:
                        reply += sock.recv(4096)
                    except (socket.timeout, OSError):
                        pass
                    if reply:
                        break
                elapsed = clock.monotonic() - started
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertLess(elapsed, 3)

    def test_client_reset_does_not_raise_out_of_the_handler(self):
        handler = collector.make_handler(collector.Store())
        fake = handler.__new__(handler)
        fake.request_version = "HTTP/1.1"
        fake.close_connection = False
        fake.send_response = mock.Mock(side_effect=BrokenPipeError())
        fake._send(400, {"error": "x"})
        self.assertTrue(fake.close_connection)
        fake.send_response = mock.Mock(side_effect=ConnectionResetError())
        fake.close_connection = False
        fake._send(400, {"error": "x"})
        self.assertTrue(fake.close_connection)

    def test_content_length_limit_is_exactly_16_mib(self):
        self.assertEqual(collector.MAX_BODY_BYTES, 16 * 1024 * 1024)
        self.assertIsNone(collector.body_length_error(16 * 1024 * 1024))
        self.assertEqual(collector.body_length_error(16 * 1024 * 1024 + 1), "invalid Content-Length")

    def test_malformed_content_length_and_gzip_return_400(self):
        status, _ = self.request("PUT", "/context", b"{}", {"Content-Length": "bad"})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"", {"Content-Length": "-1"})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"", {"Content-Length": str(16 * 1024 * 1024 + 1)})
        self.assertEqual(status, 400)
        status, _ = self.request("PUT", "/context", b"not-gzip", {"Content-Encoding": "gzip"})
        self.assertEqual(status, 400)
        corrupted = bytearray(gzip.compress(b"hello world" * 1000))
        corrupted[10] ^= 0xff
        status, _ = self.request("PUT", "/context", bytes(corrupted), {"Content-Encoding": "gzip"})
        self.assertEqual(status, 400)
        self.assertOnlyRejectedStored()

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
            "bad json": lambda out: out[0].update(json=None),
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

    def test_non_object_publish_fails_closed(self):
        # A record whose body is valid JSON but not an object is reported, not skipped.
        result = gate.verify("javascript", self.cases, [{"seq": 0, "json": [], "headers": []}],
                             "e2e-tests", "production", False)
        self.assertFalse(result["pass"])
        self.assertIn("publish #0 is not a JSON object", result["errors"])
        rejected = gate.verify("javascript", self.cases,
                               [{"seq": 3, "json": None, "parseError": "truncated request body", "headers": []}],
                               "e2e-tests", "production", False)
        self.assertIn("publish #3 is not a JSON object: truncated request body", rejected["errors"])

    def test_gate_requires_application_and_environment_headers(self):
        # Missing (not just wrong) headers on a planned publish fail the gate.
        for name in ("X-Application", "X-Environment", "X-Agent"):
            def drop(out, name=name):
                out[0]["headers"] = [h for h in out[0]["headers"] if h[0] != name]
            with self.subTest(name):
                result = self.check(drop)
                self.assertFalse(result["pass"])
                expected = "agent" if name == "X-Agent" else name[2:].lower() + " header"
                self.assertTrue(any(expected in e for e in result["errors"]), result["errors"])

    def test_duplicated_transport_headers_fail_closed(self):
        # Two values for one header are ambiguous; the gate must not pick the first.
        for name, value in (("X-Application", "e2e-tests"), ("X-Environment", "production"),
                            ("X-Agent", "absmartly-javascript-sdk/1.14.0")):
            with self.subTest(name):
                result = self.check(lambda out, n=name, v=value: out[0]["headers"].append([n, v]))
                self.assertFalse(result["pass"], name)

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

    def run_with(self, extra):
        cases = gate.plan("r", "javascript")
        publishes = _publishes_for("javascript", cases) + extra
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=publishes), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": []})):
            return gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r")

    def test_every_publish_must_belong_to_the_plan(self):
        self.assertTrue(self.run_with([])["overall_pass"])
        stray = _record(99, {"hashed": True, "units": [{"type": "user_id", "uid": gate.unit_hash("stray")}],
                             "exposures": [], "goals": []})
        for label, extra in (("unplanned unit", [stray]), ("no units", [_record(99, {"hashed": True})]),
                             ("malformed json", [{"seq": 99, "json": None, "headers": []}])):
            with self.subTest(label):
                self.assertFalse(self.run_with(extra)["overall_pass"], label)


class FetchPublishesTest(unittest.TestCase):
    PLANNED = {("user_id", "u")}
    MINE = {"json": {"units": [{"type": "user_id", "uid": "u"}]}}
    STRAY = {"json": {"units": [{"type": "user_id", "uid": "stray"}]}}

    def fetch(self, reads, expected_cases, clock):
        reads = iter(reads)
        get = mock.Mock(side_effect=lambda *a, **k: mock.Mock(json=lambda: {"publishes": next(reads)}))
        with mock.patch.object(gate.requests, "get", get), mock.patch.object(gate.time, "sleep"), \
             mock.patch.object(gate.time, "monotonic", side_effect=clock):
            return len(gate.fetch_publishes("http://c", expected_cases, timeout=30)), get.call_count

    def test_waits_past_stable_reads_until_the_expected_count_arrives(self):
        # Two stable zero reads used to end the wait; the late publishes must still be read.
        m = self.MINE
        self.assertEqual(self.fetch([[], [], [], [m, m], [m, m, m]], [{("user_id", "u")}], iter([0] + [1] * 10)), (3, 5))

    def test_stray_publishes_do_not_count_toward_expected(self):
        m, s = self.MINE, self.STRAY
        m2 = {"json": {"units": [{"type": "user_id", "uid": "missing"}]}}
        self.assertEqual(self.fetch([[m, s], [m, s, m], [m, s, m, m2], [m, s, m, m2]],
                                    [{("user_id", "u")}, {("user_id", "missing")}], iter([0] + [1] * 10)), (4, 4))

    def test_timeout_returns_what_arrived(self):
        m = self.MINE
        self.assertEqual(self.fetch([[], [m], [m]], [{("user_id", "u")}, {("user_id", "missing")}], iter([0, 10, 20, 31])), (1, 3))

    def test_one_publish_combining_two_cases_does_not_complete_both(self):
        both = {"json": {"units": [{"type": "user_id", "uid": "u"}, {"type": "user_id", "uid": "missing"}]}}
        self.assertEqual(self.fetch([[both], [both], [both]], [{("user_id", "u")}, {("user_id", "missing")}],
                                    iter([0, 10, 20, 31])), (1, 3))

    def test_a_multi_unit_case_completes_from_its_own_publish(self):
        multi = {"json": {"units": [{"type": "user_id", "uid": "mu"}, {"type": "session_id", "uid": "mu-s"}]}}
        self.assertEqual(self.fetch([[multi], [multi]], [{("user_id", "mu"), ("session_id", "mu-s")}],
                                    iter([0] + [1] * 10)), (1, 2))


if __name__ == "__main__":
    unittest.main()
