"""Docker-free end-to-end tests of the context-fetch wiring.

A real context server (context-server/server.py, in process) and a fake
wrapper (fake_wrapper.py) whose "SDK" fetches from it. Covers the probe
(`_probe_context_fetch` / `measure_context_fetch_compression`), scenarios
270-272 as generated, the empty-body refresh of contextServer scenarios and
the directData path.
"""

import copy
import json
import os
import socket
import sys
import threading
from unittest import mock

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "orchestrator"))
sys.path.insert(0, os.path.join(ROOT, "context-server"))

import generate_context_compression_scenarios as generator  # noqa: E402
import results_aggregator  # noqa: E402
import server as context_server  # noqa: E402
import test_runner  # noqa: E402
from fake_wrapper import FakeWrapper  # noqa: E402
from test_runner import TestOrchestrator  # noqa: E402

CAP = "contextFetchCompression"


@pytest.fixture(scope="module")
def server_url():
    server = context_server.make_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    with mock.patch.object(test_runner, "CONTEXT_SERVER_URL", url), \
            mock.patch.object(test_runner, "CONTEXT_SERVER_SDK_URL", url):
        yield url
    server.shutdown()
    server.server_close()


@pytest.fixture
def wrapper_factory():
    made = []

    def make(mode):
        wrapper = FakeWrapper(mode)
        made.append(wrapper)
        return wrapper

    yield make
    for wrapper in made:
        wrapper.close()


def orchestrator_for(wrapper, required=True):
    entry = {"required": [CAP], "unsupported": {}} if required else {"required": [], "unsupported": {CAP: "x"}}
    policy = {"capabilities": {CAP: "measured"}, "measuredCapabilities": [CAP], "sdks": {"fake": entry}}
    return TestOrchestrator({"fake": wrapper.url}, capability_policy=policy)


def run(orch, scenario):
    with mock.patch("builtins.print"):
        return orch.run_and_validate_sdk("fake", orch.sdks["fake"], scenario)


# -- the probe ---------------------------------------------------------------


def test_probe_measures_a_conforming_sdk(server_url, wrapper_factory):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    with mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert orch.capabilities["fake"][CAP] is True
    assert "fake" not in orch.measurement_failed and "fake" not in orch.capability_policy_violations
    probe = orch.context_fetch_probe["fake"]
    assert probe["acceptEncoding"] == ["gzip", "gzip"]  # create and refresh
    assert probe["served"] == ["gzip", "gzip"]
    assert probe["userAgent"] == ["fake-sdk/1"]
    assert probe["refreshFetched"] is True and probe["decoded"] is True and probe["refreshDecoded"] is True
    assert probe["advertises"] == {"gzip": True, "br": False, "zstd": False}
    forced = probe["forcedGzip"]
    assert forced["reportOnly"] is True and forced["decoded"] is True
    assert forced["requested"] is True  # this SDK advertised gzip: forced, but requested
    assert "unrequestedGzip" not in probe
    assert wrapper.refresh_bodies == [{}]  # the wrapper got no data to apply
    assert wrapper.contexts == {}  # probe contexts deleted


def test_probe_records_an_sdk_that_cannot_decode_forced_gzip(server_url, wrapper_factory):
    wrapper = wrapper_factory("none")
    orch = orchestrator_for(wrapper, required=False)
    with mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert orch.capabilities["fake"][CAP] is False
    assert "fake" not in orch.capability_policy_violations
    probe = orch.context_fetch_probe["fake"]
    assert probe["acceptEncoding"] == ["identity", "identity"] and probe["served"] == ["identity", "identity"]
    forced = probe["forcedGzip"]
    assert forced["failed"] is True and "cannot decode gzip" in forced["error"]
    assert forced["requested"] is False  # identity only: the forced gzip was unrequested


@pytest.mark.parametrize("mode", ["stale", "skip-fetch"])
def test_probe_rejects_a_refresh_that_does_not_bring_the_new_data(server_url, wrapper_factory, mode):
    wrapper = wrapper_factory(mode)
    orch = orchestrator_for(wrapper)
    with mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert orch.capabilities["fake"][CAP] is False
    assert "measured false" in orch.capability_policy_violations["fake"][0]
    assert "refresh data not used" in orch.capability_policy_violations["fake"][0]


