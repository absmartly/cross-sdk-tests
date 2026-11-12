"""Unit tests for the switchback harness: sameShapeAs reference resolution and shape matching,
capability gating, and generate_switchback_scenarios.py --check.

Run from the repository root:  python3 -m pytest orchestrator/tests -q
"""

import copy
import functools
import hashlib
import json
import os
import sys

import pytest
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "orchestrator"))
sys.path.insert(0, REPO)

import generate_switchback_scenarios as generator  # noqa: E402
from test_runner import SWITCHBACK_ACTIONS  # noqa: E402
from test_runner import TestOrchestrator as Orchestrator  # noqa: E402


@pytest.fixture
def runner():
    return Orchestrator({}, verbose=False)


@functools.lru_cache(maxsize=None)
def _canonical_text():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        return src.read()


@functools.lru_cache(maxsize=None)
def _canonical_parsed():
    return json.loads(_canonical_text())


def canonical():
    """The checked-in scenario file, parsed once per test session; each caller gets its own copy."""
    return copy.deepcopy(_canonical_parsed())


@pytest.fixture(scope="module")
def built_group():
    """One full generation (golden verification, builders, mutant replay) for the module."""
    return generator.build_group(generator.load_fixtures(), 248)


def exposure(**extra):
    data = {"id": 1, "name": "exp", "unit": "store_id", "variant": 1, "exposedAt": 5, "assigned": True}
    data.update(extra)
    return data


def publish(exposures, **extra):
    data = {"hashed": True, "units": [{"type": "store_id", "uid": "abc"}], "exposures": exposures}
    data.update(extra)
    return data


def response(event_type, data, include_data=True):
    event = {"type": event_type}
    if include_data:
        event["data"] = data
    return {"result": None, "events": [event]}


def validate(runner, prior, actual_data, reference, step_index=None, event_type="publish"):
    """Validates a step whose single event must have the shape of `reference`'s event."""
    step_index = len(prior) if step_index is None else step_index
    expected = {"events": [{"type": event_type, "sameShapeAs": reference}]}
    return runner.validate_step(step_index, "publish", response(event_type, actual_data), expected, prior)


# ---------------------------------------------------------------------------
# shape_mismatch
# ---------------------------------------------------------------------------


def test_identical_nested_shapes_match_regardless_of_values(runner):
    baseline = publish([exposure()])
    actual = publish([exposure(id=2, variant=0, assigned=False)])
    assert runner.shape_mismatch(actual, baseline, "data") is None


def test_nested_added_key_is_a_mismatch(runner):
    mismatch = runner.shape_mismatch(publish([exposure(periodIndex=0)]), publish([exposure()]), "data")
    assert mismatch is not None
    assert mismatch[0] == "data.exposures[0] keys"


def test_nested_dropped_key_is_a_mismatch(runner):
    dropped = exposure()
    del dropped["exposedAt"]
    mismatch = runner.shape_mismatch(publish([dropped]), publish([exposure()]), "data")
    assert mismatch is not None
    assert mismatch[0] == "data.exposures[0] keys"


def test_top_level_added_key_is_a_mismatch(runner):
    assert runner.shape_mismatch(publish([exposure()], periodIndex=0), publish([exposure()]), "data") is not None


def test_array_length_difference_is_a_mismatch(runner):
    mismatch = runner.shape_mismatch(publish([exposure(), exposure()]), publish([exposure()]), "data")
    assert mismatch == ("data.exposures length", 1, 2)


def test_container_replaced_by_scalar_is_a_mismatch(runner):
    assert runner.shape_mismatch(publish(None), publish([exposure()]), "data") is not None
    assert runner.shape_mismatch(publish([exposure()]), publish("x"), "data") is not None


def test_empty_object_and_array_baselines_reject_additions(runner):
    assert runner.shape_mismatch({}, {}, "data") is None
    assert runner.shape_mismatch([], [], "data") is None
    assert runner.shape_mismatch({"a": 1}, {}, "data") is not None
    assert runner.shape_mismatch([1], [], "data") is not None


# ---------------------------------------------------------------------------
# sameShapeAs reference resolution through validate_step
# ---------------------------------------------------------------------------


