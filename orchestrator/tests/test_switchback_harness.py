"""Unit tests for the switchback harness: sameShapeAs reference resolution and shape matching,
capability gating, and generate_switchback_scenarios.py --check.

Run from the repository root:  python3 -m pytest orchestrator/tests -q
"""

import copy
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


def load_fixtures():
    buckets, _ = generator.load_bucket_fixtures()
    return generator.load_golden(), buckets


@pytest.fixture
def runner():
    return Orchestrator({}, verbose=False)


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


def test_capability_false_skips_switchback_scenarios_without_any_request(runner, monkeypatch):
    calls = []

    def record(self, method, url, *args, **kwargs):
        calls.append((method, url))
        raise AssertionError(f"unexpected HTTP request {method} {url}")

    # Every requests entry point, module-level helpers included, goes through Session.request.
    monkeypatch.setattr(requests.sessions.Session, "request", record)

    with open(generator.CANONICAL, encoding="utf-8") as src:
        gated = [s for s in json.load(src) if generator.CAPABILITY in s.get("requires", [])]
    assert len(gated) == len(generator.build_group(*load_fixtures(), 1)) - 1 == 29

    runner.capabilities = {"sdk": {"switchback": False}}
    for scenario in gated:
        result = runner.run_and_validate_sdk("sdk", "http://sdk.invalid", scenario)
        assert result == {"passed": True, "skipped": True, "reason": "SDK does not support: switchback"}
    assert calls == []


def test_inertness_scenario_is_ungated_and_runs_without_the_capability(runner):
    with open(generator.CANONICAL, encoding="utf-8") as src:
        inert = [s for s in json.load(src) if "Switchback - Inertness" in s["name"]]
    assert len(inert) == 1
    assert "requires" not in inert[0]

    runner.capabilities = {"sdk": {"switchback": False}}
    assert runner.should_skip_wrapper_test("sdk", inert[0])[0] is False


def test_canonical_numbering_is_contiguous_and_the_switchback_group_is_the_tail():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        scenarios = json.load(src)
    numbers = [int(s["name"].split(" - ", 1)[0]) for s in scenarios]
    assert numbers == list(range(1, len(scenarios) + 1))

    group = [i for i, s in enumerate(scenarios) if generator.GROUP_MARKER in s["name"]]
    assert group == list(range(227, len(scenarios)))
    assert len(group) == 30


# ---------------------------------------------------------------------------
# generate_switchback_scenarios.py --check
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_copy(tmp_path, monkeypatch):
    with open(generator.CANONICAL, encoding="utf-8") as src:
        scenarios = json.load(src)
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
# Bucket reference
# ---------------------------------------------------------------------------


def test_bucket_of_matches_every_copied_bucket_vector():
    buckets, _ = generator.load_bucket_fixtures()
    assert len(buckets["buckets"]) == 702
    mismatches = [c for c in buckets["buckets"]
                  if generator.bucket_of(c["key"], generator.exact_long(c["seed"]), c["count"]) != c["expectedBucket"]]
    assert mismatches == []


@pytest.mark.parametrize("key, seed, count, bucket", [
    ("1tdwU5K8evYzMovqjExpBA", 0, 10, 6),
    ("\u6771\u4eac", -123456789012345, 50, 19),
    ("a\x1fb", -(1 << 63), 100, 43),
])
def test_bucket_of_matches_the_independent_hashlib_vectors(key, seed, count, bucket):
    assert generator.bucket_of(key, seed, count) == bucket


@pytest.mark.parametrize("count", [1000, 0, 20])
def test_bucket_of_rejects_disallowed_counts(count):
    with pytest.raises(ValueError):
        generator.bucket_of("k", 0, count)


def test_attribute_reference_matches_the_copied_key_and_path_vectors():
    buckets, paths = generator.load_bucket_fixtures()
    for case in buckets["attributeKeys"]:
        assert generator.normalize_attribute(case["value"]) == case["key"], case
    for case in paths["cases"]:
        supported = generator.supported_attribute_path(case["path"]) is not None
        value = generator.resolve_attribute_path(paths["attributes"], generator.parse_attribute_path(case["path"]))
        assert (supported, value) == (case["supported"], case["value"]), case
    # Index-position rule: unsupported, although a lax reader would resolve both paths.
    lax = {"tags": ["a", "b"], "m": [[{"city": "x"}]]}
    for path, lax_value in (("tags/0", "a"), ("m/0/0/city", "x")):
        assert generator.supported_attribute_path(path) is None, path
        assert generator.resolve_attribute_path(lax, generator.parse_attribute_path(path)) == lax_value


def test_bucket_vector_scenarios_replay_every_vector_through_the_bucket_endpoint():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        steps = [st for s in json.load(src) for st in s["steps"] if st["action"] == "switchbackBucket"]
    buckets, _ = generator.load_bucket_fixtures()
    replayed = sorted((st["params"]["key"], st["params"]["seed"], st["params"]["count"], st["expect"]["result"])
                      for st in steps)
    fixture = sorted((c["key"], c["seed"], c["count"], c["expectedBucket"]) for c in buckets["buckets"])
    assert replayed == fixture
    assert SWITCHBACK_ACTIONS["switchbackBucket"] == "/switchback/bucket"


def test_check_fails_on_stale_bucket_expectation(canonical_copy):
    scenarios, write = canonical_copy
    mutated = copy.deepcopy(scenarios)
    scenario = next(s for s in mutated if "Bucket Vectors - bucketOf - 50 Buckets" in s["name"])
    scenario["steps"][0]["expect"]["result"] = (scenario["steps"][0]["expect"]["result"] + 1) % 50
    write(mutated)
    assert generator.main() == 1


def test_generation_fails_when_bucket_of_disagrees_with_the_vectors(monkeypatch):
    monkeypatch.setattr(generator, "bucket_of", lambda key, seed, count: 0)
    with pytest.raises(SystemExit):
        generator.verify_bucket_reference(*generator.load_bucket_fixtures())