def closed_port():
    """A local port nothing listens on: bound, read, then released."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_probe_against_an_unreachable_context_server_is_a_measurement_failure(wrapper_factory):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    with mock.patch.object(test_runner, "CONTEXT_SERVER_URL", f"http://127.0.0.1:{closed_port()}"), \
            mock.patch.object(test_runner, "CONTEXT_SERVER_READY_TIMEOUT", 0), \
            mock.patch.object(test_runner.time, "sleep"), mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert "not ready" in orch.measurement_failed["fake"][CAP]
    assert "fake" not in orch.capabilities_fetch_failed


def test_failed_refresh_put_is_a_measurement_failure_not_a_policy_violation(server_url, wrapper_factory):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    real_put = orch.context_server_put
    calls = []

    def put(payload_id, body):
        calls.append(payload_id)
        if len(calls) == 2:  # the refresh-data PUT, after the SDK's create fetch
            raise test_runner.requests.HTTPError("500 Server Error")
        return real_put(payload_id, body)

    with mock.patch.object(orch, "context_server_put", side_effect=put), mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert "500 Server Error" in orch.measurement_failed["fake"][CAP]
    assert "fake" not in orch.capability_policy_violations
    assert CAP not in orch.capabilities["fake"]
    assert wrapper.contexts == {}  # the probe context is still deleted


@pytest.mark.parametrize("suffix", ["/context", "/refresh"])
def test_probe_timeout_is_a_measurement_failure_not_no_support(server_url, wrapper_factory, suffix):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    real_post = test_runner.requests.post

    def post(url, *args, **kwargs):
        response = real_post(url, *args, **kwargs)  # the SDK's fetch reaches the server
        if url.endswith(suffix):
            raise test_runner.requests.ReadTimeout("Read timed out. (read timeout=5)")
        return response

    with mock.patch.object(test_runner.requests, "post", side_effect=post), mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert "timed out" in orch.measurement_failed["fake"][CAP]
    assert CAP not in orch.capabilities["fake"]
    assert "fake" not in orch.capability_policy_violations


def test_probe_handles_a_non_object_wrapper_body(server_url, wrapper_factory):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    bad = mock.Mock(status_code=200, content=b"[1]")
    bad.json.return_value = [1]
    real_post = test_runner.requests.post

    def post(url, *args, **kwargs):
        return bad if url.endswith("/context") else real_post(url, *args, **kwargs)

    with mock.patch.object(test_runner.requests, "post", side_effect=post):
        outcome = orch._probe_context_fetch(wrapper.url, generator.context_data(), None, None, 5,
                                           encodings=test_runner.negotiation.DEFAULT_PREFERENCE)
    assert "non-object body" in outcome["error"]
    assert outcome["requests"] == []


# -- scenarios 270-272 as generated --------------------------------------------


@pytest.fixture(scope="module")
def group():
    return generator.build_group(270)


def test_conforming_sdk_passes_the_group(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    for scenario in group[:4]:  # 274 needs br
        result = run(orch, scenario)
        assert result["passed"], (scenario["name"], result["failures"])
    # Refresh in a contextServer scenario sends the wrapper no data.
    assert wrapper.refresh_bodies == [{}, {}, {}]
    fetches = orch.context_fetches[group[0]["name"]]["fake"]
    assert [f["action"] for f in fetches] == ["createContext", "refresh"]
    assert [r["served"] for f in fetches for r in f["requests"]] == ["gzip", "gzip"]
    # A gzip-only SDK offered br (273) negotiates gzip and loads it identically.
    fetches = orch.context_fetches[group[3]["name"]]["fake"]
    assert [r["served"] for f in fetches for r in f["requests"]] == ["gzip", "gzip"]


# -- br: the contextFetchBrotli probe and scenarios 273-274 ---------------------

BR = "contextFetchBrotli"


def br_orchestrator_for(wrapper, required=True):
    """Both measured capabilities, br required (or excepted), gzip required."""
    caps = {CAP: "measured", BR: "measured"}
    entry = ({"required": [CAP, BR], "unsupported": {}} if required
             else {"required": [CAP], "unsupported": {BR: "x"}})
    policy = {"capabilities": caps, "measuredCapabilities": [CAP, BR], "sdks": {"fake": entry}}
    return TestOrchestrator({"fake": wrapper.url}, capability_policy=policy)


def test_br_probe_measures_a_br_sdk(server_url, wrapper_factory, tmp_path):
    wrapper = wrapper_factory("br")
    orch = br_orchestrator_for(wrapper)
    with mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert orch.capabilities["fake"][BR] is True and orch.capabilities["fake"][CAP] is True
    assert "fake" not in orch.measurement_failed and "fake" not in orch.capability_policy_violations
    probe = orch.context_fetch_brotli_probe["fake"]
    assert probe["acceptEncoding"] == ["br, gzip", "br, gzip"] and probe["served"] == ["br", "br"]
    assert probe["decoded"] is True and probe["refreshDecoded"] is True
    # The gzip probe never serves br, even to a br-advertising SDK.
    assert orch.context_fetch_probe["fake"]["served"] == ["gzip", "gzip"]
    # CI consumers read the br probe from report.json.
    report_path = tmp_path / "report.json"
    with mock.patch("builtins.print"):
        orch.generate_report(str(report_path), [])
    report = json.loads(report_path.read_text())
    assert report["context_fetch_brotli_probe"]["fake"]["contextFetchBrotli"] is True


@pytest.mark.parametrize("mode, failed", [
    ("gzip", "no br advertised"),
    ("br-q0", "no br advertised"),          # br;q=0 is a refusal, served gzip
    ("br-strip", "no br advertised"),       # the wrapper strips the header on refresh
    ("br-stale", "refresh data not used"),  # refetches br but keeps the old data
    ("br-skip-fetch", "refresh not fetched"),  # refresh never refetches
    ("br-undecodable", "not decoded"),         # advertises br, cannot decode the br body
])
def test_br_probe_rejects_an_sdk_without_br(server_url, wrapper_factory, mode, failed):
    wrapper = wrapper_factory(mode)
    orch = br_orchestrator_for(wrapper)
    with mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert orch.capabilities["fake"][BR] is False
    [violation] = [v for v in orch.capability_policy_violations["fake"] if BR in v]
    assert "measured false" in violation and failed in violation


def test_br_q0_is_served_gzip_by_the_probe(server_url, wrapper_factory):
    wrapper = wrapper_factory("br-q0")
    orch = br_orchestrator_for(wrapper, required=False)
    with mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert orch.context_fetch_brotli_probe["fake"]["served"] == ["gzip", "gzip"]
    assert orch.capabilities["fake"][BR] is False and "fake" not in orch.capability_policy_violations


def test_br_sdk_passes_the_whole_group(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("br")
    orch = br_orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True, BR: True}
    for scenario in group:
        result = run(orch, scenario)
        assert result["passed"], (scenario["name"], result["failures"])
    served = {s["name"]: [r["served"] for f in orch.context_fetches[s["name"]]["fake"] for r in f["requests"]]
              for s in group if "contextServer" in s}
    # 270 and 272 never serve br, even to an SDK that advertises it; 273 and 274 do.
    assert served == {group[0]["name"]: ["gzip", "gzip"], group[2]["name"]: ["gzip", "gzip"],
                      group[3]["name"]: ["br", "br"], group[4]["name"]: ["br", "br"]}


@pytest.mark.parametrize("mode", ["none", "gzip", "br-q0"])
def test_sdk_without_br_passes_273_and_fails_only_the_br_check_of_274(server_url, wrapper_factory, group, mode):
    wrapper = wrapper_factory(mode)
    orch = br_orchestrator_for(wrapper)
    assert run(orch, group[3])["passed"]
    fields = {f.get("field") for f in run(orch, group[4])["failures"]}
    assert fields == {"contextFetch.acceptEncoding[br]"}


def test_header_stripped_on_refresh_fails_274(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("br-strip")
    result = run(br_orchestrator_for(wrapper), group[4])
    refresh = [s["action"] for s in group[4]["steps"]].index("refresh")
    assert [(f["step"], f["field"]) for f in result["failures"]] == [(refresh, "contextFetch.acceptEncoding[br]")]


def test_stale_br_refresh_fails_the_post_refresh_assertion(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("br-stale")
    for scenario in (group[3], group[4]):
        result = run(br_orchestrator_for(wrapper), scenario)
        after_refresh = [s["action"] for s in scenario["steps"]].index("refresh") + 1
        assert {f["step"] for f in result["failures"]} == {after_refresh}


@pytest.mark.parametrize("header, served", [
    ("br, gzip", "gzip"),   # gzip served on a br-preferred request
    ("gzip", "br"),         # br served when br was not advertised
    ("gzip, br;q=0", "br"),  # br;q=0 ignored
])
def test_wrong_br_negotiation_is_a_harness_error_in_scenarios(server_url, wrapper_factory, group, header, served):
    wrapper = wrapper_factory("br")
    orch = br_orchestrator_for(wrapper)
    records = [{"status": 200, "acceptEncoding": header, "served": served}]
    with pytest.raises(test_runner.HarnessError) as exc:
        orch.check_context_fetches(0, "createContext", group[3]["contextServer"], records, 0)
    assert exc.value.served_fault is True and "negotiation chooses" in str(exc.value)


def test_br_served_to_the_gzip_group_is_a_harness_error(server_url, wrapper_factory, group, serve_wrong_encoding):
    """A br payload served to a 270-272 scenario: br where the gzip negotiation chooses gzip."""
    wrapper = wrapper_factory("br")
    orch = br_orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True, BR: True}
    serve_wrong_encoding("br", 1)
    with mock.patch("builtins.print"):
        orch.run_scenario(group[0])
    result = orch.results[0]["sdks"]["fake"]
    assert "served 'br'" in result["harness_error"] and "negotiation chooses 'gzip'" in result["harness_error"]
    assert result["failures"] == []


@pytest.mark.parametrize("coding", ["gzip", "identity"])
def test_br_probe_served_the_wrong_coding_is_a_measurement_failure(server_url, wrapper_factory, coding):
    wrapper = wrapper_factory("br")
    orch = br_orchestrator_for(wrapper)
    real = context_server.choose_encoding

    def choose(header, preference, force=None):
        # Break only the br probe's fetches.
        return coding if preference == ["br", "gzip", "identity"] else real(header, preference, force)

    with mock.patch.object(context_server, "choose_encoding", side_effect=choose), mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert "would not choose" in orch.measurement_failed["fake"][BR]
    assert BR not in orch.capabilities["fake"] and orch.capabilities["fake"][CAP] is True
    assert "fake" not in orch.capability_policy_violations
    assert "measurement of contextFetchBrotli failed" in orch.capability_gate("fake", {"requires": [BR]})
    assert orch.capability_gate("fake", {"requires": [CAP]}) is None


def test_self_claimed_brotli_does_not_survive_a_failed_measurement(server_url, wrapper_factory):
    wrapper = wrapper_factory("br")
    orch = br_orchestrator_for(wrapper)
    real_get = test_runner.requests.get

    def get(url, *args, **kwargs):
        if url.endswith("/capabilities"):
            return mock.Mock(status_code=200, json=mock.Mock(return_value={BR: True, CAP: True}))
        return real_get(url, *args, **kwargs)

    with mock.patch.object(orch, "measure_context_fetch_brotli", side_effect=test_runner.MeasurementError("down")), \
            mock.patch.object(test_runner.requests, "get", side_effect=get), mock.patch("builtins.print"):
        orch.fetch_capabilities("fake", wrapper.url)
    assert BR not in orch.capabilities["fake"]
    assert orch.measurement_failed["fake"] == {BR: "down"}
    assert "measurement of contextFetchBrotli failed" in orch.capability_gate("fake", {"requires": [BR]})


def test_stale_refresh_fails_the_post_refresh_assertion(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("stale")
    orch = orchestrator_for(wrapper)
    for scenario in (group[0], group[2]):
        result = run(orch, scenario)
        assert not result["passed"]
        steps = scenario["steps"]
        after_refresh = [s["action"] for s in steps].index("refresh") + 1
        assert {f["step"] for f in result["failures"]} == {after_refresh}


def test_refresh_without_a_fetch_fails(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("skip-fetch")
    result = run(orchestrator_for(wrapper), group[0])
    fields = [f.get("field") for f in result["failures"]]
    assert "contextFetch" in fields and "result" in fields


def test_sdk_without_gzip_passes_the_ungated_scenario_and_fails_the_gated_check(
        server_url, wrapper_factory, group):
    wrapper = wrapper_factory("none")
    orch = orchestrator_for(wrapper)
    assert run(orch, group[0])["passed"]
    fields = {f.get("field") for f in run(orch, group[2])["failures"]}
    assert fields == {"contextFetch.acceptEncoding[gzip]"}


def test_direct_data_twin_never_touches_the_network(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    with mock.patch.object(orch, "context_server_put") as put:
        assert run(orch, group[1])["passed"]
    put.assert_not_called()


# -- a failing context-server control call is a harness error, not an SDK FAIL --


@pytest.mark.parametrize("method, path_part, nth", [
    ("PUT", "/payload/", 1),    # create: storing the scenario payload
    ("PUT", "/payload/", 2),    # refresh: storing the new data
    ("GET", "/requests/", 1),   # reading the recorder after the create fetch
])
def test_failing_control_call_is_a_harness_error_not_an_sdk_failure(
        server_url, wrapper_factory, group, tmp_path, method, path_part, nth):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    real_call = orch._context_server_call
    seen = []

    def call(m, path, **kwargs):
        if m == method and path_part in path:
            seen.append(path)
            if len(seen) == nth:
                raise test_runner.requests.ConnectionError("context server reset")
        return real_call(m, path, **kwargs)

    with mock.patch.object(orch, "_context_server_call", side_effect=call), mock.patch("builtins.print"):
        orch.run_scenario(group[0])
    result = orch.results[0]["sdks"]["fake"]
    assert "context server reset" in result["harness_error"]
    assert not any("Request failed" in str(f.get("error")) for f in result["failures"])
    assert orch.scenario_outcomes[group[0]["name"]]["fake"] == "harness_error"
    assert wrapper.contexts == {}  # the scenario still cleans up

    report_path = tmp_path / "report.json"
    with mock.patch("builtins.print"):
        assert orch.generate_report(str(report_path), []) == 1  # still fails the run
    stats = json.loads(report_path.read_text())["sdk_stats"]["fake"]
    assert stats["harness_errors"] == 1 and stats["failed"] == 0 and stats["errors"] == 0
    row = results_aggregator.load_cross_sdk_report(str(report_path))["fake"]
    assert row["forced_status"] == "HARNESS ERROR" and row["failed"] == 0 and row["harness_errors"] == 1
    with mock.patch("builtins.print"):
        assert results_aggregator.print_results_table({}, {"fake": row}, ["fake"], False, True) is True


def test_wire_failure_carried_by_a_harness_error_is_an_sdk_failure(server_url, wrapper_factory, group):
    """The scenario handler merges SDK failures carried by a HarnessError: still an SDK FAIL."""
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    wire = {"step": 0, "action": "createContext", "field": "contextFetch.acceptEncoding[gzip]",
            "expected": "advertises gzip", "actual": None}

    def faulty_check(*args, **kwargs):
        raise test_runner.HarnessError("context server served 'identity'", [wire])

    with mock.patch.object(orch, "check_context_fetches", side_effect=faulty_check), mock.patch("builtins.print"):
        orch.run_scenario(group[2])
    result = orch.results[0]["sdks"]["fake"]
    assert "served 'identity'" in result["harness_error"] and wire in result["failures"]
    assert orch.scenario_outcomes[group[2]["name"]]["fake"] == "fail"


def test_sdk_failure_before_a_harness_error_is_still_an_sdk_failure(server_url, wrapper_factory, group, tmp_path):
    """An SDK failure already found in the scenario is not absorbed by a later harness error."""
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    real_call = orch._context_server_call

    def call(m, path, **kwargs):
        if m == "GET" and "/requests/" in path:
            raise test_runner.requests.ConnectionError("context server reset")
        return real_call(m, path, **kwargs)

    wrong = [{"step": 0, "action": "createContext", "field": "x", "expected": 1, "actual": 2}]
    with mock.patch.object(orch, "_context_server_call", side_effect=call), \
            mock.patch.object(orch, "validate_step", return_value=wrong), mock.patch("builtins.print"):
        orch.run_scenario(group[0])
    result = orch.results[0]["sdks"]["fake"]
    assert "context server reset" in result["harness_error"] and result["failures"] == wrong
    assert orch.scenario_outcomes[group[0]["name"]]["fake"] == "fail"

    report_path = tmp_path / "report.json"
    with mock.patch("builtins.print"):
        assert orch.generate_report(str(report_path), []) == 1
    stats = json.loads(report_path.read_text())["sdk_stats"]["fake"]
    assert stats["failed"] == 1 and stats["harness_errors"] == 1 and stats["tested"] == 1
    row = results_aggregator.load_cross_sdk_report(str(report_path))["fake"]
    assert row["forced_status"] == "HARNESS ERROR" and row["failed"] == 1


# -- the context server serving the wrong encoding is the harness's fault --------
#
# The mutation: from the `from_fetch`-th GET /context on, the in-process context
# server serves `coding` whatever the negotiation chooses. Each SDK response path
# is covered: a 200 the SDK decodes (gzip SDK served identity), a 200 with
# `failed: true` (mode "none" served unrequested gzip) and an HTTP error (mode
# "none-http" served unrequested gzip).

WRONG_ENCODING_PATHS = [
    ("gzip", "identity"),   # decoded fine: only the server fault
    ("none", "gzip"),       # wrapper answers 200 with failed: true
    ("none-http", "gzip"),  # wrapper answers HTTP 500
]
FETCH_OF_STEP = {"createContext": 1, "refresh": 2}


@pytest.fixture
def serve_wrong_encoding():
    real = context_server.choose_encoding
    patcher = None

    def start(coding, from_fetch):
        nonlocal patcher
        calls = []

        def choose(header, preference, force=None):
            calls.append(header)
            return coding if len(calls) >= from_fetch else real(header, preference, force)

        patcher = mock.patch.object(context_server, "choose_encoding", side_effect=choose)
        patcher.start()

    yield start
    if patcher is not None:
        patcher.stop()


def assert_report(orch, tmp_path, failed, harness_errors=1):
    report_path = tmp_path / "report.json"
    with mock.patch("builtins.print"):
        assert orch.generate_report(str(report_path), []) == 1
    stats = json.loads(report_path.read_text())["sdk_stats"]["fake"]
    assert stats["failed"] == failed and stats["harness_errors"] == harness_errors and stats["errors"] == 0
    row = results_aggregator.load_cross_sdk_report(str(report_path))["fake"]
    assert row["forced_status"] == "HARNESS ERROR" and row["failed"] == failed


@pytest.mark.parametrize("step", ["createContext", "refresh"])
@pytest.mark.parametrize("mode, coding", WRONG_ENCODING_PATHS)
def test_wrong_encoding_is_a_harness_error_on_every_response_path(
        server_url, wrapper_factory, group, serve_wrong_encoding, tmp_path, mode, coding, step):
    """However the wrapper answers the step the server broke, it is a HARNESS ERROR, not an SDK FAIL."""
    wrapper = wrapper_factory(mode)
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    serve_wrong_encoding(coding, FETCH_OF_STEP[step])
    with mock.patch("builtins.print"):
        orch.run_scenario(group[0])
    result = orch.results[0]["sdks"]["fake"]
    step_index = [s["action"] for s in group[0]["steps"]].index(step)
    assert result.get("harness_error", "").startswith(f"step {step_index} ({step}): ")
    assert "negotiation chooses" in result["harness_error"]
    assert result["failures"] == []
    assert orch.scenario_outcomes[group[0]["name"]]["fake"] == "harness_error"
    assert wrapper.contexts == {}
    assert_report(orch, tmp_path, failed=0)


@pytest.mark.parametrize("mode, coding", WRONG_ENCODING_PATHS)
def test_sdk_failure_before_the_wrong_encoding_step_is_still_counted(
        server_url, wrapper_factory, group, serve_wrong_encoding, tmp_path, mode, coding):
    """A failure on a step before the faulty refresh stays an SDK failure, on every response path."""
    wrapper = wrapper_factory(mode)
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    serve_wrong_encoding(coding, FETCH_OF_STEP["refresh"])
    earlier = {"step": 1, "action": "treatment", "field": "x", "expected": 1, "actual": 2}
    real_validate = orch.validate_step

    def validate(step_index, *args, **kwargs):
        return [earlier] if step_index == 1 else real_validate(step_index, *args, **kwargs)

    with mock.patch.object(orch, "validate_step", side_effect=validate), mock.patch("builtins.print"):
        orch.run_scenario(group[0])
    result = orch.results[0]["sdks"]["fake"]
    assert "(refresh)" in result["harness_error"] and result["failures"] == [earlier]
    assert orch.scenario_outcomes[group[0]["name"]]["fake"] == "fail"
    assert_report(orch, tmp_path, failed=1)


@pytest.mark.parametrize("mode, coding", [("none", "gzip"), ("none-http", "gzip")])
def test_require_advertised_failure_survives_a_wrong_encoding(
        server_url, wrapper_factory, group, serve_wrong_encoding, tmp_path, mode, coding):
    """The SDK not advertising gzip is independent of what the server sent: still an SDK failure."""
    wrapper = wrapper_factory(mode)
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    serve_wrong_encoding(coding, FETCH_OF_STEP["createContext"])
    with mock.patch("builtins.print"):
        orch.run_scenario(group[2])
    result = orch.results[0]["sdks"]["fake"]
    assert "negotiation chooses" in result["harness_error"]
    assert [f["field"] for f in result["failures"]] == ["contextFetch.acceptEncoding[gzip]"]
    assert orch.scenario_outcomes[group[2]["name"]]["fake"] == "fail"
    assert_report(orch, tmp_path, failed=1)


@pytest.mark.parametrize("step", ["createContext", "refresh"])
def test_unreadable_recorder_on_an_http_error_keeps_the_response_diagnostics(
        server_url, wrapper_factory, group, serve_wrong_encoding, tmp_path, step):
    """HTTP-error path, recorder unreadable (served_fault=False): nothing says the server was wrong,
    so the wrapper's error response is still validated and its detail kept next to the harness error."""
    wrapper = wrapper_factory("none-http")
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    serve_wrong_encoding("gzip", FETCH_OF_STEP[step])  # the wrapper answers this step with HTTP 500
    real_call = orch._context_server_call
    reads = []

    def call(m, path, **kwargs):
        if m == "GET" and "/requests/" in path:
            reads.append(path)
            if len(reads) == FETCH_OF_STEP[step]:
                raise test_runner.requests.ConnectionError("context server reset")
        return real_call(m, path, **kwargs)

    with mock.patch.object(orch, "_context_server_call", side_effect=call), mock.patch("builtins.print"):
        orch.run_scenario(group[0])
    result = orch.results[0]["sdks"]["fake"]
    step_index = [s["action"] for s in group[0]["steps"]].index(step)
    assert result["harness_error"].startswith(f"step {step_index} ({step}): ")
    assert "context server reset" in result["harness_error"]
    errors = [f for f in result["failures"] if f["step"] == step_index]
    assert any("cannot decode gzip" in str(f.get("error")) for f in errors), result["failures"]
    # ... and then the harness error stops the scenario: no later step ran.
    assert all(f["step"] <= step_index for f in result["failures"]), result["failures"]
    assert orch.scenario_outcomes[group[0]["name"]]["fake"] == "fail"
    assert wrapper.contexts == {}


