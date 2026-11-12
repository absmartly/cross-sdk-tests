"""Docker-free end-to-end tests of the context-fetch wiring.

A real context server (context-server/server.py, in process) and a fake
wrapper (fake_wrapper.py) whose "SDK" fetches from it. Covers the probe
(`_probe_context_fetch` / `measure_context_fetch_compression`), scenarios
230-232 as generated, the empty-body refresh of contextServer scenarios and
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
        outcome = orch._probe_context_fetch(wrapper.url, generator.context_data(), None, None, 5)
    assert "non-object body" in outcome["error"]
    assert outcome["requests"] == []


# -- scenarios 230-232 as generated --------------------------------------------


@pytest.fixture(scope="module")
def group():
    return generator.build_group(230)


def test_conforming_sdk_passes_the_group(server_url, wrapper_factory, group):
    wrapper = wrapper_factory("gzip")
    orch = orchestrator_for(wrapper)
    orch.capabilities["fake"] = {CAP: True}
    for scenario in group:
        result = run(orch, scenario)
        assert result["passed"], (scenario["name"], result["failures"])
    # Refresh in a contextServer scenario sends the wrapper no data.
    assert wrapper.refresh_bodies == [{}, {}]
    fetches = orch.context_fetches[group[0]["name"]]["fake"]
    assert [f["action"] for f in fetches] == ["createContext", "refresh"]
    assert [r["served"] for f in fetches for r in f["requests"]] == ["gzip", "gzip"]


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


@pytest.mark.parametrize("mutate, reason", [
    (lambda s: s.update(directData=False), "'directData' must be true"),
    (lambda s: s.update(directData=True, contextServer={}), "cannot be combined"),
    (lambda s: s.update(contextServer={"encodings": ["br"]}), "contextServer:"),
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
