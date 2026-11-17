"""Docker-free tests for the negotiating context server (context-server/server.py)."""

import gzip
import http.client
import json
import os
import sys
import threading
import unittest

# Pinned in orchestrator/requirements.txt (CI installs it): a missing wheel is an
# ImportError that fails this module, never a silent skip.
import brotli

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "context-server"))

import negotiation  # noqa: E402
import server as context_server  # noqa: E402

DATA = {"experiments": [{"id": 1, "name": "exp"}]}


class NegotiationRules(unittest.TestCase):
    """Absolute expectations for the one negotiation module (server and orchestrator import it)."""

    def test_parse_accept_encoding(self):
        parse = negotiation.parse_accept_encoding
        self.assertEqual(parse(None), {})
        self.assertEqual(parse(""), {})
        self.assertEqual(parse("gzip, deflate;q=0.5, br;q=0"), {"gzip": 1.0, "deflate": 0.5, "br": 0.0})
        self.assertEqual(parse("  GZIP ;  Q=0.5 ,, Deflate"), {"gzip": 0.5, "deflate": 1.0})
        self.assertEqual(parse("x-gzip"), {"gzip": 1.0})
        self.assertEqual(parse("gzip;q=oops"), {"gzip": 0.0})
        self.assertEqual(parse("gzip, gzip;q=0"), {"gzip": 0.0})  # last one wins

    def test_advertises_requires_an_explicit_positive_q(self):
        advertises = negotiation.advertises
        for header in ("gzip", "gzip, deflate", "x-gzip", "X-GZIP", " Gzip ; q=0.1", "deflate, gzip;q=1.0"):
            self.assertTrue(advertises(header, "gzip"), header)
        for header in (None, "", "*", "deflate", "gzip;q=0", "x-gzip;q=0", "identity", "*;q=1"):
            self.assertFalse(advertises(header, "gzip"), header)

    def test_choose_encoding(self):
        choose = negotiation.choose_encoding
        cases = {
            None: "identity",
            "": "identity",
            "gzip": "gzip",
            "gzip, deflate": "gzip",
            "GZIP": "gzip",
            " gzip ;q=0.5 ": "gzip",
            "x-gzip": "gzip",
            "deflate": "identity",
            "gzip;q=0": "identity",
            "gzip;q=0, identity": "identity",
            "identity;q=0, gzip": "gzip",
            "*": "gzip",
            "*;q=0": "identity",  # nothing acceptable: deliberate identity fallback, not 406
            "identity;q=0": "identity",  # same fallback
            "*;q=0, gzip": "gzip",
            "br, zstd": "identity",  # never served, only reported
        }
        for header, served in cases.items():
            self.assertEqual(choose(header), served, header)
        self.assertEqual(choose("gzip", ["identity"]), "identity")
        self.assertEqual(choose("gzip", ["br", "zstd", "gzip"]), "gzip")  # unknown codings are skipped
        self.assertEqual(choose(None, ["identity"], force="gzip"), "gzip")  # force ignores the request

    def test_multiple_header_lines_are_one_list(self):
        combined = negotiation.combine_header_lines(["deflate", "gzip;q=0.5"])
        self.assertEqual(combined, "deflate, gzip;q=0.5")
        self.assertTrue(negotiation.advertises(combined, "gzip"))
        self.assertIsNone(negotiation.combine_header_lines(None))
        self.assertIsNone(negotiation.combine_header_lines([]))

    def test_server_produces_br_gzip_or_identity_and_never_zstd(self):
        self.assertEqual(set(negotiation.CODINGS), {"br", "gzip", "identity"})
        self.assertEqual(set(context_server.COMPRESSORS), set(negotiation.CODINGS))

    def test_gzip_group_preference_never_serves_br(self):
        # 270-272 and the contextFetchCompression probe: br is never chosen,
        # whatever the SDK advertises (swift sends `deflate, gzip, br, zstd`).
        self.assertEqual(negotiation.DEFAULT_PREFERENCE, ["gzip", "identity"])
        for header in ("deflate, gzip, br, zstd", "br", "br;q=1, gzip;q=0.1", "*", "zstd, br, gzip"):
            self.assertNotEqual(negotiation.choose_encoding(header), "br", header)
        self.assertEqual(negotiation.choose_encoding("br"), "identity")
        self.assertEqual(negotiation.choose_encoding("deflate, gzip, br, zstd"), "gzip")

    def test_br_preference_is_br_then_gzip_honouring_qvalues(self):
        br = negotiation.BR_PREFERENCE
        self.assertEqual(br, ["br", "gzip", "identity"])
        cases = {
            "deflate, gzip, br, zstd": "br",       # swift
            "gzip, deflate, br": "br",
            "br": "br",
            "BR": "br",
            "br;q=0.1, gzip;q=1": "br",            # server preference, not client q order
            "br;q=0, gzip": "gzip",                # br refused
            "br;q=0": "identity",
            "gzip, br;q=0": "gzip",
            "br;q=0, *": "gzip",                   # `*` does not revive a refused br
            "gzip": "gzip",
            "gzip, deflate": "gzip",
            "zstd": "identity",                    # zstd is never served
            "zstd, gzip": "gzip",
            "*": "br",
            "*;q=0, gzip": "gzip",
            "": "identity",
            None: "identity",
            "identity": "identity",
            "br;q=oops, gzip": "gzip",             # malformed q is a refusal
        }
        for header, served in cases.items():
            self.assertEqual(negotiation.choose_encoding(header, br), served, header)
        self.assertEqual(negotiation.choose_encoding("gzip", ["br", "identity"]), "identity")

    def test_advertises_br(self):
        for header in ("br", "gzip, br", "deflate, gzip, br, zstd", " BR ;q=0.5"):
            self.assertTrue(negotiation.advertises(header, "br"), header)
        for header in (None, "", "*", "gzip", "br;q=0", "brotli"):
            self.assertFalse(negotiation.advertises(header, "br"), header)

    def test_payload_validation(self):
        self.assertIsNone(context_server.validate_payload({"data": DATA}))
        self.assertIsNone(context_server.validate_payload({"data": DATA, "encodings": ["gzip", "identity"]}))
        self.assertIsNotNone(context_server.validate_payload({"data": []}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "encodings": ["deflate"]}))
        self.assertIsNone(context_server.validate_payload({"data": DATA, "encodings": ["br", "gzip", "identity"]}))
        self.assertIsNone(context_server.validate_payload({"data": DATA, "force": "br"}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "encodings": ["zstd"]}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "encodings": []}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "force": "deflate"}))
        self.assertIsNotNone(context_server.validate_payload({"data": DATA, "force": "zstd"}))


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

    def test_repeated_accept_encoding_lines_are_combined(self):
        self.put("g")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("GET", "/negotiate/g/context", skip_accept_encoding=True)
        conn.putheader("Accept-Encoding", "deflate")
        conn.putheader("Accept-Encoding", "gzip")
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(json.loads(gzip.decompress(body)), DATA)
        self.assertEqual(self.recorded("g")[0]["acceptEncoding"], "deflate, gzip")

    def test_absent_accept_encoding_is_recorded_as_none(self):
        self.put("h")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("GET", "/negotiate/h/context", skip_accept_encoding=True)
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertIsNone(self.recorded("h")[0]["acceptEncoding"])

    def test_br_when_advertised_on_a_br_payload(self):
        self.put("br1", encodings=negotiation.BR_PREFERENCE)
        response, body = self.request("GET", "/negotiate/br1/context", headers={
            "Accept-Encoding": "deflate, gzip, br, zstd"})
        self.assertEqual(response.getheader("Content-Encoding"), "br")
        self.assertEqual(response.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(json.loads(brotli.decompress(body)), DATA)
        # Encoded at quality 5 (server.py COMPRESSORS), not brotli's slow default 11.
        self.assertEqual(body, brotli.compress(json.dumps(DATA).encode(), quality=5))
        [record] = self.recorded("br1")
        self.assertEqual(record["served"], "br")
        self.assertEqual(record["bodyBytes"], len(body))

    def test_br_payload_serves_gzip_when_br_is_refused(self):
        self.put("br2", encodings=negotiation.BR_PREFERENCE)
        response, body = self.request("GET", "/negotiate/br2/context", headers={"Accept-Encoding": "gzip, br;q=0"})
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(json.loads(gzip.decompress(body)), DATA)
        self.assertEqual(self.recorded("br2")[0]["served"], "gzip")

    def test_gzip_payload_never_serves_br(self):
        self.put("br3")  # default preference: the gzip group's
        response, body = self.request("GET", "/negotiate/br3/context", headers={"Accept-Encoding": "br"})
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertEqual(json.loads(body), DATA)

    def test_refresh_put_keeps_br_settings(self):
        self.put("br4", encodings=negotiation.BR_PREFERENCE)
        self.request("PUT", "/payload/br4", {"data": {"experiments": []}})
        response, body = self.request("GET", "/negotiate/br4/context", headers={"Accept-Encoding": "br"})
        self.assertEqual(response.getheader("Content-Encoding"), "br")
        self.assertEqual(json.loads(brotli.decompress(body)), {"experiments": []})

    def test_head_is_not_routed(self):
        response, _ = self.request("HEAD", "/health")
        self.assertEqual(response.status, 501)

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