def test_valid_reference_passes(runner):
    prior = [response("publish", publish([exposure()]))]
    assert validate(runner, prior, publish([exposure(variant=2)]), 0) == []


def test_valid_reference_detects_added_field(runner):
    prior = [response("publish", publish([exposure()]))]
    failures = validate(runner, prior, publish([exposure(periodIndex=3)]), 0)
    assert len(failures) == 1
    assert "sameShapeAs step 0" in failures[0]["field"]


def test_sdk_specific_baseline_fields_are_accepted_when_present_in_both(runner):
    # An SDK that publishes extra envelope fields (e.g. JS sdkVersion, ruleOverride) matches its own baseline.
    prior = [response("publish", publish([exposure(ruleOverride=None)], sdkVersion="1.0"))]
    assert validate(runner, prior, publish([exposure(ruleOverride=None)], sdkVersion="1.0"), 0) == []


def test_sdk_specific_baseline_field_missing_from_actual_fails(runner):
    prior = [response("publish", publish([exposure()], sdkVersion="1.0"))]
    assert validate(runner, prior, publish([exposure()]), 0) != []


def test_empty_object_baseline_is_valid(runner):
    prior = [response("publish", {})]
    assert validate(runner, prior, {}, 0) == []
    assert validate(runner, prior, {"a": 1}, 0) != []


def test_empty_array_baseline_is_valid(runner):
    prior = [response("publish", [])]
    assert validate(runner, prior, [], 0) == []
    assert validate(runner, prior, [1], 0) != []


def reference_failure(failures):
    assert len(failures) == 1, failures
    assert failures[0]["field"] == "events[0].sameShapeAs"
    return failures[0]["actual"]


def test_reference_event_without_data_fails(runner):
    prior = [response("publish", None, include_data=False)]
    assert "has no data" in reference_failure(validate(runner, prior, 123, 0))


def test_reference_event_with_null_data_fails(runner):
    prior = [response("publish", None)]
    assert "has no data" in reference_failure(validate(runner, prior, None, 0))


def test_reference_step_without_events_fails(runner):
    prior = [{"result": None, "events": []}]
    assert "has no event 0" in reference_failure(validate(runner, prior, publish([]), 0))


def test_reference_step_without_response_fails(runner):
    # A failed earlier step leaves no stored response.
    prior = [None, response("publish", publish([]))]
    assert "has no response" in reference_failure(validate(runner, prior, publish([]), 0))


@pytest.mark.parametrize("reference", [-1, 1, 2, 7])
def test_reference_outside_earlier_steps_fails(runner, reference):
    prior = [response("publish", publish([])), None]
    actual = publish([])
    assert "is not an earlier step" in reference_failure(validate(runner, prior, actual, reference, step_index=1))


@pytest.mark.parametrize("reference", [True, False, 0.0, "0", None, [0]])
def test_non_integer_reference_fails(runner, reference):
    prior = [response("publish", publish([]))]
    assert "must be an integer" in reference_failure(validate(runner, prior, publish([]), reference))


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------


def test_capability_false_skips_switchback_scenarios_without_any_request(runner, monkeypatch, built_group):
    calls = []

    def record(self, method, url, *args, **kwargs):
        calls.append((method, url))
        raise AssertionError(f"unexpected HTTP request {method} {url}")

    # Every requests entry point, module-level helpers included, goes through Session.request.
    monkeypatch.setattr(requests.sessions.Session, "request", record)

    gated = [s for s in canonical() if generator.CAPABILITY in s.get("requires", [])]
    assert len(gated) == len(built_group) - 1 == 23

    # javascript lists switchback as a reviewed `unsupported` exception, so its gated scenarios skip.
    reason = runner.policy["sdks"]["javascript"]["unsupported"]["switchback"]
    runner.capabilities = {"javascript": {"switchback": False}}
    for scenario in gated:
        result = runner.run_and_validate_sdk("javascript", "http://sdk.invalid", scenario)
        assert result == {"passed": True, "skipped": True, "reason": f"reviewed exception: switchback ({reason})"}
    assert calls == []


