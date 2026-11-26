#!/usr/bin/env python3
"""Local collector: the blocking SDK event-emission e2e gate.

It stands in for the ABsmartly collector at `{ABSMARTLY_E2E_ENDPOINT}` so each
SDK's real client and default publisher run end to end without test-1:

  GET  {prefix}/context   Deterministic context data (the e2e experiment below).
  PUT  {prefix}/context   One SDK publish. The raw body bytes, the parsed JSON and
                          every request header are stored verbatim; 200 `{}`.

`{prefix}` is empty or `/v1`, so an endpoint of `http://collector:8090/v1` or
`http://collector:8090` both work.

Control API (used by the orchestrator and the tests):
  GET    /health
  GET    /publishes       Every stored publish, in arrival order.
  GET    /requests        Every GET /context request (headers), in arrival order.
  DELETE /publishes       Clears publishes and requests.

This tests what the SDK emits and how it transports it, not analysis: the
collector never derives, merges or drops events. Validation lives in
orchestrator/local_collector_gate.py.
"""

import base64
import json
import os
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY_BYTES = 16 * 1024 * 1024
# A real publish nests a few levels; json.loads accepts ~990 on 3.11, and a body
# that deep breaks json.dumps when /publishes is read back, so reject it on PUT.
MAX_JSON_DEPTH = 64
# A client that sends fewer body bytes than Content-Length and keeps the socket open
# would otherwise hold a handler thread forever. The socket timeout bounds each read;
# REQUEST_DEADLINE_S bounds the whole body, so a trickling client cannot reset it.
REQUEST_TIMEOUT_S = 10
REQUEST_DEADLINE_S = 30
EXPERIMENT_ID = 9001
EXPERIMENT_NAME = "e2e_local_collector"
SESSION_EXPERIMENT_ID = 9002
SESSION_EXPERIMENT_NAME = "e2e_local_collector_session"


def _experiment(experiment_id, name, unit_type):
    return {
        "id": experiment_id,
        "name": name,
        "iteration": 1,
        "unitType": unit_type,
        "seedHi": 3603515,
        "seedLo": 233373850,
        "split": [0, 1],
        "trafficSeedHi": 449867249,
        "trafficSeedLo": 455443629,
        "trafficSplit": [0, 1],
        "fullOnVariant": 0,
        "applications": [{"name": os.environ.get("COLLECTOR_APPLICATION", "e2e-tests")}],
        "variants": [{"name": "control", "config": None}, {"name": "treatment", "config": None}],
        "audience": "",
        "audienceStrict": False,
        "customFieldValues": None,
    }


def context_data():
    """The deterministic context every SDK fetches.

    traffic split [0, 1] puts every unit in each experiment and split [0, 1]
    assigns variant 1 to every unit, so the expected exposure variant never
    depends on an SDK's hashing. One experiment per unit type the cases use.
    """
    return {"experiments": [
        _experiment(EXPERIMENT_ID, EXPERIMENT_NAME, "user_id"),
        _experiment(SESSION_EXPERIMENT_ID, SESSION_EXPERIMENT_NAME, "session_id"),
    ]}


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.publishes = []
        self.requests = []

    def add_publish(self, record):
        with self.lock:
            record["seq"] = len(self.publishes)
            self.publishes.append(record)

    def add_request(self, record):
        with self.lock:
            self.requests.append(record)

    def snapshot(self):
        with self.lock:
            return list(self.publishes), list(self.requests)

    def clear(self):
        with self.lock:
            self.publishes.clear()
            self.requests.clear()


def parse_content_length(values):
    """Content-Length as an int per RFC 9110 (1*DIGIT), or None when invalid.

    `values` is every Content-Length header line. int() alone accepts "1_0", "+5"
    and " 7"; an empty line or conflicting duplicate lines are ambiguous framing.
    """
    if not values:
        return None
    if len(set(values)) > 1:
        return None
    value = values[0]
    if not value.isascii() or not value.isdigit():
        return None
    return int(value)


def body_length_error(length):
    # parse_content_length never returns a negative number, so None covers "invalid".
    if length is None or length > MAX_BODY_BYTES:
        return "invalid Content-Length"
    return None


