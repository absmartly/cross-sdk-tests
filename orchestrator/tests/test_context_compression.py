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
    kept_max = max(generator.scenario_number(s) for s in scenarios if s not in group)
    assert kept_max == 229
    assert scenarios[-3:] == group == generator.build_group(230)
    assert [generator.scenario_number(s) for s in group] == [230, 231, 232]


def test_only_the_wire_check_is_gated():
    ungated, twin, gated = generator.build_group(230)
    assert "requires" not in ungated and "requires" not in twin
    assert gated["requires"] == [CAP]
    assert ungated["contextServer"] == {"encodings": ["gzip", "identity"]}
    assert gated["contextServer"]["requireAdvertised"] == ["gzip"]
    assert twin["directData"] is True and "contextServer" not in twin


def test_twin_shares_every_pre_refresh_expectation():
    ungated, twin, _ = generator.build_group(230)
    assert ungated["contextData"] == twin["contextData"]
    actions = [s["action"] for s in ungated["steps"]]
    before_refresh = ungated["steps"][:actions.index("refresh")]
    assert [s["expect"] for s in before_refresh] == [s["expect"] for s in twin["steps"]]
    assert [s["action"] for s in twin["steps"]] == ["createContextWith", "treatment", "variableValue", "publish"]


def test_refresh_data_differs_and_the_next_step_proves_it_was_used():
    for scenario in (generator.build_group(230)[0], generator.build_group(230)[2]):
        steps = scenario["steps"]
        i = [s["action"] for s in steps].index("refresh")
        new_data = steps[i]["params"]["newData"]
        assert new_data != scenario["contextData"]
        before = next(s for s in steps[:i] if s["action"] == "treatment")
        after = steps[i + 1]
        assert after["action"] == "treatment"
        # Keeping the old data would repeat the cached result with no event.
        assert after["expect"]["result"] != before["expect"]["result"]
        assert after["expect"]["events"], "a changed assignment must queue a fresh exposure"


def test_refresh_expectation_matches_the_pinned_traffic_split_scenario():
    """The refreshed data is scenario 29's; so is the treatment after its refresh."""
    by_number = {generator.scenario_number(s): s for s in canonical()}
    pinned = by_number[29]["steps"]
    ungated = generator.build_group(230)[0]["steps"]
    refresh = [s["action"] for s in ungated].index("refresh")
    [ours] = ungated[refresh]["params"]["newData"]["experiments"]
    [theirs] = pinned[2]["params"]["newData"]["experiments"]
    # Same assignment inputs; only the variant configs (not read after refresh) differ.
    assert {k: v for k, v in ours.items() if k != "variants"} == {k: v for k, v in theirs.items() if k != "variants"}
    assert ungated[refresh + 1]["expect"] == pinned[3]["expect"]


def test_payload_is_only_the_tested_experiment():
    assert [e["name"] for e in generator.context_data()["experiments"]] == ["exp_test_ab"]


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


def test_required_where_observed_or_read_from_code():
    """Required where a lane observed gzip or the code shows it (CI proves it); the rest are exceptions."""
    policy = test_runner.load_capability_policy()
    assert policy["measuredCapabilities"] == [CAP]
    required = sorted(sdk for sdk, e in policy["sdks"].items() if CAP in e["required"])
    assert required == ["angular", "dart", "flutter", "javascript", "liquid", "python", "react", "ruby",
                        "scala", "vue2", "vue3"]
    excepted = {sdk: e["unsupported"][CAP] for sdk, e in policy["sdks"].items() if CAP in e["unsupported"]}
    assert sorted(excepted) == ["cpp", "dotnet", "elixir", "go", "java", "kotlin", "php", "rust", "swift"]
    assert "fails on main CI" in excepted["swift"]


def test_measured_capability_needs_a_probe():
    policy = test_runner.load_capability_policy()
    policy["measuredCapabilities"] = ["holdouts"]
    assert any("no orchestrator probe" in e for e in test_runner.validate_capability_policy(policy))


@pytest.mark.parametrize("measured, error", [
    ("contextFetchCompression", "must be a list"),
    ([1], "must be a list"),
    (["notDeclared"], "is not declared"),
])
def test_malformed_measured_capabilities_are_rejected(measured, error):
    policy = test_runner.load_capability_policy()
    policy["measuredCapabilities"] = measured
    assert any(error in e for e in test_runner.validate_capability_policy(policy))


# -- orchestrator: measured capability --------------------------------------


def small_policy(required, other=False):
    caps = {CAP: "measured"}
    if other:
        caps["holdouts"] = "advertised"
    entry = {"required": [CAP] if required else [], "unsupported": {} if required else {CAP: "fix pending"}}
    if other:
        entry["required"].append("holdouts")
    return {"capabilities": caps, "measuredCapabilities": [CAP], "sdks": {"alpha": entry}}