def test_switchback_policy_requires_java_and_excepts_every_other_sdk(runner):
    sdks = runner.policy["sdks"]
    assert "switchback" in runner.policy["capabilities"]
    assert "switchback" in sdks["java"]["required"]
    for sdk, entry in sdks.items():
        if sdk != "java":
            assert "switchback" not in entry["required"], sdk
            assert entry["unsupported"]["switchback"].endswith("java-sdk only (R33)"), sdk
    assert "bucket" not in runner.policy["capabilities"]["switchback"]


def test_java_without_switchback_still_runs_the_gated_scenarios(runner):
    # Required capabilities fail closed: java not advertising switchback is a policy violation, not a skip.
    gated = [s for s in canonical() if generator.CAPABILITY in s.get("requires", [])]
    runner.capabilities = {"java": {"switchback": False}}
    assert all(runner.capability_gate("java", s) is None for s in gated)
    assert any("'switchback'" in v for v in runner.policy_violations_for("java", {"switchback": False}))


def test_inertness_scenario_is_ungated_and_runs_without_the_capability(runner):
    inert = [s for s in canonical() if "Switchback - Inertness" in s["name"]]
    assert len(inert) == 1
    assert "requires" not in inert[0]

    runner.capabilities = {"sdk": {"switchback": False}}
    assert runner.should_skip_wrapper_test("sdk", inert[0])[0] is False


def test_canonical_numbering_is_unique_and_the_switchback_group_is_contiguous_tail():
    scenarios = canonical()
    numbers = [int(s["name"].split(" - ", 1)[0]) for s in scenarios]
    # Main removed seven JS-only scenarios without renumbering the remaining IDs.
    assert numbers == sorted(set(numbers))

    group = [i for i, s in enumerate(scenarios) if generator.GROUP_MARKER in s["name"]]
    assert group == list(range(240, len(scenarios)))
    assert len(group) == 24
    assert numbers[group[0]] == 248 and numbers[group[-1]] == 271
    assert numbers[group[0]:] == list(range(248, 272))


# ---------------------------------------------------------------------------
# generate_switchback_scenarios.py --check
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_copy(tmp_path, monkeypatch):
    scenarios = canonical()
    path = tmp_path / "test_scenarios_complete.json"
    monkeypatch.setattr(generator, "CANONICAL", str(path))
    monkeypatch.setattr(sys, "argv", ["generate_switchback_scenarios.py", "--check"])

    def write(content):
        path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return scenarios, write


def golden_scenario(scenarios):
    return next(s for s in scenarios if "Switchback - Golden Vectors - assignSwitchback - 2 Arms" in s["name"])


def test_check_passes_on_current_file(canonical_copy):
    scenarios, write = canonical_copy
    write(scenarios)
    assert generator.main() == 0


def test_check_fails_on_stale_expectation(canonical_copy):
    scenarios, write = canonical_copy
    mutated = copy.deepcopy(scenarios)
    step = golden_scenario(mutated)["steps"][0]
    step["expect"]["result"] = 1 - step["expect"]["result"]
    write(mutated)
    assert generator.main() == 1


def test_check_fails_on_omitted_step(canonical_copy):
    scenarios, write = canonical_copy
    mutated = copy.deepcopy(scenarios)
    del golden_scenario(mutated)["steps"][10]
    write(mutated)
    assert generator.main() == 1


def test_check_fails_on_duplicate_step(canonical_copy):
    scenarios, write = canonical_copy
    mutated = copy.deepcopy(scenarios)
    steps = golden_scenario(mutated)["steps"]
    steps.insert(0, copy.deepcopy(steps[0]))
    write(mutated)
    assert generator.main() == 1


def test_check_fails_on_omitted_scenario(canonical_copy):
    scenarios, write = canonical_copy
    write([s for s in scenarios if "Switchback - Inertness" not in s["name"]])
    assert generator.main() == 1


# ---------------------------------------------------------------------------
# R33 reference, wire shape and endpoints
# ---------------------------------------------------------------------------


def group(scenarios=None):
    return [s for s in (scenarios or canonical()) if generator.GROUP_MARKER in s["name"]]


def test_attribute_path_reference_matches_the_copied_cases():
    paths = generator.load_path_cases()
    for case in paths["cases"]:
        segments = generator.supported_attribute_path(case["path"])
        assert (segments is not None) == case["supported"], case
        if segments is not None:
            assert generator.resolve_attribute_path(paths["attributes"], segments) == case["value"], case


