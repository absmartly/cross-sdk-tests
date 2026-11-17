"""In-process fake wrapper for Docker-free tests of the context-fetch wiring.

It behaves like a wrapper whose "SDK" fetches `{endpoint}/context` with
urllib, for exactly the experiment shape of scenarios 270-274 (exp_test_ab:
variant 1 while trafficSplit is [0, 1], else out of traffic: variant 0,
ineligible). `mode` selects the SDK behaviour:

  gzip         sends Accept-Encoding: gzip, decodes gzip (a conforming SDK)
  none         sends Accept-Encoding: identity (never gzip) and cannot decode gzip
  none-http    like none, but a failed load answers HTTP 500 (create and refresh)
               instead of 200 with `failed: true`
  stale        like gzip, but keeps its old data after a refresh fetch
  skip-fetch   like gzip, but refresh never fetches
  br           sends Accept-Encoding: br, gzip; decodes br and gzip
  br-stale     like br, but keeps its old data after a refresh fetch
  br-strip     like br, but the wrapper strips Accept-Encoding on refresh (sends identity)
  br-q0        sends Accept-Encoding: gzip, br;q=0 (refuses br); decodes gzip, cannot decode br
"""

import gzip
import json

import brotli
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeSDKContext:
    def __init__(self, wrapper, endpoint, units, data=None):
        self.wrapper = wrapper
        self.endpoint = endpoint
        self.units = units
        self.failed = False
        self.error = None
        self.last_fetch_failed = False
        self.assignments = {}
        self.exposures = []
        self.fetches = 0
        self.data = data if data is not None else self.fetch()

    def fetch(self):
        headers = {"User-Agent": "fake-sdk/1"}
        mode = self.wrapper.mode
        if mode in ("gzip", "stale", "skip-fetch"):
            headers["Accept-Encoding"] = "gzip"
        elif mode == "br-strip" and self.fetches:
            headers["Accept-Encoding"] = "identity"
        elif mode in ("br", "br-stale", "br-strip"):
            headers["Accept-Encoding"] = "br, gzip"
        elif mode == "br-q0":
            headers["Accept-Encoding"] = "gzip, br;q=0"
        else:
            headers["Accept-Encoding"] = "identity"  # urllib would otherwise send nothing either
        self.fetches += 1
        request = urllib.request.Request(f"{self.endpoint}/context?application=website&environment=test",
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    if self.wrapper.mode in ("none", "none-http"):
                        raise ValueError("cannot decode gzip")
                    body = gzip.decompress(body)
                elif response.headers.get("Content-Encoding") == "br":
                    if not self.wrapper.mode.startswith("br") or self.wrapper.mode == "br-q0":
                        raise ValueError("cannot decode br")
                    body = brotli.decompress(body)
                self.last_fetch_failed = False
                return json.loads(body)
        except Exception as exc:  # noqa: BLE001 - mirrors an SDK's failed load
            self.last_fetch_failed = True
            self.failed = True
            self.error = str(exc)
            return {"experiments": []}

    def refresh(self):
        if self.wrapper.mode == "skip-fetch":
            return
        data = self.fetch()
        if self.wrapper.mode not in ("stale", "br-stale"):
            self.data = data

    def experiment(self, name):
        return next((e for e in self.data["experiments"] if e["name"] == name), None)

    def treatment(self, name):
        exp = self.experiment(name)
        in_traffic = exp is not None and exp["trafficSplit"] == [0, 1]
        key = (name, json.dumps(exp, sort_keys=True))
        events = []
        if self.assignments.get(name) != key:
            self.assignments[name] = key
            variant = 1 if in_traffic else 0
            exposure = {"id": exp["id"], "name": name, "unit": "session_id", "variant": variant,
                        "assigned": True, "eligible": in_traffic, "overridden": False, "fullOn": False,
                        "custom": False, "audienceMismatch": False}
            self.exposures.append({"id": exp["id"], "name": name, "variant": variant})
            events.append({"type": "exposure", "data": exposure})
        return (1 if in_traffic else 0), events

    def variable(self, key, default):
        variant, events = self.treatment("exp_test_ab")
        config = self.experiment("exp_test_ab")["variants"][variant]["config"]
        return (json.loads(config).get(key, default) if config else default), events


class FakeWrapper:
    def __init__(self, mode="gzip"):
        self.mode = mode
        self.contexts = {}
        self.refresh_bodies = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        wrapper = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                return json.loads(raw) if raw else {}

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                if parts == ["health"]:
                    return self._json(200, {"status": "ok"})
                if parts == ["capabilities"]:
                    return self._json(200, {})
                if len(parts) == 3 and parts[0] == "context" and parts[2] == "experiments":
                    ctx = wrapper.contexts[parts[1]]
                    return self._json(200, {"result": [e["name"] for e in ctx.data["experiments"]], "events": []})
                return self._json(404, {"error": "no route"})

            def do_DELETE(self):
                wrapper.contexts.pop(self.path.strip("/").split("/")[-1], None)
                return self._json(200, {"result": None, "events": []})

            def do_POST(self):
                body = self._body()
                parts = self.path.split("?")[0].strip("/").split("/")
                if parts == ["context"]:
                    ctx = FakeSDKContext(wrapper, body.get("endpoint"), body.get("units"), body.get("data"))
                    if ctx.failed and wrapper.mode == "none-http":
                        return self._json(500, {"error": f"context failed: {ctx.error}", "events": []})
                    context_id = str(uuid.uuid4())
                    wrapper.contexts[context_id] = ctx
                    refs = [{"id": e["id"], "name": e["name"]} for e in ctx.data["experiments"]]
                    events = [] if ctx.failed else [{"type": "ready", "data": {"experiments": refs}}]
                    return self._json(200, {"result": {"contextId": context_id, "ready": True,
                                                       "failed": ctx.failed, "finalized": False},
                                            "events": events})
                ctx = wrapper.contexts[parts[1]]
                action = parts[2]
                if action == "refresh":
                    wrapper.refresh_bodies.append(body)
                    if body.get("newData") is not None:
                        ctx.data = body["newData"]
                    else:
                        ctx.refresh()
                        if ctx.last_fetch_failed and wrapper.mode == "none-http":
                            return self._json(500, {"error": f"refresh failed: {ctx.error}", "events": []})
                    refs = [{"id": e["id"], "name": e["name"]} for e in ctx.data["experiments"]]
                    return self._json(200, {"result": None, "events": [{"type": "refresh",
                                                                         "data": {"experiments": refs}}]})
                if action == "treatment":
                    result, events = ctx.treatment(body["experimentName"])
                    return self._json(200, {"result": result, "events": events})
                if action == "variableValue":
                    result, events = ctx.variable(body["key"], body["defaultValue"])
                    return self._json(200, {"result": result, "events": events})
                if action == "publish":
                    exposures, ctx.exposures = ctx.exposures, []
                    return self._json(200, {"result": None,
                                            "events": [{"type": "publish", "data": {"exposures": exposures}}]})
                if action == "readyError":
                    return self._json(200, {"result": ctx.error, "events": []})
                return self._json(404, {"error": f"no action {action}"})

        return Handler
