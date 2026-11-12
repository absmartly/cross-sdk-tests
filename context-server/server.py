#!/usr/bin/env python3
"""Negotiating context server: one place that decides the encoding of GET /context.

The SDKs under test fetch `{endpoint}/context` from here instead of from their
wrapper's own mock endpoint, so what each SDK asks for (Accept-Encoding) and how
it copes with what it gets back is decided and recorded in one process, not in
twenty wrappers.

Control API (used by the orchestrator):
  PUT    /payload/{id}   {"data": {...}, "encodings": [...], "force": null|"gzip"|"identity"}
                         Stores the context data the SDK will fetch. `encodings` is the
                         server preference list (subset of gzip, identity, in order); the
                         first one the client accepts is served (see negotiation.py).
                         `force` serves that encoding even when it was not advertised.
                         Omitted `encodings`/`force` keep the stored values (refresh).
  GET    /requests/{id}  Every request the SDK made for that payload, in order.
  DELETE /payload/{id}   Drops the payload and its recorded requests.
  GET    /health

SDK-facing API:
  GET    /negotiate/{id}/context   The collector's GET /context, negotiated.

The path deliberately does not start with /context_payload/: some wrappers
rewrite such endpoints to their own host.

Only gzip and identity are served. Whether an SDK also lists br or zstd is
reported by the orchestrator from the recorded Accept-Encoding, never served.
"""

import gzip
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from negotiation import CODINGS, DEFAULT_PREFERENCE, choose_encoding, combine_header_lines

COMPRESSORS = {"gzip": lambda body: gzip.compress(body, mtime=0), "identity": lambda body: body}
assert set(COMPRESSORS) == set(CODINGS)


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.payloads = {}
        self.requests = {}

    def put(self, payload_id, body):
        with self.lock:
            entry = self.payloads.get(payload_id, {"encodings": list(DEFAULT_PREFERENCE), "force": None})
            entry = dict(entry)
            entry["data"] = body["data"]
            if "encodings" in body:
                entry["encodings"] = list(body["encodings"])
            if "force" in body:
                entry["force"] = body["force"]
            self.payloads[payload_id] = entry
            self.requests.setdefault(payload_id, [])

    def get(self, payload_id):
        with self.lock:
            return self.payloads.get(payload_id)

    def record(self, payload_id, record):
        with self.lock:
            self.requests.setdefault(payload_id, []).append(record)

    def recorded(self, payload_id):
        with self.lock:
            return list(self.requests.get(payload_id, []))

    def delete(self, payload_id):
        with self.lock:
            self.payloads.pop(payload_id, None)
            self.requests.pop(payload_id, None)


def validate_payload(body):
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        return "body must be an object with a 'data' object"
    if "encodings" in body:
        encodings = body["encodings"]
        if not isinstance(encodings, list) or not encodings or any(e not in CODINGS for e in encodings):
            return f"'encodings' must be a non-empty list drawn from {list(CODINGS)}"
    force = body.get("force")
    if force is not None and force not in CODINGS:
        return f"'force' must be null or one of {list(CODINGS)}"
    return None


NEGOTIATE = re.compile(r"^/negotiate/([^/]+)/context$")
PAYLOAD = re.compile(r"^/payload/([^/]+)$")
REQUESTS = re.compile(r"^/requests/([^/]+)$")


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet by default
            if os.environ.get("CONTEXT_SERVER_LOG"):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status, body, headers=None):
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj).encode(), {"Content-Type": "application/json"})

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                return self._json(200, {"status": "ok", "encodings": list(CODINGS)})
            match = REQUESTS.match(path)
            if match:
                return self._json(200, {"requests": store.recorded(match.group(1))})
            match = NEGOTIATE.match(path)
            if match:
                return self._serve_context(match.group(1))
            return self._json(404, {"error": f"no route for GET {path}"})

        def _serve_context(self, payload_id):
            entry = store.get(payload_id)
            # RFC 9110 5.3: repeated header lines form one comma-separated list.
            accept = combine_header_lines(self.headers.get_all("Accept-Encoding"))
            record = {
                "method": "GET",
                "path": urlsplit(self.path).path,
                "query": urlsplit(self.path).query,
                "acceptEncoding": accept,
                "userAgent": self.headers.get("User-Agent"),
            }
            if entry is None:
                record["status"] = 404
                record["served"] = None
                store.record(payload_id, record)
                return self._json(404, {"error": f"unknown payload {payload_id}"})
            coding = choose_encoding(accept, entry["encodings"], entry["force"])
            raw = json.dumps(entry["data"]).encode()
            body = COMPRESSORS[coding](raw)
            headers = {"Content-Type": "application/json", "Vary": "Accept-Encoding"}
            if coding != "identity":
                headers["Content-Encoding"] = coding
            record.update({"status": 200, "served": coding, "rawBytes": len(raw), "bodyBytes": len(body)})
            store.record(payload_id, record)
            self._send(200, body, headers)

        def do_PUT(self):
            path = urlsplit(self.path).path
            match = PAYLOAD.match(path)
            if not match:
                return self._json(404, {"error": f"no route for PUT {path}"})
            try:
                body = json.loads(self._body() or b"null")
            except ValueError:
                return self._json(400, {"error": "body is not JSON"})
            error = validate_payload(body)
            if error:
                return self._json(400, {"error": error})
            store.put(match.group(1), body)
            return self._json(200, {"success": True})

        def do_DELETE(self):
            path = urlsplit(self.path).path
            match = PAYLOAD.match(path)
            if not match:
                return self._json(404, {"error": f"no route for DELETE {path}"})
            store.delete(match.group(1))
            return self._json(200, {"success": True})

    return Handler


def make_server(host="0.0.0.0", port=8080):
    server = ThreadingHTTPServer((host, port), make_handler(Store()))
    server.daemon_threads = True
    return server


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = make_server(port=port)
    print(f"context-server listening on :{port}, encodings {list(CODINGS)}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