@pytest.mark.parametrize("path", ["geo.city", "'geo.city'", '"geo.city"', "a/b.c", "'geo", "tags/0", "m/0/0/city",
                                  "0/city", "items/-1/city", "", None, 5])
def test_unsupported_paths(path):
    assert generator.supported_attribute_path(path) is None


@pytest.mark.parametrize("value, key", [("s-1", "s-1"), (42, "42"), (4_000_000_000, "4000000000"), (-7, "-7"),
                                        ("", None), (None, None), (True, None), (False, None), (3.0, None),
                                        (1.5, None), ({"a": 1}, None), (["a"], None)])
def test_cluster_value_normalisation(value, key):
    assert generator.normalize_cluster_value(value) == key


def test_fixtures_are_the_pinned_platform_blobs():
    def blob(path):
        with open(path, "rb") as src:
            data = src.read()
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    assert blob(generator.GOLDEN).startswith("b314abeb9e97")
    assert blob(generator.PATH_CASES).startswith("7b1d3fed5985")
    assert not os.path.exists(os.path.join(REPO, "fixtures", "switchback", "bucket-golden-vectors.json"))


def test_only_assign_and_permutation_endpoints_remain():
    assert SWITCHBACK_ACTIONS == {"switchbackAssign": "/switchback/assign",
                                  "switchbackPermutation": "/switchback/permutation"}
    actions = {st["action"] for s in canonical() for st in s["steps"]}
    assert "switchbackBucket" not in actions


LEGACY_FIELDS = ("switchbackClusterUnitType", "switchbackClusterCount", "switchbackClusterAttribute")


def experiments_of(scenario):
    found = list(scenario["contextData"]["experiments"])
    for step in scenario["steps"]:
        if step["action"] == "refresh":
            found += step["params"]["newData"]["experiments"]
    return found


def test_switchbacks_carry_r33_fields_and_legacy_fields_only_where_tested():
    for scenario in group():
        for exp in experiments_of(scenario):
            if exp.get("randomizationType") != "switchback":
                continue
            assert "switchbackStartAt" in exp or exp["name"].endswith("no_anchor"), exp["name"]
            if any(f in exp for f in LEGACY_FIELDS):
                assert "Legacy Path" in scenario["name"], (scenario["name"], exp["name"])
            clusters = exp.get("switchbackClusters")
            assert clusters is None or isinstance(clusters, dict)


def test_ordered_clusters_are_in_the_checked_in_text():
    """The declared order is in the checked-in text (what the orchestrator sends). That the wrapper
    keeps it is checked by the java JUnit round-trip test; --check guards the text itself."""
    scenario = next(s for s in group() if "Declared Order" in s["name"])
    exps = scenario["contextData"]["experiments"]
    assert [list(e["switchbackClusters"]) for e in exps] == [["session_id", "user_id"], ["user_id", "session_id"]]
    text = _canonical_text()
    assert '"switchbackClusters": {\n' in text
    i = text.index('"name": "exp_sb_order_user_first"')
    block = text[text.index('"switchbackClusters"', i):]
    assert block.index('"user_id"') < block.index('"session_id"')


def test_legacy_fields_are_present_and_ignored_by_the_model():
    scenario = next(s for s in group() if "Legacy Path" in s["name"])
    mixed = next(e for e in scenario["contextData"]["experiments"] if e["name"] == "exp_sb_legacy_fields_ignored")
    assert all(f in mixed for f in LEGACY_FIELDS) and mixed["switchbackClusters"] == {"store_id": "city"}
    assert generator.failing_steps(scenario, None) == []
    assert generator.failing_steps(scenario, "legacy_fields") != []


def test_inertness_carries_r33_and_legacy_fields_on_unit_experiments():
    (inert,) = [s for s in group() if "Inertness" in s["name"]]
    units = [e for e in inert["contextData"]["experiments"] if e.get("switchbackClusters")
             and e.get("randomizationType") != "switchback"]
    assert len(units) == 2
    assert all(list(e["switchbackClusters"]) == ["session_id", "store_id"] for e in units)
    assert generator.failing_steps(inert, "unit_experiment_as_switchback") != []


# ---------------------------------------------------------------------------
# Discrimination: replay the canonical context scenarios against the R33 model and its mutants
# ---------------------------------------------------------------------------


