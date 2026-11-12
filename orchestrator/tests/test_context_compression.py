"""Context Fetch Compression: generator guards and the orchestrator's measured capability."""

import copy
import json
import os
import sys
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "orchestrator"))

import generate_context_compression_scenarios as generator  # noqa: E402
import test_runner  # noqa: E402
from test_runner import TestOrchestrator  # noqa: E402

CAP = "contextFetchCompression"


def canonical():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        return json.load(src)


# -- generator ---------------------------------------------------------------


def test_retained_prefix_is_the_exact_30d23e9_baseline():
    kept = [s for s in canonical() if generator.GROUP_MARKER not in s["name"]]
    assert len(kept) == generator.BASELINE_COUNT == 222
    assert generator.baseline_digest(kept) == generator.BASELINE_SHA256


def test_group_is_a_contiguous_tail_numbered_after_the_max_retained_id():
    scenarios = canonical()
    group = [s for s in scenarios if generator.GROUP_MARKER in s["name"]]
    kept_max = max(int(s["name"].split(" - ", 1)[0]) for s in scenarios if s not in group)
    assert kept_max == 229
    assert scenarios[-3:] == group == generator.build_group(230)
    assert [int(s["name"].split(" - ", 1)[0]) for s in group] == [230, 231, 232]


def test_only_the_wire_check_is_gated():
    ungated, twin, gated = generator.build_group(230)
    assert "requires" not in ungated and "requires" not in twin
    assert gated["requires"] == [CAP]
    assert ungated["contextServer"] == {"encodings": ["gzip", "identity"]}
    assert gated["contextServer"]["requireAdvertised"] == ["gzip"]
    assert twin["directData"] is True and "contextServer" not in twin


def test_twin_shares_every_non_refresh_expectation():
    ungated, twin, _ = generator.build_group(230)
    assert ungated["contextData"] == twin["contextData"]
    fetched = [s for s in ungated["steps"] if s["action"] not in ("refresh",)]
    fetched = [fetched[0], fetched[1], fetched[2], fetched[-1]]
    assert [s["expect"] for s in fetched] == [s["expect"] for s in twin["steps"]]
    assert [s["action"] for s in twin["steps"]] == ["createContextWith", "treatment", "variableValue", "publish"]


def test_refresh_goes_through_the_sdk_fetch_of_the_same_payload():
    for scenario in (generator.build_group(230)[0], generator.build_group(230)[2]):
        refresh = [s for s in scenario["steps"] if s["action"] == "refresh"]
        assert len(refresh) == 1
        assert refresh[0]["params"]["newData"] == scenario["contextData"]


def test_payload_is_large_enough_to_compress():
    raw = json.dumps(generator.context_data()).encode()
    assert len(raw) > 10_000


def test_scenarios_validate_with_the_checked_in_policy():
    policy = test_runner.load_capability_policy()
    valid, empty, invalid = test_runner.validate_and_filter_scenarios(canonical(), set(policy["capabilities"]))
    assert invalid == [] and empty == []


@pytest.mark.parametrize("mutation", [None, "expectation", "missing", "prefix", "spec"])
def test_check_detects_drift(tmp_path, monkeypatch, mutation):
    scenarios = copy.deepcopy(canonical())
    if mutation == "expectation":
        scenarios[-3]["steps"][1]["expect"]["result"] = 0
    elif mutation == "missing":
        scenarios.pop()
    elif mutation == "prefix":
        scenarios[0]["description"] += " drift"
    elif mutation == "spec":
        scenarios[-1]["contextServer"]["requireAdvertised"] = []
    path = tmp_path / "scenarios.json"
    path.write_text(generator.render(scenarios), encoding="utf-8")
    monkeypatch.setattr(generator, "CANONICAL", str(path))
    monkeypatch.setattr(sys, "argv", ["generate_context_compression_scenarios.py", "--check"])
    assert generator.main() == (0 if mutation is None else 1)


