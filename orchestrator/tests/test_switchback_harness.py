"""Unit tests for the switchback harness: sameShapeAs reference resolution and shape matching,
capability gating, and generate_switchback_scenarios.py --check.

Run from the repository root:  python3 -m pytest orchestrator/tests -q
"""

import copy
import hashlib
import json
import os
import sys
import time

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
    assert len(gated) == len(generator.build_group(generator.load_fixtures(), 1)) - 1 == 29

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
            assert entry["unsupported"]["switchback"].endswith("java-sdk only (Phase 1B)"), sdk


def test_java_without_switchback_still_runs_the_gated_scenarios(runner):
    # Required capabilities fail closed: java not advertising switchback is a policy violation, not a skip.
    with open(generator.CANONICAL, encoding="utf-8") as src:
        gated = [s for s in json.load(src) if generator.CAPABILITY in s.get("requires", [])]
    runner.capabilities = {"java": {"switchback": False}}
    assert all(runner.capability_gate("java", s) is None for s in gated)
    assert any("'switchback'" in v for v in runner.policy_violations_for("java", {"switchback": False}))


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
    assert group == list(range(229, len(scenarios)))
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


# ---------------------------------------------------------------------------
# Discrimination: replay the canonical context scenarios against mutant SDK models
# ---------------------------------------------------------------------------

BASE_MUTANTS = ("per_value", "sticky", "le", "count_10", "count_50", "count_100")
# Serves the switchback draw even when fullOnVariant is set; only a full-on scenario can catch it.
FULL_ON_MUTANTS = ("ignore_full_on",)
ATTRIBUTE_MUTANTS = ("lax_attr", "unit_fallback")

# Mutants that cannot change what a scenario serves, by scenario name suffix.
EXEMPT = {
    # Every experiment is ineligible or unassigned before any draw; only sticky reaches a variant.
    "Ineligible - exp_switchback_pre_anchor": {"per_value", "le", "count_10", "count_50", "count_100"},
    "Ineligible - exp_switchback_no_anchor": {"per_value", "le", "count_10", "count_50", "count_100"},
    "Unassigned - Cluster Unit Not Set": {"per_value", "le", "count_10", "count_50", "count_100", "sticky"},
    # The count is rejected before any draw, so only a count or sticky mutant reaches a variant.
    "Ineligible - Cluster Count Not 10, 50 Or 100": {"per_value", "le"},
    # fullOnVariant and an override are served ahead of any draw.
    "Full-On Serves The Full-On Variant": {"per_value", "le", "count_10", "count_50", "count_100", "sticky"},
    "Override Wins Over The Switchback Draw": {"per_value", "le", "count_10", "count_50", "count_100", "sticky"},
    # The served variant is 0 (ineligible) either way; only a mutant that finds a key draws.
    "No Bucket Key Is Ineligible": {"per_value", "le", "count_10", "count_50", "count_100"},
    "Does Not Reassign Within The Period": {"per_value", "le", "count_10", "count_50", "count_100", "lax_attr"},
    # Every attribute is set before the first read, so a unit fallback never triggers; 257's values
    # are plain strings, which a lax reader keys identically.
    "String, Whole Number, Nested And Array-Index Paths": {"unit_fallback"},
    "Mid-Period setAttribute Keeps The Bucket": {"lax_attr", "unit_fallback"},
}


def lax_attribute_key(attributes, path):
    """Any segment position, Python int() indices (+0, \\u0660, -1), and str()/JSON of any value."""
    current = attributes
    for segment in (s.strip("'\"") for s in path.split("/") if s):
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            current = current.get(segment) if isinstance(current, dict) else None
        if current is None:
            return None
    if isinstance(current, bool):
        return str(current).lower()
    if isinstance(current, int) and abs(current) >= 1 << 31:
        return str(float(current))
    return json.dumps(current, separators=(",", ":")) if isinstance(current, (dict, list)) else str(current)