def context_scenarios(scenarios):
    return [s for s in scenarios if generator.GROUP_MARKER in s["name"] and s["steps"][0]["action"] == "createContextWith"]


def test_the_model_reproduces_every_checked_in_context_scenario():
    scenarios = context_scenarios(canonical())
    assert [int(s["name"].split(" - ", 1)[0]) for s in scenarios] == list(range(254, 272))
    assert [s["name"] for s in scenarios if generator.failing_steps(s, None)] == []


@pytest.mark.parametrize("mutant", generator.ALL_MUTANTS)
def test_every_mutant_fails_the_checked_in_scenarios_it_guards(mutant):
    by_name = {s["name"].split(" - Switchback - ", 1)[1]: s for s in context_scenarios(_canonical_parsed())}
    guarded = generator.MUTANT_SCENARIOS[mutant]
    assert guarded
    assert [n for n in guarded if not generator.failing_steps(by_name[n], mutant)] == [], mutant


def test_every_eligible_draw_differs_under_every_wrong_key_model(built_group):
    eligible, zero = 0, []
    for scenario in context_scenarios(built_group):
        _, model = generator.replay(scenario)
        for key, uid, exp in model.draws:
            eligible += 1
            variant = generator.draw(key, exp)
            wrong = generator.wrong_key_variants(key, uid, exp)
            if exp["name"] in generator.ELIGIBLE_ZERO_CASES:
                zero.append(exp["name"])
                assert variant == 0 and all(wrong.values()), (scenario["name"], key, wrong)
                continue
            assert variant != 0
            assert variant not in wrong.values(), (scenario["name"], key)
    assert eligible >= 20
    assert sorted(zero) == sorted(generator.ELIGIBLE_ZERO_CASES)


def test_eligible_control_is_served_exposed_and_published():
    """An eligible draw of 0 is variant 0 with assigned=true, eligible=true, exposed and published;
    zero_maps_to_one fails 254 only through this experiment's treatment and publish."""
    scenario = next(s for s in group() if "Single Source" in s["name"])
    (control,) = [e for e in scenario["contextData"]["experiments"] if e["name"] == "exp_sb_eligible_control"]
    assert len(control["split"]) >= 3 and control["switchbackStartAt"] == generator.ANCHOR_PERIOD_0
    steps = scenario["steps"]
    (i,) = [i for i, st in enumerate(steps) if st["action"] == "treatment"
            and st["params"]["experimentName"] == control["name"]]
    exposed = generator.exposure(control, 0, "store_id")
    assert steps[i]["expect"] == {"result": 0, "events": [{"type": "exposure", "data": exposed}]}
    (publish,) = [st for st in steps if st["action"] == "publish"]
    data = publish["expect"]["events"][0]["data"]
    assert exposed in data["exposures"] and data["units"] == [
        {"type": "store_id", "uid": generator.hash_unit(steps[0]["params"]["units"]["store_id"])}]
    j = steps.index(publish)
    assert generator.failing_steps(scenario, "zero_maps_to_one") == [i, j]
    others = [s for s in context_scenarios(canonical()) if "Single Source" not in s["name"]]
    assert [s["name"] for s in others if generator.failing_steps(s, "zero_maps_to_one")] == []


def test_check_fails_when_an_exposure_unit_is_wrong(canonical_copy):
    scenarios, write = canonical_copy
    mutated = copy.deepcopy(scenarios)
    scenario = next(s for s in mutated if "Declared Order" in s["name"])
    step = next(st for st in scenario["steps"] if st["action"] == "treatment")
    step["expect"]["events"][0]["data"]["unit"] = "user_id"
    write(mutated)
    assert generator.main() == 1


# The 240 scenarios inherited from PR #17 (53bc49c9), serialized with sort_keys=True.
MAIN_SHA256 = "9199f0575ee247c11c1630701e14692a7a67cf74c899eddbfcaf3099262d9495"


def test_main_scenarios_are_untouched():
    kept = [s for s in canonical() if generator.GROUP_MARKER not in s["name"]]
    assert len(kept) == 240
    assert hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest() == MAIN_SHA256


