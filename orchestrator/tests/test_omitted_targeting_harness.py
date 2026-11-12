"""Reference and wire-contract guards for omitted empty targeting."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import generate_omitted_targeting_scenarios as generator
from orchestrator.test_runner import TestOrchestrator as Orchestrator


def test_generated_group_and_policy():
    scenarios = json.loads(Path(generator.CANONICAL).read_text())
    group = generator.build_group()
    assert scenarios[-7:] == group
    assert [int(s["name"].split(" - ")[0]) for s in group] == list(range(280, 287))
    assert sum("requires" not in s for s in group) == 4
    policy = json.loads((Path(generator.CANONICAL).parent / "orchestrator/expected-capabilities.json").read_text())
    for entry in policy["sdks"].values():
        assert generator.CAPABILITY not in entry["required"]
        assert entry["unsupported"][generator.CAPABILITY] == "pending SDK cache fix"


def test_fresh_forms_are_truly_absent_or_the_exact_requested_strings():
    for scenario in generator.build_group():
        if "requires" in scenario:
            continue
        key, values = ("audience", generator.EMPTY_AUDIENCES) if "Fresh Audience" in scenario["name"] else ("assignmentRules", generator.EMPTY_RULES)
        experiments = scenario["contextData"]["experiments"]
        assert generator.verdict(experiments[0], "CA")[0] != 0
        assert key not in experiments[0]
        assert [e[key] for e in experiments[1:]] == list(values[1:])
        for field in ("seedHi", "seedLo", "split", "unitType", "trafficSplit"):
            assert all(e[field] == experiments[0][field] for e in experiments)


def test_refresh_reference_clears_mismatch_with_same_identity_and_one_exposure_each():
    for scenario in generator.build_group():
        if "requires" not in scenario:
            continue
        before = scenario["contextData"]["experiments"]
        refresh = next(i for i, st in enumerate(scenario["steps"]) if st["action"] == "refresh")
        after = scenario["steps"][refresh]["params"]["newData"]["experiments"]
        assert [(e["id"], e["iteration"]) for e in before] == [(e["id"], e["iteration"]) for e in after]
        events = [event for st in scenario["steps"][refresh + 1:] for event in st["expect"]["events"] if event["type"] == "exposure"]
        assert len(events) == 3
        assert all(e["data"]["assigned"] and not e["data"]["audienceMismatch"] for e in events)
        attributes = [st for st in scenario["steps"][refresh + 1:] if st["action"] == "attribute"]
        assert len(attributes) == (0 if "Rules Signal" in scenario["name"] else 1)


@pytest.mark.parametrize("event_type", ["exposure", "publish"])
def test_extra_exposure_field_fails_shape_matcher(event_type):
    runner = Orchestrator({})
    prior = []
    checked = False
    for index, st in enumerate(generator.build_group()[0]["steps"]):
        actual = copy.deepcopy(st["expect"])
        for event in actual["events"]:
            if event["type"] != event_type:
                continue
            if "sameShapeAs" in event or "sameExposureShapeAs" in event:
                assert not runner.validate_events(index, st["action"], actual["events"], st["expect"], prior_responses=prior)
                body = event["data"] if event_type == "exposure" else event["data"]["exposures"][0]
                body["regression"] = True
                assert runner.validate_events(index, st["action"], actual["events"], st["expect"], prior_responses=prior)
                checked = True
        prior.append(copy.deepcopy(st["expect"]))
    assert checked


@pytest.mark.parametrize("mutation", ["expectation", "missing", "absent-to-null"])
def test_check_detects_group_drift(mutation, tmp_path, monkeypatch):
    scenarios = json.loads(Path(generator.CANONICAL).read_text())
    if mutation == "expectation":
        scenarios[-7]["steps"][2]["expect"]["result"] = 99
    elif mutation == "missing":
        scenarios.pop()
    else:
        scenarios[-7]["contextData"]["experiments"][0]["audience"] = None
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n")
    monkeypatch.setattr(generator, "CANONICAL", str(path))
    monkeypatch.setattr(sys, "argv", [generator.__file__, "--check"])
    assert generator.main() == 1
