"""Group placement and composition guards, without the external JS oracle."""

import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import generate_sdk_round_2026_11_scenarios as sdk_round
import generate_switchback_scenarios as switchback


@pytest.fixture
def recorded_oracle(monkeypatch):
    canonical = json.loads(Path(sdk_round.CANONICAL).read_text(encoding="utf-8"))
    responses = {}
    for scenario in canonical:
        for marker in sdk_round.GROUP_MARKERS:
            if marker in scenario["name"]:
                ready, *steps = scenario["steps"]
                responses[scenario["name"].split(marker, 1)[1]] = {
                    "ready": ready["expect"]["result"],
                    "readyEvents": ready["expect"]["events"],
                    "steps": [step["expect"] for step in steps],
                }

    monkeypatch.setattr(sdk_round, "ensure_reference_build", lambda: None)
    monkeypatch.setattr(sdk_round, "run_reference", lambda specs: [
        copy.deepcopy(responses[title]) for title, _, _, _ in specs
    ])


@pytest.mark.parametrize("order", [(sdk_round, switchback), (switchback, sdk_round)],
                         ids=["sdk-then-switchback", "switchback-then-sdk"])
def test_generators_compose_byte_identically(order, recorded_oracle, tmp_path, monkeypatch):
    from scenario_groups import place_group

    raw = Path(sdk_round.CANONICAL).read_bytes()
    scenarios = json.loads(raw)
    for module in order:
        if module is sdk_round:
            scenarios, _ = place_group(scenarios, sdk_round.GROUP_MARKERS, sdk_round.build_all_groups)
        else:
            scenarios, _ = place_group(scenarios, (switchback.GROUP_MARKER,),
                                       lambda n: switchback.build_group(switchback.load_fixtures(), n))
        assert (json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n").encode("utf-8") == raw

    # Also exercise both CLI entry points and their own --check paths.
    path = tmp_path / "scenarios.json"
    path.write_bytes(raw)
    monkeypatch.setattr(switchback, "CANONICAL", str(path))

    def run(module, check=False):
        argv = [module.__file__]
        if module is sdk_round:
            argv += ["--file", str(path)]
        if check:
            argv.append("--check")
        monkeypatch.setattr(sys, "argv", argv)
        assert module.main() == 0

    for module in order:
        run(module)
        assert path.read_bytes() == raw
    for module in order:
        run(module, check=True)


def scenario(number, marker="Retained", title="case"):
    return {"name": f"{number} - {marker} - {title}"}


def test_replace_group_preserves_position_and_first_id():
    from scenario_groups import place_group

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
    from scenario_groups import place_group

    existing = [scenario(3), scenario(18)]
    updated, group = place_group(existing, (" - A - ",), lambda n: [scenario(n, "A")])
    assert updated == [*existing, scenario(19, "A")]
    assert group == [scenario(19, "A")]


def test_noncontiguous_markers_fail_before_building():
    from scenario_groups import place_group

    def build(first):
        pytest.fail("must reject non-contiguous markers before rebuilding")

    with pytest.raises(ValueError, match="non-contiguous"):
        place_group([scenario(1, "A"), scenario(2), scenario(3, "B")],
                    (" - A - ", " - B - "), build)


def test_growing_group_collision_tells_operator_what_to_regenerate():
    from scenario_groups import place_group

    existing = [scenario(1), scenario(2, "A"), scenario(3, "Next")]
    with pytest.raises(ValueError, match=r"collide.*3 - Next - case.*[Rr]egenerate"):
        place_group(existing, (" - A - ",),
                    lambda n: [scenario(n, "A"), scenario(n + 1, "A")])
    assert existing == [scenario(1), scenario(2, "A"), scenario(3, "Next")]


def test_growing_group_can_use_gap_without_renumbering_next_group():
    from scenario_groups import place_group

    existing = [scenario(2, "A"), scenario(5, "Next")]
    updated, group = place_group(existing, (" - A - ",),
                                 lambda n: [scenario(n, "A"), scenario(n + 1, "A")])
    assert updated == [*group, existing[-1]]


def test_absent_group_in_empty_file_starts_at_one():
    from scenario_groups import place_group

    assert place_group([], (" - A - ",), lambda n: [scenario(n, "A")]) == (
        [scenario(1, "A")], [scenario(1, "A")])