def test_check_fails_when_only_the_declared_cluster_order_changes(canonical_copy):
    # Dict equality ignores key order; --check compares the serialized text, so it does not.
    scenarios, write = canonical_copy
    mutated = copy.deepcopy(scenarios)
    scenario = next(s for s in mutated if "Unit Without A Value Is Skipped" in s["name"])
    exp = scenario["contextData"]["experiments"][0]
    exp["switchbackClusters"] = dict(reversed(list(exp["switchbackClusters"].items())))
    assert mutated == scenarios
    write(mutated)
    assert generator.main() == 1


def test_strict_audience_mismatch_is_not_assigned():
    scenario = next(s for s in group() if "Precedence" in s["name"])
    exposures = [e["data"] for st in scenario["steps"] if st["action"] == "treatment" for e in st["expect"]["events"]]
    (strict,) = [e for e in exposures if e["name"] == "exp_sb_audience_strict"]
    assert strict["assigned"] is False and strict["audienceMismatch"] is True and strict["variant"] == 0


def test_rules_full_on_and_strict_audiences_never_apply_to_an_ineligible_cluster():
    scenario = next(s for s in group() if "Precedence" in s["name"])
    steps = {st["params"]["experimentName"]: (i, st) for i, st in enumerate(scenario["steps"])
             if st["action"] == "treatment"}
    for name in ("exp_sb_full_on_ineligible", "exp_sb_rule_ineligible", "exp_sb_strict_ineligible"):
        assert steps[name][1]["expect"] == {"result": 0, "events": []}, name
    failing = generator.failing_steps(scenario, "precedence_on_ineligible")
    assert steps["exp_sb_rule_ineligible"][0] in failing
    assert steps["exp_sb_full_on_ineligible"][0] in failing


def test_override_without_a_cluster_value_is_exposed_under_the_declared_unit_not_unit_type():
    scenario = next(s for s in group() if "Precedence" in s["name"])
    (exp,) = [e for e in scenario["contextData"]["experiments"] if e["name"] == "exp_sb_override_no_value"]
    assert exp["unitType"] == "user_id" and list(exp["switchbackClusters"]) == ["store_id"]
    (step,) = [st for st in scenario["steps"] if st["action"] == "treatment"
               and st["params"]["experimentName"] == "exp_sb_override_no_value"]
    assert step["expect"]["events"][0]["data"]["unit"] == "store_id"


def test_unit_variant_matches_the_main_suite_vectors():
    # Each vector is read back from its main-suite scenario: the uid from the first create step's
    # units, the split and seeds from the experiment (the traffic fields for 13), and the expected
    # variant from the scenario's first treatment.
    by_number = {int(s["name"].split(" - ", 1)[0]): s for s in canonical()}
    numbers = (4, 52, 13)
    assert len(numbers) == len(generator.UNIT_VARIANT_VECTORS)
    for number, (uid, split, seed_hi, seed_lo, expected) in zip(numbers, generator.UNIT_VARIANT_VECTORS):
        scenario = by_number[number]
        exp = scenario["contextData"]["experiments"][0]
        create = next(st for st in scenario["steps"] if st["action"].startswith("createContext"))
        assert str(create["params"]["units"][exp["unitType"]]) == uid, number
        fields = ("trafficSplit", "trafficSeedHi", "trafficSeedLo") if number == 13 else ("split", "seedHi", "seedLo")
        assert tuple(exp[f] for f in fields) == (split, seed_hi, seed_lo), number
        treatment = next(st for st in scenario["steps"] if st["action"] == "treatment"
                         and st["params"]["experimentName"] == exp["name"])
        assert treatment["expect"]["result"] == expected, number
        assert generator.unit_variant(uid, split, seed_hi, seed_lo) == expected, number


def test_multi_unit_publishes_pin_every_hashed_unit_as_a_set():
    for scenario in group():
        units = scenario["steps"][0]["params"].get("units", {})
        for step in scenario["steps"]:
            for event in step["expect"].get("events", []):
                if event["type"] != "publish":
                    continue
                data = event["data"]
                assert data["hashed"] is True, scenario["name"]
                assert data["units"] == [{"type": t, "uid": generator.hash_unit(u)} for t, u in units.items()]
                assert event.get("unorderedUnits", False) is (len(units) > 1), scenario["name"]