def test_place_group_appends_after_max_id_with_gaps():
    existing = [{"name": "3 - A - x"}, {"name": "18 - B - y"}]
    updated, group = generator.place_group(existing)
    assert [s["name"].split(" - ")[0] for s in group] == ["19", "20", "21"]
    assert updated[:2] == existing


def test_place_group_rejects_a_split_group():
    names = ["1 - A - x", f"2{generator.GROUP_MARKER}a", "3 - B - y", f"4{generator.GROUP_MARKER}b"]
    with pytest.raises(ValueError, match="contiguous"):
        generator.place_group([{"name": n} for n in names])


# -- policy ------------------------------------------------------------------


def test_policy_declares_the_measured_capability_for_every_sdk():
    policy = test_runner.load_capability_policy()
    assert policy["measuredCapabilities"] == [CAP]
    for sdk, entry in policy["sdks"].items():
        assert (CAP in entry["required"]) != (CAP in entry["unsupported"]), sdk


def test_required_only_where_a_lane_observed_gzip():
    policy = test_runner.load_capability_policy()
    required = sorted(sdk for sdk, e in policy["sdks"].items() if CAP in e["required"])
    assert required == ["angular", "javascript", "liquid", "python", "ruby", "vue2", "vue3"]


def test_measured_capability_needs_a_probe():
    policy = test_runner.load_capability_policy()
    policy["measuredCapabilities"] = ["holdouts"]
    assert any("no orchestrator probe" in e for e in test_runner.validate_capability_policy(policy))


# -- orchestrator: measured capability --------------------------------------


def small_policy(required):
    return {
        "capabilities": {CAP: "measured"},
        "measuredCapabilities": [CAP],
        "sdks": {"alpha": {"required": [CAP], "unsupported": {}} if required
                 else {"required": [], "unsupported": {CAP: "sends no Accept-Encoding; fix pending"}}},
    }


def probe_outcome(headers, decoded=True, refresh=200):
    return {
        "ready": True, "failed": False, "decoded": decoded, "refreshStatus": refresh,
        "requests": [{"status": 200, "acceptEncoding": h, "userAgent": "ua", "served": "identity"} for h in headers],
    }


def measure(required, negotiated, wrapper_says=None):
    orch = TestOrchestrator({"alpha": "http://alpha"}, capability_policy=small_policy(required))
    body = {} if wrapper_says is None else {CAP: wrapper_says}
    response = mock.Mock(status_code=200)
    response.json.return_value = body
    unrequested = {"ready": True, "failed": True, "requests": [{"acceptEncoding": None}]}
    with mock.patch.object(test_runner.requests, "get", return_value=response), \
            mock.patch.object(orch, "_probe_context_fetch", side_effect=[negotiated, unrequested]), \
            mock.patch("builtins.print"):
        orch.fetch_capabilities("alpha", "http://alpha")
    return orch


def test_excepted_sdk_that_sends_nothing_is_skipped_without_violation():
    orch = measure(False, probe_outcome([None, None]))
    assert orch.capabilities["alpha"][CAP] is False
    assert "alpha" not in orch.capability_policy_violations
    assert "reviewed exception" in orch.capability_gate("alpha", {"requires": [CAP]})


def test_excepted_sdk_that_starts_sending_gzip_fails_until_policy_moves():
    orch = measure(False, probe_outcome(["gzip, deflate", "gzip, deflate"]))
    assert orch.capabilities["alpha"][CAP] is True
    assert "move it to 'required'" in orch.capability_policy_violations["alpha"][0]
    assert orch.capability_gate("alpha", {"requires": [CAP]}) is None


def test_required_sdk_that_stops_sending_gzip_is_a_violation():
    orch = measure(True, probe_outcome(["gzip", None]))
    assert orch.capabilities["alpha"][CAP] is False
    assert "is not advertised" in orch.capability_policy_violations["alpha"][0]