def model_variant(exp, units, attributes, mutant, now):
    split = exp["split"]
    uid = units.get(exp.get("switchbackClusterUnitType", exp["unitType"]))
    if uid is None:
        return 0
    if exp["fullOnVariant"] and mutant != "ignore_full_on":
        return exp["fullOnVariant"]
    if mutant == "sticky" or exp.get("randomizationType") != "switchback":
        return generator.unit_variant(uid, split, exp["seedHi"], exp["seedLo"])
    start, count = exp.get("switchbackStartAt"), exp.get("switchbackClusterCount")
    count = int(mutant[6:]) if mutant.startswith("count_") else count
    if start is None or now < start or count not in generator.CLUSTER_COUNTS:
        return 0
    key, path = generator.hash_unit(uid), exp.get("switchbackClusterAttribute")
    if path is not None:
        segments = generator.supported_attribute_path(path)
        key = (lax_attribute_key(attributes, path) if mutant == "lax_attr" else
               segments and generator.normalize_attribute(generator.resolve_attribute_path(attributes, segments)))
        key = generator.hash_unit(uid) if key is None and mutant == "unit_fallback" else key
        if key is None:
            return 0
    seed = generator.experiment_seed(exp["seedHi"], exp["seedLo"])
    digest = hashlib.md5(f"sbcluster\x1f{exp['seedHi']}:{exp['seedLo']}\x1f{key}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "little" if mutant == "le" else "big") % count
    cluster = key if mutant == "per_value" else str(bucket)
    return generator.assign_switchback(cluster, (now - start) // generator.PERIOD_MS, seed, len(split))


def failing_steps(scenario, mutant):
    """Treatment steps whose expected result the model misses; each slot is read once, as in the SDK."""
    create = scenario["steps"][0]
    units, attributes, served, failures = create["params"]["units"], {}, {}, 0
    experiments = {e["name"]: e for e in scenario["contextData"]["experiments"]}
    now = int(time.time() * 1000)
    for step in scenario["steps"][1:]:
        params = step["params"]
        if step["action"] == "attribute":
            attributes[params["name"]] = params["value"]
        elif step["action"] == "override":
            served[params["experimentName"]] = params["variant"]
        elif step["action"] == "treatment":
            name = params["experimentName"]
            if name not in served:
                served[name] = model_variant(experiments[name], units, attributes, mutant, now)
            failures += served[name] != step["expect"]["result"]
    return failures


def context_scenarios(scenarios):
    return [s for s in scenarios if generator.GROUP_MARKER in s["name"] and "requires" in s
            and s["steps"][0]["action"] == "createContextWith"]


def applicable_mutants(scenario):
    experiments = [e for e in scenario["contextData"]["experiments"] if e.get("randomizationType") == "switchback"]
    has_attribute = any("switchbackClusterAttribute" in e for e in experiments)
    has_full_on = any(e.get("fullOnVariant") for e in experiments)
    mutants = [*BASE_MUTANTS, *(ATTRIBUTE_MUTANTS if has_attribute else ()), *(FULL_ON_MUTANTS if has_full_on else ())]
    configured = {e.get("switchbackClusterCount") for e in experiments}
    mutants = [m for m in mutants if not (m.startswith("count_") and configured == {int(m[6:])})]
    exempt = next((v for k, v in EXEMPT.items() if scenario["name"].endswith(k)), set())
    return [m for m in mutants if m not in exempt]


def test_every_context_scenario_fails_under_each_applicable_mutant():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        scenarios = context_scenarios(json.load(src))
    numbers = [int(s["name"].split(" - ", 1)[0]) for s in scenarios]
    assert numbers == list(range(239, 259))
    assert [s["name"] for s in scenarios if failing_steps(s, "correct")] == []
    survivors = [(s["name"], m) for s in scenarios for m in applicable_mutants(s) if failing_steps(s, m) == 0]
    assert survivors == []


def test_full_on_scenario_fails_when_full_on_is_ignored():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        (scenario,) = [s for s in context_scenarios(json.load(src)) if s["name"].endswith("Full-On Serves The Full-On Variant")]
    assert "ignore_full_on" in applicable_mutants(scenario)
    assert failing_steps(scenario, "correct") == 0
    assert failing_steps(scenario, "ignore_full_on") == 1


def test_every_exemption_names_a_scenario():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        names = [s["name"] for s in context_scenarios(json.load(src))]
    assert all(sum(n.endswith(k) for n in names) == 1 for k in EXEMPT)


# The 229 scenarios inherited from main (4734dcc), serialized with sort_keys=True.
MAIN_SHA256 = "8ddb8e702cf86ddf9346560071e8ba75ca6b58901ad90ebe8f2acb9ac4b25e86"


def test_main_scenarios_are_untouched():
    with open(generator.CANONICAL, encoding="utf-8") as src:
        kept = [s for s in json.load(src) if generator.GROUP_MARKER not in s["name"]]
    assert len(kept) == 229
    assert hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest() == MAIN_SHA256
