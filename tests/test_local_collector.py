"""Local collector service and the blocking gate's verifier, without Docker."""
import copy
import gzip
import http.client
import itertools
import json
from decimal import Decimal
import os
import sys
import threading
import unittest
from collections import Counter
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
        first, rejected = stored["publishes"]
        self.assertEqual(first["json"], {"hashed": True})
        self.assertEqual(first["contentEncoding"], "gzip")
        self.assertEqual(first["seq"], 0)
        # The malformed body is retained for the gate to fail on, never accepted as JSON.
        self.assertEqual(rejected["seq"], 1)
        self.assertIsNone(rejected["json"])
        self.assertIn("parseError", rejected)
        self.assertEqual(rejected["rawBody"], "{not json")

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
        import codecs
        text = json.dumps({"hashed": True})
        # A UTF-8 BOM, and UTF-16/32 without a BOM, are valid UTF-8 bytes that json.loads(bytes)
        # would still auto-detect and accept; only plain UTF-8 JSON text is a publish.
        for payload in (b"\xff", text.encode("utf-16"), codecs.BOM_UTF8 + text.encode(),
                        text.encode("utf-16-le"), text.encode("utf-16-be"), text.encode("utf-32-le")):
            with self.subTest(payload=payload[:6]):
                status, reply = self.request("PUT", "/context", payload, {"Content-Length": str(len(payload))})
                self.assertEqual(status, 400)
                self.assertIn("error", reply)
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
        reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 100", b"{}")
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
        # Declared 100 bytes, sent 2, socket left open: the per-read timeout ends the wait long
        # before the whole-body deadline.
        import socket, time as clock
        self.assertGreater(collector.REQUEST_DEADLINE_S, 5)
        with mock.patch.object(collector, "REQUEST_TIMEOUT_S", 1):
            server = collector.make_server("127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=15) as sock:
                    sock.sendall(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{}")
                    started = clock.monotonic()
                    reply = sock.recv(4096)
                    elapsed = clock.monotonic() - started
            finally:
                server.shutdown()
                server.server_close()
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertLess(elapsed, 3)

    def test_gzip_bomb_is_rejected_without_full_inflation(self):
        # Twice the cap, so inflating it whole would yield more than MAX_BODY_BYTES + 1 bytes.
        bomb = gzip.compress(b"\0" * (2 * collector.MAX_BODY_BYTES))
        inflated = []
        real = collector.zlib.decompressobj

        class Spy:
            def __init__(self, *a):
                self.inner = real(*a)

            def decompress(self, *args):
                chunk = self.inner.decompress(*args)
                inflated.append(len(chunk))
                return chunk

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with mock.patch.object(collector.zlib, "decompressobj", Spy):
            status, _ = self.request("PUT", "/context", bomb, {"Content-Encoding": "gzip"}, timeout=30)
        self.assertEqual(status, 400)
        self.assertTrue(inflated)
        self.assertLessEqual(sum(inflated), collector.MAX_BODY_BYTES + 1)
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
        # Zero padding after a member is skipped, as gzip.decompress skips it.
        for padding in (b"\0", b"\0" * 512):
            with self.subTest(padding=len(padding)):
                self.assertEqual(collector.gunzip_capped(one + padding), b'{"hashed":')
                body = gzip.compress(b'{"hashed":true}') + padding
                self.assertEqual(self.request("PUT", "/context", body, {"Content-Encoding": "gzip"})[0], 200)
                self.request("DELETE", "/publishes")
        # Other bytes after the last member (also after padding), or a second member that
        # makes the JSON invalid, are rejected and not accepted.
        for body in (gzip.compress(b'{"hashed":true}') + b"JUNK",
                     gzip.compress(b'{"hashed":true}') + b"\0\0JUNK",
                     gzip.compress(b'{"hashed":true}') + b"\0J",
                     gzip.compress(b'{"hashed":true}') + b"\0\x1f",
                     gzip.compress(b'{"hashed":true}') + b"\0\n",
                     gzip.compress(b'{"hashed":true}') + b" ",
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
        # Missing Content-Length: only the framing-error close stops the pipelined request.
        reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x", pipelined)
        self.assertEqual(reply.count(b"HTTP/1.1 "), 1, reply)
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"))
        self.assertOnlyRejectedStored(count=2)

    def test_body_on_a_route_that_ignores_it_closes_the_connection(self):
        smuggled = b'{"hashed":true,"units":[]}'
        pipelined = (b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: "
                     + str(len(smuggled)).encode() + b"\r\n\r\n" + smuggled)
        length = str(len(pipelined)).encode()
        for method, path, framing in ((b"PUT", b"/events", b"Content-Length: " + length),
                                      (b"GET", b"/health", b"Content-Length: " + length),
                                      (b"DELETE", b"/nope", b"Content-Length: " + length),
                                      # every Content-Length line counts, not just the first
                                      (b"GET", b"/health", b"Content-Length: 0\r\nContent-Length: " + length),
                                      (b"GET", b"/health", b"Transfer-Encoding: chunked"),
                                      (b"GET", b"/health", b"Content-Length: 00\r\nContent-Length: " + length),
                                      (b"GET", b"/health", b"Content-Length: 1_0")):
            with self.subTest(method=method, path=path, framing=framing):
                reply = self.raw_put(method + b" " + path + b" HTTP/1.1\r\nHost: x\r\n" + framing, pipelined)
                self.assertEqual(reply.count(b"HTTP/1.1 "), 1, reply)
                self.assertIn(b"Connection: close", reply)
        self.assertEqual(self.request("GET", "/publishes")[1], {"publishes": []})
        # Every valid zero length is no body, so the connection stays reusable.
        for zero in (b"0", b"00", b"000", b"0\r\nContent-Length: 0"):
            with self.subTest(zero=zero):
                reply = self.raw_put(b"GET /health HTTP/1.1\r\nHost: x\r\nContent-Length: " + zero + b"\r\n\r\n"
                                     b"GET /health HTTP/1.1\r\nHost: x")
                self.assertEqual(reply.count(b"HTTP/1.1 200"), 2, reply)
                self.assertNotIn(b"Connection: close", reply)
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
        # The flag resets per request: a GET with a body after a publish whose body was read closes.
        reply = self.raw_put(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: "
                             + str(len(smuggled)).encode() + b"\r\n\r\n" + smuggled
                             + b"GET /health HTTP/1.1\r\nHost: x\r\nContent-Length: " + length, pipelined)
        self.assertEqual(reply.count(b"HTTP/1.1 200"), 2, reply)
        self.assertEqual(reply.count(b"Connection: close"), 1, reply)
        self.assertEqual(len(self.request("GET", "/publishes")[1]["publishes"]), 3)

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

    def test_a_body_completed_after_the_deadline_is_rejected(self):
        # The last bytes arrive after the whole-body deadline: no read may wait past it, so the
        # publish is rejected rather than accepted late.
        import socket, time as clock
        with mock.patch.object(collector, "REQUEST_DEADLINE_S", 1):
            with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
                sock.sendall(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n")
                clock.sleep(1.5)
                try:
                    sock.sendall(b"{}")
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                reply = b""
                try:
                    while chunk := sock.recv(4096):
                        reply += chunk
                except OSError:
                    pass
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertOnlyRejectedStored(count=1)

    def test_a_stalled_body_is_answered_at_the_deadline_not_the_socket_timeout(self):
        # One read must not wait the full per-read timeout once the body deadline is nearer.
        import socket, time as clock
        self.assertGreater(collector.REQUEST_TIMEOUT_S, 5)
        with mock.patch.object(collector, "REQUEST_DEADLINE_S", 1):
            with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
                sock.sendall(b"PUT /context HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n")
                started = clock.monotonic()
                reply = sock.recv(4096)
                elapsed = clock.monotonic() - started
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:80])
        self.assertLess(elapsed, 3)

    def test_a_read_that_completes_past_the_deadline_is_rejected(self):
        # The clock is checked again after the final read, not only before each one.
        import io
        handler = collector.make_handler(collector.Store())
        fake = handler.__new__(handler)
        fake.rfile = io.BytesIO(b"{}")
        fake.connection = mock.Mock()
        with mock.patch.object(collector, "_monotonic", side_effect=[0, 0, collector.REQUEST_DEADLINE_S + 1]):
            with self.assertRaisesRegex(ValueError, "deadline"):
                fake._read_exact(2)
        fake.connection.settimeout.assert_called_with(collector.REQUEST_TIMEOUT_S)

    def test_no_read_starts_once_the_deadline_has_passed(self):
        # The budget is checked before every read, so an expired deadline never waits on the socket.
        handler = collector.make_handler(collector.Store())
        fake = handler.__new__(handler)
        import io
        fake.rfile = mock.Mock(wraps=io.BytesIO(b"{}"))
        fake.connection = mock.Mock()
        deadline = collector.REQUEST_DEADLINE_S
        with mock.patch.object(collector, "_monotonic", side_effect=[0, deadline, deadline]):
            with self.assertRaisesRegex(ValueError, "deadline"):
                fake._read_exact(2)
        fake.rfile.read1.assert_not_called()

    def test_the_socket_timeout_is_restored_after_a_bounded_read(self):
        # A read near the deadline shortens the socket timeout; the next request must not inherit it.
        import io
        deadline = collector.REQUEST_DEADLINE_S
        handler = collector.make_handler(collector.Store())
        fake = handler.__new__(handler)
        fake.rfile = io.BytesIO(b"{}")
        fake.connection = mock.Mock()
        # A per-read timeout other than the default proves the restore reads the setting.
        with mock.patch.object(collector, "REQUEST_TIMEOUT_S", 7), \
             mock.patch.object(collector, "_monotonic", side_effect=[0, deadline - 1, deadline - 0.5]):
            self.assertEqual(fake._read_exact(2), b"{}")
        self.assertEqual(fake.connection.settimeout.call_args_list, [mock.call(1), mock.call(7)])

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

    def test_real_planned_unit_types_and_uids_are_not_absent_type_aliases(self):
        for bad in (None, "", "wrong"):
            with self.subTest(exposure_unit=bad):
                result = self.check(lambda out: out[0]["json"]["exposures"][0].update(unit=bad))
                self.assertFalse(result["pass"])
        for bad in (None, ""):
            with self.subTest(uid=bad):
                result = self.check(lambda out: out[0]["json"]["units"][0].update(uid=bad))
                self.assertFalse(result["pass"])

    def test_intact_publishes_pass_with_received_revenue(self):
        result = self.check()
        self.assertTrue(result["pass"], result["errors"])
        self.assertEqual(result["revenue"], "1000021.6")
        self.assertEqual(result["planned_revenue"], "1000021.6")
        self.assertEqual(result["exposures"], len(self.cases) + 1)

    def test_malformed_and_duplicate_units_fail_direct_and_cached_verification(self):
        for duplicate, malformed in ((True, False), (False, True), (True, True)):
            for cached in (False, True):
                with self.subTest(duplicate=duplicate, malformed=malformed, cached=cached):
                    publishes = _publishes_for("javascript", self.cases)
                    record = publishes[0]
                    if duplicate:
                        record["json"]["units"].append(copy.deepcopy(record["json"]["units"][0]))
                    if malformed:
                        record["json"]["units"].append({"bad": 1})
                    if cached:
                        record["_units"], record["_unit_errors"] = gate._publish_units(record)
                    result = gate.verify("javascript", self.cases, publishes, "e2e-tests", "production")
                    self.assertFalse(result["pass"])
                    for enabled, diagnostic in ((duplicate, "duplicate unit"), (malformed, "malformed units entry")):
                        if enabled:
                            self.assertTrue(any(diagnostic in e for e in result["errors"]), result["errors"])

    def test_another_sdks_duplicate_units_do_not_fail_this_sdk(self):
        other = _publishes_for("go", gate.plan("abcd", "go"))
        other[0]["json"]["units"].append(copy.deepcopy(other[0]["json"]["units"][0]))
        other[0]["json"]["units"].append({"bad": 1})
        result = gate.verify("javascript", self.cases, _publishes_for("javascript", self.cases) + other,
                             "e2e-tests", "production", False)
        self.assertTrue(result["pass"], result["errors"])

    def test_excepted_case_duplicate_units_still_fail_transport(self):
        cases = self.cases[:-1]
        excepted = self.cases[-1:]
        publishes = _publishes_for("javascript", self.cases)
        publishes[-1]["json"]["units"].append(copy.deepcopy(publishes[-1]["json"]["units"][0]))
        result = gate.verify("javascript", cases, publishes, "e2e-tests", "production", excepted=excepted)
        self.assertFalse(result["pass"])
        self.assertTrue(any("duplicate unit" in e for e in result["errors"]))

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
                 mock.patch.object(gate, "fetch_publishes", return_value=([], None)):
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
             mock.patch.object(gate, "fetch_publishes", return_value=(publishes, None)), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": []})):
            result = gate.run({"python": "http://w"}, "http://c", "e2e-tests", "production", "r")
        self.assertTrue(result["overall_pass"], result["sdks"]["python"]["errors"])

    def test_a_rejected_publish_still_fails_under_an_exception_policy(self):
        # python excepts null_value; a body the collector rejected must not ride on that exception.
        cases = gate.plan("r", "python")
        publishes = _publishes_for("python", [c for c in cases if c["name"] != "null_value"])
        for record in publishes:
            record["headers"][0] = ["X-Agent", "absmartly-python-sdk/0.4.0"]
        publishes.append({"seq": 99, "json": None, "parseError": "number out of range: 1e400",
                          "rawBody": '{"amount": 1e400}', "headers": []})
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=(publishes, None)), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": []})):
            result = gate.run({"python": "http://w"}, "http://c", "e2e-tests", "production", "r")
        self.assertFalse(result["overall_pass"])
        self.assertIn("publish #99 is not a JSON object: number out of range: 1e400",
                      result["sdks"]["python"]["errors"])

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
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": []})):
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

    def test_the_python_null_property_exception_still_checks_that_publishs_transport(self):
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
             mock.patch.object(gate.requests, "get", return_value=mock.Mock(json=lambda: {"requests": []})):
            return gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r")

    def test_every_publish_must_belong_to_the_plan(self):
        self.assertTrue(self.run_with([])["overall_pass"])
        self.assertNotIn("transport_errors", self.run_with([]))
        stray = _record(99, {"hashed": True, "units": [{"type": "user_id", "uid": gate.unit_hash("stray")}],
                             "exposures": [], "goals": []})
        for label, extra in (("unplanned unit", [stray]), ("no units", [_record(99, {"hashed": True})]),
                             ("malformed json", [{"seq": 99, "json": None, "headers": []}])):
            with self.subTest(label):
                self.assertFalse(self.run_with(extra)["overall_pass"], label)
        # A rejected record is reported once, as not a JSON object, not also as a unit-less publish.
        result = self.run_with([{"seq": 99, "json": None, "parseError": "x", "headers": []}])
        self.assertFalse(result["overall_pass"])
        self.assertIn("publish #99 is not a JSON object: x", result["sdks"]["javascript"]["errors"])
        self.assertFalse(any("carries no planned unit" in e for e in result.get("transport_errors", [])),
                         result.get("transport_errors"))

    def test_a_duplicate_unit_fails_the_sdk_that_sent_it(self):
        def dup(out):
            out[0]["json"]["units"].append(copy.deepcopy(out[0]["json"]["units"][0]))
        result = self.run_with([], dup)
        self.assertFalse(result["sdks"]["javascript"]["pass"])
        self.assertTrue(any("duplicate unit" in e for e in result["sdks"]["javascript"]["errors"]))

    def test_a_conflicting_uid_for_one_unit_type_fails_the_sdk_that_sent_it(self):
        def conflict(out):
            out[0]["json"]["units"].append({"type": "user_id", "uid": gate.unit_hash("another")})
        result = self.run_with([], conflict)
        self.assertFalse(result["sdks"]["javascript"]["pass"])
        self.assertTrue(any("conflicting uids for unit type 'user_id'" in e
                            for e in result["sdks"]["javascript"]["errors"]), result["sdks"]["javascript"]["errors"])

    def test_blank_unit_fields_report_their_own_diagnostic(self):
        for bad in ({"type": "", "uid": "x"}, {"type": "user_id", "uid": ""}):
            with self.subTest(bad=bad):
                _, errors = gate._publish_units(_record(0, {"hashed": True, "units": [bad]}))
                self.assertTrue(any("blank type or uid" in e for e in errors), errors)
        result = self.run_with([], lambda out: out[0]["json"]["units"].append({"type": "", "uid": "x"}))
        self.assertFalse(result["overall_pass"])

    def test_run_parses_each_publishs_units_once(self):
        real = gate._entries
        parsed = Counter()

        def counting(record, field, keys, errors):
            if field == "units":
                parsed[record["seq"]] += 1
            return real(record, field, keys, errors)

        with mock.patch.object(gate, "_entries", side_effect=counting):
            result = self.run_with([])
        self.assertTrue(result["overall_pass"], result)
        self.assertEqual(set(parsed.values()), {1}, parsed)

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

    def test_an_http_error_from_the_request_log_fails_even_with_a_valid_looking_body(self):
        cases = gate.plan("r", "javascript")
        error = gate.requests.HTTPError("503 Server Error: Service Unavailable")
        response = mock.Mock(json=lambda: {"requests": []}, raise_for_status=mock.Mock(side_effect=error))
        with mock.patch.object(gate, "drive", return_value=[]), \
             mock.patch.object(gate, "fetch_publishes", return_value=(_publishes_for("javascript", cases), None)), \
             mock.patch.object(gate.requests, "delete"), \
             mock.patch.object(gate.requests, "get", return_value=response):
            result = gate.run({"javascript": "http://w"}, "http://c", "e2e-tests", "production", "r")
        self.assertFalse(result["overall_pass"])
        self.assertTrue(any(e.startswith("collector request log unavailable") for e in result["transport_errors"]),
                        result.get("transport_errors"))

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
        # Each overflows a float, so the collector rejects the publish before the gate compares it;
        # the gate's own exact comparison and unparseable-body paths are pinned in GateNumberTest.
        for token in ("1e999999999", "1e1000000", "-1e99999999", "1e9999999999999999999"):
            diagnostic = f"is not a JSON object: number out of range: {token}"
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


class GateNumberTest(unittest.TestCase):
    """Gate-side number handling, reachable only if a collector stores such a body."""

    def test_huge_wire_numbers_compare_exactly_against_the_plan(self):
        def canonical(token):
            return gate._canonical({"amount": Decimal(token)})
        for token in ("1e999999999", "-1e99999999"):
            self.assertNotEqual(canonical(token), gate._canonical({"amount": 10}))
        # Distinct huge values stay distinct: no exponent clamp or rounding collapses them.
        tokens = ("1e999999999", "1e999999998", "-1e999999999", "1e-999999999", "2e999999999")
        self.assertEqual(len({canonical(t) for t in tokens}), len(tokens))
        self.assertEqual(canonical("10e999999998"), canonical("1e999999999"))
        self.assertEqual(gate._canonical({"amount": Decimal("1e1")}), gate._canonical({"amount": 10}))

    def test_an_unrepresentable_raw_body_is_reported_unparseable(self):
        record = {"seq": 0, "json": {"amount": 1.0}, "rawBody": '{"amount": 1e9999999999999999999}'}
        out = gate._lossless(record)
        self.assertIsNone(out["json"])
        self.assertIn("raw body does not parse", out["parseError"])

    def test_a_rejected_record_is_never_reparsed_into_an_accepted_publish(self):
        cases = gate.plan("r", "javascript")
        good = _publishes_for("javascript", cases)
        rejected = [{**r, "json": None, "parseError": "JSON nested deeper than 64 levels",
                     "rawBody": json.dumps(r["json"])} for r in good]
        normalized = [gate._lossless(r) for r in rejected]
        self.assertTrue(all(r["json"] is None for r in normalized))
        result = gate.verify("javascript", cases, normalized, "e2e-tests", "production", False)
        self.assertFalse(result["pass"])
        self.assertTrue(any("is not a JSON object: JSON nested deeper" in e for e in result["errors"]),
                        result["errors"])


class PublishLogFailureTest(unittest.TestCase):
    def run_with_reads(self, reads):
        reads = iter(reads)
        cases = gate.plan("r", "javascript")
        good = _publishes_for("javascript", cases)

        def get(url, **_):
            if url.endswith("/requests"):
                return mock.Mock(json=lambda: {"requests": []})
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

if __name__ == "__main__":
    unittest.main()