def test_wrapper_cannot_self_report_the_measured_capability():
    orch = measure(True, probe_outcome([None, None]), wrapper_says=True)
    assert orch.capabilities["alpha"][CAP] is False
    assert orch.capability_policy_violations["alpha"]


@pytest.mark.parametrize("outcome", [
    probe_outcome(["gzip"]),                 # refresh did not fetch
    probe_outcome(["gzip", "gzip"], refresh=500),
    probe_outcome(["gzip", "gzip"], decoded=False),
    probe_outcome(["*", "*"]),               # a wildcard is not an explicit gzip
])
def test_gzip_must_be_advertised_on_create_and_refresh_and_decode(outcome):
    assert measure(True, outcome).capabilities["alpha"][CAP] is False


def test_probe_without_any_fetch_fails_closed():
    orch = measure(False, {"error": "boom", "requests": []})
    assert "made no GET /context" in orch.capabilities_fetch_failed["alpha"]


def test_unreachable_context_server_fails_closed():
    orch = TestOrchestrator({"alpha": "http://alpha"}, capability_policy=small_policy(False))
    response = mock.Mock(status_code=200)
    response.json.return_value = {}
    with mock.patch.object(test_runner.requests, "get", return_value=response), \
            mock.patch.object(orch, "_probe_context_fetch", side_effect=test_runner.requests.ConnectionError("down")), \
            mock.patch("builtins.print"):
        orch.fetch_capabilities("alpha", "http://alpha")
    assert "could not use the context server" in orch.capabilities_fetch_failed["alpha"]


# -- orchestrator: per-step wire checks -------------------------------------


def fetch(header, served, status=200):
    return {"acceptEncoding": header, "served": served, "status": status}


def check(spec, records, seen=0):
    orch = TestOrchestrator({}, capability_policy=small_policy(False))
    return [f["field"] for f in orch.check_context_fetches(0, "createContext", spec, records, seen)]


def test_served_encoding_must_follow_the_request():
    spec = {"encodings": ["gzip", "identity"]}
    assert check(spec, [fetch("gzip, deflate", "gzip")]) == []
    assert check(spec, [fetch(None, "identity")]) == []
    assert check(spec, [fetch("gzip", "identity")]) == ["contextFetch.served"]
    assert check(spec, [fetch(None, "gzip")]) == ["contextFetch.served"]


def test_step_without_a_fetch_fails():
    assert check({}, [fetch("gzip", "gzip")], seen=1) == ["contextFetch"]


def test_require_advertised_gzip():
    spec = {"encodings": ["gzip", "identity"], "requireAdvertised": ["gzip"]}
    assert check(spec, [fetch("gzip", "gzip")]) == []
    assert check(spec, [fetch(None, "identity")]) == ["contextFetch.acceptEncoding[gzip]"]


def test_forced_encoding_is_expected_regardless_of_request():
    assert check({"force": "gzip"}, [fetch(None, "gzip")]) == []


@pytest.mark.parametrize("spec", [[], {"encodings": ["deflate"]}, {"encodings": []}, {"force": "lz4"},
                                  {"requireAdvertised": ["identity"]}, {"extra": 1}])
def test_bad_context_server_specs_are_rejected(spec):
    assert test_runner.validate_context_server_spec(spec)


def test_header_parsing_matches_the_server():
    sys.path.insert(0, os.path.join(ROOT, "context-server"))
    import negotiation
    import server as context_server
    for header in [None, "", "gzip", "gzip;q=0", "*", "x-gzip", "br, zstd", "gzip;q=1.0,deflate;q=0.6,identity;q=0.3",
                   "identity;q=0", "*;q=0"]:
        for pref in (["gzip", "identity"], ["zstd", "br", "gzip", "identity"], ["identity"]):
            pref = [c for c in pref if c in context_server.COMPRESSORS]  # br/zstd depend on the interpreter
            assert test_runner.expected_served_encoding(header, pref) == context_server.choose_encoding(header, pref)
        assert test_runner.advertises_encoding(header, "gzip") == negotiation.advertises(header, "gzip")
