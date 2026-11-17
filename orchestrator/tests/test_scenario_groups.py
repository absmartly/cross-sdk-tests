"""Group placement and composition guards, without the external JS oracle."""

import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import generate_holdout_conformance_scenarios as holdout_conformance
import generate_parity_round_scenarios as parity
import generate_sdk_round_2026_11_scenarios as sdk_round
import generate_switchback_scenarios as switchback
import scenario_groups
from scenario_groups import place_group


def recorded_responses(markers):
    """The JS reference's recorded results for every checked-in scenario of these groups, by title."""
    canonical = json.loads(Path(sdk_round.CANONICAL).read_text(encoding="utf-8"))
    responses = {}
    for scenario in canonical:
        for marker in markers:
            if marker in scenario["name"]:
                ready, *steps = scenario["steps"]
                responses[scenario["name"].split(marker, 1)[1]] = {
                    "ready": ready["expect"]["result"],
                    "readyEvents": ready["expect"]["events"],
                    "steps": [step["expect"] for step in steps],
                }
    return responses


@pytest.fixture
def recorded_oracle(monkeypatch):
    round_responses = recorded_responses(sdk_round.GROUP_MARKERS)
    # Only the parity rules and semver groups come from the reference; X-Agent and Client
    # Application are written by the generator itself.
    parity_responses = recorded_responses((parity.RULES_MARKER, parity.SEMVER_MARKER))

    monkeypatch.setattr(sdk_round, "ensure_reference_build", lambda: None)
    monkeypatch.setattr(sdk_round, "run_reference", lambda specs: [
        copy.deepcopy(round_responses[spec[0]]) for spec in specs
    ])
    monkeypatch.setattr(parity, "run_reference", lambda specs: [
        copy.deepcopy(parity_responses[spec["title"]]) for spec in specs
    ])
    holdout_responses = recorded_responses((holdout_conformance.MARKER,))
    monkeypatch.setattr(holdout_conformance, "run_reference", lambda specs: [
        copy.deepcopy(holdout_responses[spec["title"]]) for spec in specs
    ])


GENERATORS = {"sdk-round": sdk_round, "parity": parity, "switchback": switchback,
              "holdout-conformance": holdout_conformance}


@pytest.mark.parametrize("order", [("sdk-round", "parity", "switchback", "holdout-conformance"),
                                   ("holdout-conformance", "switchback", "parity", "sdk-round"),
                                   ("parity", "holdout-conformance", "switchback", "sdk-round")],
                         ids=lambda order: "-then-".join(order))
def test_generators_compose_byte_identically(order, recorded_oracle, tmp_path, monkeypatch):
    raw = Path(sdk_round.CANONICAL).read_bytes()
    path = tmp_path / "scenarios.json"
    path.write_bytes(raw)
    monkeypatch.setattr(switchback, "CANONICAL", str(path))

    def run(module, check=False):
        argv = [module.__file__]
        if module is not switchback:
            argv += ["--file", str(path)]
        if check:
            argv.append("--check")
        monkeypatch.setattr(sys, "argv", argv)
        assert module.main() == 0

    for name in order:
        run(GENERATORS[name])
        assert path.read_bytes() == raw, name
    for name in order:
        run(GENERATORS[name], check=True)


def scenario(number, marker="Retained", title="case"):
    return {"name": f"{number} - {marker} - {title}"}


def test_replace_group_preserves_position_and_first_id():
    existing = [scenario(3), scenario(10, "A"), scenario(11, "B"), scenario(20)]
    seen = []

    def build(first):
        seen.append(first)
        return [scenario(first, "A", "rebuilt"), scenario(first + 1, "B", "rebuilt")]

    updated, group = place_group(existing, (" - A - ", " - B - "), build)
    assert seen == [10]
    assert updated == [existing[0], *group, existing[-1]]
    assert [s["name"] for s in group] == ["10 - A - rebuilt", "11 - B - rebuilt"]
    assert existing[1]["name"] == "10 - A - case"