@pytest.mark.parametrize("mutate, reason", [
    (lambda s: s.update(directData=False), "'directData' must be true"),
    (lambda s: s.update(directData=True, contextServer={}), "cannot be combined"),
    (lambda s: s.update(contextServer={"encodings": ["zstd"]}), "contextServer:"),
])
def test_invalid_direct_data_and_context_server_are_rejected(group, mutate, reason):
    scenario = copy.deepcopy(group[1])
    mutate(scenario)
    _, _, invalid = test_runner.validate_and_filter_scenarios([scenario], {CAP})
    assert len(invalid) == 1 and reason in invalid[0]["reason"]


def test_create_with_scenario_cannot_use_the_context_server(group):
    """CREATE_WITH_SCENARIO bypasses the fetch path, so a contextServer on it is rejected by name."""
    scenario = copy.deepcopy(group[0])
    scenario["name"] = test_runner.CREATE_WITH_SCENARIO
    valid, _, invalid = test_runner.validate_and_filter_scenarios([scenario], {CAP})
    assert valid == [] and len(invalid) == 1
    assert invalid[0]["name"] == test_runner.CREATE_WITH_SCENARIO
    assert "contextServer cannot be used on" in invalid[0]["reason"]
    assert test_runner.CREATE_WITH_SCENARIO in invalid[0]["reason"]
    # The same scenario without contextServer stays valid.
    del scenario["contextServer"]
    valid, _, invalid = test_runner.validate_and_filter_scenarios([scenario], {CAP})
    assert len(valid) == 1 and invalid == []
