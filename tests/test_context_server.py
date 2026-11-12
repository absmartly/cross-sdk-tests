"""Docker-free tests for the negotiating context server (context-server/server.py)."""

import gzip
import http.client
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "context-server"))

import server as context_server  # noqa: E402

DATA = {"experiments": [{"id": 1, "name": "exp"}]}


class NegotiationRules(unittest.TestCase):
    def test_parse_accept_encoding(self):
        self.assertEqual(context_server.parse_accept_encoding(None), {})
        self.assertEqual(
            context_server.parse_accept_encoding("gzip, deflate;q=0.5, br;q=0"),
            {"gzip": 1.0, "deflate": 0.5, "br": 0.0},
        )

    def test_advertises_requires_explicit_positive_q(self):
        self.assertTrue(context_server.advertises("gzip, deflate", "gzip"))
        self.assertTrue(context_server.advertises("x-gzip", "gzip"))
        self.assertFalse(context_server.advertises("*", "gzip"))
        self.assertFalse(context_server.advertises("gzip;q=0", "gzip"))
        self.assertFalse(context_server.advertises(None, "gzip"))

    def test_first_preferred_acceptable_coding_wins(self):
        choose = context_server.choose_encoding
        self.assertEqual(choose("gzip, deflate", ["gzip", "identity"]), "gzip")
        self.assertEqual(choose(None, ["gzip", "identity"]), "identity")
        self.assertEqual(choose("deflate", ["gzip", "identity"]), "identity")
        self.assertEqual(choose("gzip, br, zstd", ["zstd", "br", "gzip", "identity"]),
                         next(c for c in ("zstd", "br", "gzip") if c in context_server.COMPRESSORS))
        self.assertEqual(choose("gzip", ["identity"]), "identity")

    def test_force_ignores_the_request(self):
        self.assertEqual(context_server.choose_encoding(None, ["identity"], force="gzip"), "gzip")

    def test_payload_validation(self):
        self.assertIsNone(context_server.validate_payload({"data": DATA}))
        self.assertIsNotNone(context_server.validate_payload({"data": []}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "encodings": ["deflate"]}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "encodings": []}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "force": "deflate"}))


class LiveServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = context_server.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def put(self, pid, **body):
        response, _ = self.request("PUT", f"/payload/{pid}", {"data": DATA, **body})
        self.assertEqual(response.status, 200)

    def recorded(self, pid):
        _, data = self.request("GET", f"/requests/{pid}")
        return json.loads(data)["requests"]

    def test_gzip_when_advertised(self):
        self.put("a")
        response, body = self.request("GET", "/negotiate/a/context?application=x", headers={
            "Accept-Encoding": "gzip, deflate", "User-Agent": "probe/1"})
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(response.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(json.loads(gzip.decompress(body)), DATA)
        [record] = self.recorded("a")
        self.assertEqual(record["acceptEncoding"], "gzip, deflate")
        self.assertEqual(record["userAgent"], "probe/1")
        self.assertEqual(record["served"], "gzip")
        self.assertEqual(record["query"], "application=x")

    def test_identity_without_accept_encoding(self):
        self.put("b")
        response, body = self.request("GET", "/negotiate/b/context", headers={"Accept-Encoding": ""})
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertEqual(response.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(json.loads(body), DATA)
        self.assertEqual(self.recorded("b")[0]["served"], "identity")

    def test_forced_gzip_is_sent_unrequested(self):
        self.put("c", force="gzip")
        response, body = self.request("GET", "/negotiate/c/context", headers={"Accept-Encoding": "identity"})
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(json.loads(gzip.decompress(body)), DATA)

    def test_refresh_put_keeps_negotiation_settings(self):
        self.put("d", encodings=["identity"])
        self.request("PUT", "/payload/d", {"data": {"experiments": []}})
        response, body = self.request("GET", "/negotiate/d/context", headers={"Accept-Encoding": "gzip"})
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertEqual(json.loads(body), {"experiments": []})

    def test_unknown_payload_is_recorded_and_404(self):
        response, _ = self.request("GET", "/negotiate/nope/context")
        self.assertEqual(response.status, 404)
        self.assertEqual(self.recorded("nope")[0]["status"], 404)

    def test_delete_drops_records(self):
        self.put("e")
        self.request("GET", "/negotiate/e/context")
        self.request("DELETE", "/payload/e")
        self.assertEqual(self.recorded("e"), [])

    def test_bad_payload_rejected(self):
        response, _ = self.request("PUT", "/payload/f", {"data": DATA, "encodings": ["deflate"]})
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