def probe_outcome(headers, decoded=True, refresh_decoded=True, refresh=200):
    return {
        "ready": True, "failed": False, "decoded": decoded, "refreshDecoded": refresh_decoded,
        "refreshStatus": refresh,
        "requests": [{"status": 200, "acceptEncoding": h, "userAgent": "ua", "served": "identity"} for h in headers],
    }


def measure(required, negotiated, wrapper_says=None, other=False, wrapper_caps=None):
    orch = TestOrchestrator({"alpha": "http://alpha"}, capability_policy=small_policy(required, other))
    orch._context_server_ready = ""  # the context server answered /health
    body = dict(wrapper_caps or {})
    if wrapper_says is not None:
        body[CAP] = wrapper_says
    response = mock.Mock(status_code=200)
    response.json.return_value = body
    unrequested = {"ready": True, "failed": True, "requests": [{"acceptEncoding": None}]}
    side_effect = negotiated if isinstance(negotiated, Exception) else [negotiated, unrequested]
    with mock.patch.object(test_runner.requests, "get", return_value=response), \
            mock.patch.object(orch, "_probe_context_fetch", side_effect=side_effect), \
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
    probe_outcome(["gzip", "gzip"], refresh_decoded=False),  # refetched but kept the old data
    probe_outcome(["*", "*"]),               # a wildcard is not an explicit gzip
    probe_outcome(["gzip;q=0", "gzip;q=0"]),  # refused, not advertised
])
def test_gzip_must_be_advertised_on_create_and_refresh_and_decode(outcome):
    assert measure(True, outcome).capabilities["alpha"][CAP] is False


@pytest.mark.parametrize("negotiated, reason", [
    ({"error": "boom", "requests": []}, "made no GET /context"),
    (test_runner.requests.ConnectionError("down"), "could not use the context server"),
    (KeyError("anything"), "anything"),
])
def test_failed_measurement_fails_the_run_but_gates_only_its_scenarios(negotiated, reason):
    orch = measure(True, negotiated, other=True, wrapper_caps={"holdouts": True})
    assert reason in orch.measurement_failed["alpha"][CAP]
    # Not a capabilities-fetch failure: the wrapper's validated capabilities still apply.
    assert "alpha" not in orch.capabilities_fetch_failed
    assert orch.capabilities["alpha"]["holdouts"] is True and CAP not in orch.capabilities["alpha"]
    assert "measurement of contextFetchCompression failed" in orch.capability_gate("alpha", {"requires": [CAP]})
    assert orch.capability_gate("alpha", {"requires": ["holdouts"]}) is None
    # Reported once, as a measurement failure, not again as a policy violation.
    assert "alpha" not in orch.capability_policy_violations
    with mock.patch("builtins.print"):
        exit_code = orch.generate_report(os.devnull, [])
    assert exit_code != 0


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
    assert check(spec, [fetch("gzip;q=0", "identity")]) == []
    assert check(spec, [fetch("gzip", "identity")]) == ["contextFetch.served"]
    assert check(spec, [fetch(None, "gzip")]) == ["contextFetch.served"]


def test_step_without_a_fetch_fails():
    assert check({}, [fetch("gzip", "gzip")], seen=1) == ["contextFetch"]


def test_require_advertised_gzip():
    spec = {"encodings": ["gzip", "identity"], "requireAdvertised": ["gzip"]}
    assert check(spec, [fetch("gzip", "gzip")]) == []
    assert check(spec, [fetch(None, "identity")]) == ["contextFetch.acceptEncoding[gzip]"]
    assert check(spec, [fetch("gzip;q=0", "identity")]) == ["contextFetch.acceptEncoding[gzip]"]
    assert check(spec, [fetch("*", "gzip")]) == ["contextFetch.acceptEncoding[gzip]"]


def test_forced_encoding_is_expected_regardless_of_request():
    assert check({"force": "gzip"}, [fetch(None, "gzip")]) == []


@pytest.mark.parametrize("spec", [[], {"encodings": ["deflate"]}, {"encodings": ["br"]}, {"encodings": []},
                                  {"force": "lz4"}, {"force": "zstd"},
                                  {"requireAdvertised": ["identity"]}, {"extra": 1}])
def test_bad_context_server_specs_are_rejected(spec):
    assert test_runner.validate_context_server_spec(spec)


def test_orchestrator_and_server_share_one_negotiation_module():
    sys.path.insert(0, os.path.join(ROOT, "context-server"))
    import server as context_server
    assert context_server.choose_encoding is test_runner.negotiation.choose_encoding
