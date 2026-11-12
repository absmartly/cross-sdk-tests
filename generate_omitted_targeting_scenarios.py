#!/usr/bin/env python3
"""Generate empty-targeting equivalence and refresh conformance from a Python model."""

import argparse
import copy
import json
from pathlib import Path

from generate_absent_keys_scenarios import step
from generate_switchback_scenarios import CANONICAL, create_step, experiment, exposure, publish_step, unit_variant
from scenario_groups import place_group

GROUP_MARKER = " - Omitted Empty Targeting - "
CAPABILITY = "omittedTargetingRefresh"
UNITS = {"session_id": "targeting-1"}
EMPTY_AUDIENCES = (None, "", '{"filter":[{"and":[]}]}')
EMPTY_RULES = (None, "", '{"rules":[]}', '{}')


def audience(country):
    return json.dumps({"filter": [{"eq": [{"var": "country"}, {"value": country}]}]}, separators=(",", ":"))


def twins(key, values, strict):
    result = []
    for i, value in enumerate(values):
        exp = experiment(900 + i, f"exp_targeting_{i}", "session_id", 3,
                         audience="", audienceStrict=strict, assignmentRules="")
        if value is None:
            exp.pop(key, None)
        else:
            exp[key] = value
        for v, variant in enumerate(exp["variants"]):
            variant["config"] = json.dumps({exp["name"] + ".value": f"variant-{v}"})
        result.append(exp)
    return result


def verdict(exp, country):
    """Empty targeting is eligible; an edited audience uses current attributes on next access.

    These fixtures contain only empty rules and country equality audiences. No SDK
    supplies expectations, and refresh never makes a previous mismatch sticky.
    """
    raw = exp.get("audience")
    mismatch = bool(raw and raw not in EMPTY_AUDIENCES and
                    json.loads(raw)["filter"][0]["eq"][1]["value"] != country)
    assigned = not (mismatch and exp["audienceStrict"])
    variant = unit_variant(UNITS["session_id"], exp["split"], exp["seedHi"], exp["seedLo"]) if assigned else 0
    return variant, exposure(exp, variant, assigned=assigned, audience_mismatch=mismatch)


def observe(steps, experiments, country):
    events = []
    baseline = None
    # Explicit structured empty form is the baseline, not the omitted form.
    for exp in reversed(experiments):
        variant, event = verdict(exp, country)
        value = f"variant-{variant}" if event["assigned"] else "missing"
        params = {"experimentName": exp["name"]}
        key = exp["name"] + ".value"
        steps.append(step("peek", params, variant))
        steps.append(step("peekVariableValue", {"key": key, "defaultValue": "missing"}, value))
        observation = step("treatment", params, variant, [{"type": "exposure", "data": event}])
        if baseline is None:
            baseline = len(steps)
        else:
            observation["expect"]["events"][0]["sameShapeAs"] = baseline
        steps.append(observation)
        steps.append(step("variableValue", {"key": key, "defaultValue": "missing"}, value))
        steps.append(step("treatment", params, variant))
        events.append(event)
    return events, baseline


def build_group(first_number=280):
    scenarios = []

    def add(title, experiments, refreshed=None, unrelated=False):
        original = copy.deepcopy(experiments)
        steps = [create_step(experiments, UNITS)]
        steps.append(step("attribute", {"name": "country", "value": "CA"}, None))
        events, baseline = observe(steps, experiments, "CA")
        steps.append(step("pending", {}, len(events)))
        if refreshed is not None:
            steps.append(step("refresh", {"newData": {"experiments": refreshed}}, None, [
                {"type": "refresh", "data": {"experiments": [{"id": e["id"], "name": e["name"]} for e in refreshed]}}]))
            if unrelated:
                steps.append(step("attribute", {"name": "unrelated", "value": "changed"}, None))
            more, baseline = observe(steps, refreshed, "CA")
            events += more
            steps.append(step("pending", {}, len(events)))
        steps.append(publish_step(events, UNITS))
        steps[-1]["expect"]["events"][0]["sameExposureShapeAs"] = baseline
        scenario = {"name": f"{first_number + len(scenarios)}{GROUP_MARKER}{title}",
                    "description": "Python-reference twin equivalence: " + title +
                    ". Identical seeds, split and unit; treatment, peek, variables and exposure flags/shapes agree.",
                    "contextData": {"experiments": original}, "steps": steps}
        if refreshed is not None:
            scenario["requires"] = [CAPABILITY]
        scenarios.append(scenario)

    for strict in (False, True):
        label = "Strict" if strict else "Non-Strict"
        add("Fresh Audience " + label, twins("audience", EMPTY_AUDIENCES, strict))
        add("Fresh Rules " + label, twins("assignmentRules", EMPTY_RULES, strict))
        refreshed = twins("audience", EMPTY_AUDIENCES, strict)
        initial = copy.deepcopy(refreshed)
        for exp in initial:
            exp["audience"] = audience("US")
        add("Audience Removed On Refresh " + label, initial, refreshed, unrelated=True)
    initial = twins("assignmentRules", (None, '{}', ""), True)
    refreshed = copy.deepcopy(initial)
    for i, exp in enumerate(initial):
        exp["audience"] = audience("US")
        refreshed[i]["audience"] = audience("CA")
        if i:
            refreshed[i]["assignmentRules"] = '{"rules":[]}'
    add("Audience Edited With Or Without Rules Signal", initial, refreshed)
    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(CANONICAL)
    text = path.read_text(encoding="utf-8")
    updated, group = place_group(json.loads(text), (GROUP_MARKER,), build_group)
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if rendered != text:
            print("omitted targeting scenarios are stale")
            return 1
    else:
        path.write_text(rendered, encoding="utf-8")
    print(f"{'checked' if args.check else 'wrote'} {len(group)} omitted-targeting scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
