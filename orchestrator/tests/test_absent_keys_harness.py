"""Guards for absent (not null) wire keys and the generated contiguous tail."""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import generate_absent_keys_scenarios as generator
from orchestrator.test_runner import TestOrchestrator as Orchestrator


def canonical():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        return json.load(src)


def test_retained_prefix_is_the_exact_starting_baseline():
    kept = [s for s in canonical() if generator.GROUP_MARKER not in s["name"]
            and " - Omitted Empty Targeting - " not in s["name"]]
    assert len(kept) == generator.BASELINE_COUNT == 264
    assert generator.baseline_digest(kept) == generator.BASELINE_SHA256


def test_group_is_an_ungated_contiguous_tail_after_max_retained_id():
    scenarios = canonical()
    group = [s for s in scenarios if generator.GROUP_MARKER in s["name"]]
    assert len(scenarios) == 279
    assert scenarios[264:272] == group == generator.build_group(272)
    assert [int(s["name"].split(" - ", 1)[0]) for s in group] == list(range(272, 280))
    assert all("requires" not in s and s["steps"] for s in group)


def test_twins_really_omit_keys_and_keep_required_fields_and_assignment_inputs():
    for scenario in generator.build_group():
        absent, empty = scenario["contextData"]["experiments"][:2]
        omitted = set(empty) - set(absent)
        if "Refresh" in scenario["name"]:
            assert not omitted
        else:
            assert omitted
            for key in omitted:
                assert empty[key] in ([], "")
        for key in ("iteration", "unitType", "fullOnVariant", "audienceStrict", "seedHi", "seedLo",
                    "split", "trafficSeedHi", "trafficSeedLo", "trafficSplit"):
            assert absent[key] == empty[key]
        for exp in (absent, empty):
            assert all("config" in variant for variant in exp["variants"])
            assert not any(key.startswith("switchback") for key in exp)
        for action in ("peek", "treatment", "peekVariableValue", "variableValue", "customFieldKeys",
                       "customFieldValue", "customFieldValueType", "publish"):
            assert any(st["action"] == action for st in scenario["steps"])
        treatments = [st for st in scenario["steps"] if st["action"] == "treatment"]
        assert len({st["expect"]["result"] for st in treatments}) == 1
        publish = scenario["steps"][-1]["expect"]["events"][0]["data"]
        assert len(publish["exposures"]) == len(scenario["contextData"]["experiments"])
        twin_bodies = [dict(e) for e in publish["exposures"][:2]]
        for body in twin_bodies:
            del body["id"], body["name"]
        assert twin_bodies[0] == twin_bodies[1]


def test_refresh_sequence_removes_the_key_and_does_not_reexpose():
    scenario = next(s for s in generator.build_group() if "Fields Refresh" in s["name"])
    payloads = [scenario["contextData"]] + [st["params"]["newData"] for st in scenario["steps"]
                                            if st["action"] == "refresh"]
    assert payloads[0]["experiments"][0]["customFieldValues"] == []
    assert "customFieldValues" not in payloads[1]["experiments"][0]
    assert payloads[2]["experiments"][0]["customFieldValues"] == generator.FIELDS
    assert "customFieldValues" not in payloads[3]["experiments"][0]
    assert all(p["experiments"][1]["customFieldValues"] == [] for p in payloads)
    exposed = [e for st in scenario["steps"] if st["action"] == "treatment"
               for e in st["expect"]["events"]]
    assert len(exposed) == 2
    assert [st["expect"]["result"] for st in scenario["steps"] if st["action"] == "customFieldKeys"] == [
        [], [], [f["name"] for f in generator.FIELDS], []]


def test_holdouts_are_absent_on_the_refreshed_wire_payload():
    scenario = next(s for s in generator.build_group() if s["name"].endswith(" - Holdouts"))
    assert scenario["contextData"]["holdouts"] == []
    refreshed = next(st["params"]["newData"] for st in scenario["steps"] if st["action"] == "refresh")
    assert "holdouts" not in refreshed
    assert "holdoutIds" not in refreshed["experiments"][0]
    assert refreshed["experiments"][1]["holdoutIds"] == []


def test_audience_is_strict_and_assignment_rules_are_a_separate_case():
    audience, rules = generator.build_group()[-2:]
    assert audience["name"].startswith("278 - ")
    assert rules["name"].startswith("279 - ")
    for scenario, omitted in ((audience, "audience"), (rules, "assignmentRules")):
        absent, empty = scenario["contextData"]["experiments"]
        assert set(empty) - set(absent) == {omitted}
        assert empty[omitted] == ""
    assert all(e["audienceStrict"] is True for e in audience["contextData"]["experiments"])
    for exposure in audience["steps"][-1]["expect"]["events"][0]["data"]["exposures"]:
        assert exposure["assigned"] is True
        assert exposure["eligible"] is True
        assert exposure["audienceMismatch"] is False


@pytest.mark.parametrize("event_type", ["exposure", "publish"])
def test_absent_only_extra_exposure_key_fails_validate_events(event_type):
    runner = Orchestrator({}, verbose=False)
    scenario = generator.build_group()[0]
    prior = []
    checked = False
    for index, step in enumerate(scenario["steps"]):
        response = copy.deepcopy(step["expect"])
        for event in response["events"]:
            if event["type"] != event_type:
                continue
            exposures = [event["data"]] if event_type == "exposure" else event["data"]["exposures"]
            for exposure in exposures:
                # Dynamic clock fields must not make otherwise valid events fail.
                exposure["exposedAt"] = 123
            assert runner.validate_events(index, step["action"], response["events"], step["expect"], prior_responses=prior) == []
            absent = next((e for e in exposures if e["name"] == "exp_optional_absent"), None)
            if absent is not None:
                absent["absentOnlyRegression"] = True
                failures = runner.validate_events(index, step["action"], response["events"], step["expect"], prior_responses=prior)
                assert failures
                assert any("keys" in failure["field"] for failure in failures)
                checked = True
        # Baselines include the same dynamic clock field as real SDK events.
        for event in response["events"]:
            if event["type"] == "exposure":
                event["data"]["exposedAt"] = 123
        prior.append(response)
    assert checked


@pytest.mark.parametrize("mutation", [None, "expectation", "missing", "null", "prefix"])
def test_check_detects_drift(tmp_path, monkeypatch, mutation):
    scenarios = copy.deepcopy(canonical())
    target = next(s for s in scenarios if generator.GROUP_MARKER in s["name"])
    if mutation == "expectation":
        target["steps"][1]["expect"]["result"] = None
    elif mutation == "missing":
        scenarios.remove(next(s for s in scenarios if s["name"].startswith("279 - ")))
    elif mutation == "null":
        target["contextData"]["experiments"][0]["customFieldValues"] = None
    elif mutation == "prefix":
        scenarios[0]["description"] += " drift"
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(generator, "CANONICAL", str(path))
    monkeypatch.setattr(sys, "argv", ["generate_absent_keys_scenarios.py", "--check"])
    assert generator.main() == (0 if mutation is None else 1)