def test_absent_group_appends_after_max_id_with_gaps():
    existing = [scenario(3), scenario(18)]
    updated, group = place_group(existing, (" - A - ",), lambda n: [scenario(n, "A")])
    assert updated == [*existing, scenario(19, "A")]
    assert group == [scenario(19, "A")]


def test_noncontiguous_markers_fail_before_building():
    def build(first):
        pytest.fail("must reject non-contiguous markers before rebuilding")

    with pytest.raises(ValueError, match="non-contiguous"):
        place_group([scenario(1, "A"), scenario(2), scenario(3, "B")],
                    (" - A - ", " - B - "), build)


def test_growing_group_collision_tells_operator_what_to_regenerate():
    existing = [scenario(1), scenario(2, "A"), scenario(3, "Next")]
    with pytest.raises(ValueError, match=r"collide.*3 - Next - case.*[Rr]egenerate"):
        place_group(existing, (" - A - ",),
                    lambda n: [scenario(n, "A"), scenario(n + 1, "A")])
    assert existing == [scenario(1), scenario(2, "A"), scenario(3, "Next")]


def test_growing_group_can_use_gap_without_renumbering_next_group():
    existing = [scenario(2, "A"), scenario(5, "Next")]
    updated, group = place_group(existing, (" - A - ",),
                                 lambda n: [scenario(n, "A"), scenario(n + 1, "A")])
    assert updated == [*group, existing[-1]]


def test_absent_group_in_empty_file_starts_at_one():
    assert place_group([], (" - A - ",), lambda n: [scenario(n, "A")]) == (
        [scenario(1, "A")], [scenario(1, "A")])


def test_shrinking_group_keeps_next_ids_and_is_stable_on_rebuild():
    existing = [scenario(1), scenario(2, "A"), scenario(3, "A"), scenario(4, "Next")]
    updated, _ = place_group(existing, (" - A - ",), lambda n: [scenario(n, "A")])
    assert updated == [scenario(1), scenario(2, "A"), scenario(4, "Next")]
    again, _ = place_group(updated, (" - A - ",), lambda n: [scenario(n, "A")])
    assert again == updated


def test_empty_rebuild_removes_group_without_renumbering():
    existing = [scenario(1), scenario(2, "A"), scenario(3, "Next")]
    assert place_group(existing, (" - A - ",), lambda n: []) == ([scenario(1), scenario(3, "Next")], [])


def test_last_group_can_grow_at_end_of_file():
    existing = [scenario(1), scenario(2, "A")]
    updated, _ = place_group(existing, (" - A - ",),
                             lambda n: [scenario(n, "A"), scenario(n + 1, "A")])
    assert updated == [scenario(1), scenario(2, "A"), scenario(3, "A")]


def test_fixed_first_number_inserts_before_higher_ids_and_leaves_lower_ones():
    existing = [scenario(3), scenario(10, "A"), scenario(11, "A"), scenario(20), scenario(50)]
    updated, group = place_group(existing, (" - A - ",), lambda n: [scenario(n, "A")], first_number=30)
    assert group == [scenario(30, "A")]
    assert updated == [scenario(3), scenario(20), scenario(30, "A"), scenario(50)]


def test_fixed_first_number_rejects_a_retained_scenario_inside_the_range():
    existing = [scenario(1), scenario(31)]
    with pytest.raises(ValueError, match="31 - Retained - case"):
        place_group(existing, (" - A - ",), lambda n: [scenario(n, "A"), scenario(n + 1, "A")], first_number=30)


def test_switchback_group_owns_its_fixed_range_after_the_parity_groups():
    canonical = json.loads(Path(sdk_round.CANONICAL).read_text(encoding="utf-8"))
    numbers = [int(s["name"].split(" - ", 1)[0]) for s in canonical if switchback.GROUP_MARKER in s["name"]]
    parity_numbers = [int(s["name"].split(" - ", 1)[0]) for s in canonical
                      if any(m in s["name"] for m in parity.PARITY_MARKERS)]
    assert numbers[0] == scenario_groups.SWITCHBACK_FIRST_NUMBER
    assert max(parity_numbers) < numbers[0]