def gunzip_capped(raw):
    """Inflate every gzip member of a body, refusing to grow past MAX_BODY_BYTES.

    gzip.decompress reads concatenated members, so this does too; a member that
    does not end cleanly, or bytes after the last member that are not another
    member, is malformed.
    """
    out = bytearray()
    data = raw
    while data:
        inflater = zlib.decompressobj(31)
        chunk = inflater.decompress(data, MAX_BODY_BYTES + 1 - len(out))
        out += chunk
        if len(out) > MAX_BODY_BYTES:
            raise ValueError("decoded body exceeds the size limit")
        if not inflater.eof:
            raise ValueError("truncated gzip body")
        data = inflater.unused_data
    return bytes(out)


def _reject_constant(name):
    raise ValueError(f"non-standard JSON constant {name}")


def json_depth_error(value, limit=MAX_JSON_DEPTH):
    """Iteratively measure nesting so the check itself cannot recurse too deep."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if depth > limit:
                return f"JSON nested deeper than {limit} levels"
            children = node.values() if isinstance(node, dict) else node
            stack.extend((child, depth + 1) for child in children)
    return None


def _strip_prefix(path):
    return path[len("/v1"):] if path == "/v1" or path.startswith("/v1/") else path


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_S

        def log_message(self, fmt, *args):
            if os.environ.get("COLLECTOR_VERBOSE"):
                sys.stderr.write("collector: " + fmt % args + "\n")

        def _send(self, status, body):
            raw = json.dumps(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                # The client went away; there is nobody left to answer.
                self.close_connection = True

        def _headers(self):
            return [[name, value] for name, value in self.headers.items()]

        def _read_exact(self, length):
            deadline = time.monotonic() + REQUEST_DEADLINE_S
            chunks = []
            remaining = length
            while remaining:
                if time.monotonic() > deadline:
                    raise ValueError("request body not received within the deadline")
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    raise ValueError("truncated request body")
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        def _read_body(self):
            try:
                if self.headers.get_all("Transfer-Encoding"):
                    # RFC 9112: Transfer-Encoding with Content-Length is a smuggling vector;
                    # SDK publishers always send a plain Content-Length.
                    raise ValueError("Transfer-Encoding is not supported")
                length = parse_content_length(self.headers.get_all("Content-Length") or [])
                length_error = body_length_error(length)
                if length_error:
                    raise ValueError(length_error)
                raw = self._read_exact(length)
                decoded = gunzip_capped(raw) if (self.headers.get("Content-Encoding") or "").lower() == "gzip" else raw
                return raw, decoded, None
            except (ValueError, EOFError, OSError, zlib.error) as exc:
                return b"", b"", str(exc)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                return self._send(200, {"ok": True})
            if path == "/publishes":
                return self._send(200, {"publishes": store.snapshot()[0]})
            if path == "/requests":
                return self._send(200, {"requests": store.snapshot()[1]})
            if _strip_prefix(path) == "/context":
                store.add_request({"method": "GET", "path": self.path, "headers": self._headers(),
                                   "receivedAt": int(time.time() * 1000)})
                return self._send(200, context_data())
            return self._send(404, {"error": "not found"})

        def do_PUT(self):
            path = urlsplit(self.path).path
            if _strip_prefix(path) != "/context":
                return self._send(404, {"error": "not found"})
            raw, decoded, body_error = self._read_body()
            record = {"method": "PUT", "path": self.path, "headers": self._headers(),
                      "receivedAt": int(time.time() * 1000),
                      "rawBodyBytes": base64.b64encode(raw).decode("ascii"),
                      "decodedBodyBytes": base64.b64encode(decoded).decode("ascii"),
                      "contentEncoding": self.headers.get("Content-Encoding")}
            if body_error:
                # Broken framing leaves the stream position unknown; never reuse it.
                self.close_connection = True
                return self._send(400, {"error": body_error})
            try:
                record["rawBody"] = decoded.decode("utf-8", errors="strict")
                record["json"] = json.loads(decoded, parse_constant=_reject_constant)
                depth_error = json_depth_error(record["json"])
                if depth_error:
                    raise ValueError(depth_error)
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                # Reject with a deterministic 400 rather than letting a malformed body
                # kill the request handler.
                return self._send(400, {"error": str(exc)})
            store.add_publish(record)
            return self._send(200, {})

        do_POST = do_PUT

        def do_DELETE(self):
            if urlsplit(self.path).path == "/publishes":
                store.clear()
                return self._send(200, {})
            return self._send(404, {"error": "not found"})

    return Handler


def make_server(host, port, store=None):
    return ThreadingHTTPServer((host, port), make_handler(store or Store()))


def main():
    port = int(os.environ.get("COLLECTOR_PORT", "8090"))
    server = make_server("0.0.0.0", port)
    print(f"local collector listening on {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
