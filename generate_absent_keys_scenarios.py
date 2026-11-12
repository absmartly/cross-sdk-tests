#!/usr/bin/env python3
"""Generate ungated absent-optional-key scenarios; expectations never come from an SDK.

The sticky assignment/hash and wire-event reference is shared with the switchback
generator (itself checked against the original suite). Edit this file, not JSON.
Run with --check to verify the canonical text without writing it.
"""

import argparse
import copy
import hashlib
import json
import sys

from scenario_groups import place_group

from generate_switchback_scenarios import (
    CANONICAL, create_step, experiment, exposure, publish_step, unit_variant,
)

GROUP_MARKER = " - Absent Optional Keys - "
BASELINE_COUNT = 264
BASELINE_SHA256 = "220a76068abca50df2506e16afd0c98652d256aa4e42556ff2036db6ff3ca943"
UNITS = {"session_id": "test123"}
FIELDS = [
    {"name": "owner", "type": "string", "value": "sdk-team"},
    {"name": "limit", "type": "number", "value": "42"},
    {"name": "enabled", "type": "boolean", "value": "false"},
    {"name": "metadata", "type": "json", "value": '{"tier":"gold","ids":[1,2]}'},
]


def baseline_digest(scenarios):
    return hashlib.sha256(json.dumps(scenarios, sort_keys=True).encode()).hexdigest()


def pair(kind="ordinary", omitted=("customFieldValues",)):
    twins = []
    for i, suffix in enumerate(("absent", "empty")):
        name = f"exp_optional_{suffix}"
        exp = experiment(800 + i, name, "session_id", 3,
                         customFieldValues=[], holdoutIds=[], audience="", assignmentRules="")
        if kind == "full-on":
            exp["fullOnVariant"] = 2
        elif kind == "feature-flag":
            # An enabled two-arm flag: full-on treatment, with real variant configs.
            exp["split"] = [0, 1]
            exp["fullOnVariant"] = 1
            exp["variants"] = exp["variants"][:2]
        for variant, config in enumerate(exp["variants"]):
            config["config"] = json.dumps({name + ".value": f"variant-{variant}"})
        if suffix == "absent":
            for key in omitted:
                del exp[key]
        twins.append(exp)
    return twins


def step(action, params, result, events=None):
    return {"action": action, "params": params,
            "expect": {"result": result, "events": events or []}}


def observations(experiments, exposed):
    """Reference for these fully eligible, audience/rule-free payloads and their API calls.

    Exposures are retained across payload-only refreshes. Peeks/custom fields never
    expose; treatment exposes once; variable reads then reuse that assignment.
    """
    steps = []
    keys = list(dict.fromkeys(f["name"] for e in experiments for f in e.get("customFieldValues", [])))
    # Match existing scenario 63's nonempty request body; results are context-wide.
    steps.append(step("customFieldKeys", {"experimentName": experiments[0]["name"]}, keys))
    for exp in experiments:
        name = exp["name"]
        variant = exp["fullOnVariant"] or unit_variant(
            UNITS[exp["unitType"]], exp["split"], exp["seedHi"], exp["seedLo"])
        variable_key = name + ".value"
        value = json.loads(exp["variants"][variant]["config"])[variable_key]
        steps.append(step("peek", {"experimentName": name}, variant))
        steps.append(step("peekVariableValue", {"key": variable_key, "defaultValue": "missing"}, value))
        fields = {f["name"]: f for f in exp.get("customFieldValues", [])}
        for field_name in [f["name"] for f in FIELDS] + ["unknown"]:
            field = fields.get(field_name)
            field_type = field["type"] if field else None
            field_value = field["value"] if field else None
            if field_type in ("number", "boolean", "json"):
                field_value = json.loads(field_value)
            params = {"experimentName": name, "fieldName": field_name}
            steps.append(step("customFieldValue", params, field_value))
            steps.append(step("customFieldValueType", params, field_type))
        event = exposure(exp, variant, full_on=exp["fullOnVariant"] != 0)
        events = [] if name in exposed else [{"type": "exposure", "data": event}]
        steps.append(step("treatment", {"experimentName": name}, variant, events))
        exposed[name] = event
        steps.append(step("variableValue", {"key": variable_key, "defaultValue": "missing"}, value))
        steps.append(step("treatment", {"experimentName": name}, variant))
        steps.append(step("peek", {"experimentName": name}, variant))
    steps.append(step("pending", {}, len(exposed)))
    return steps


def build_group(first_number=272):
    scenarios = []

    def add(title, description, experiments, *, refresh=False, holdouts=False):
        data = {"experiments": experiments}
        if holdouts:
            data["holdouts"] = []
        exposed = {}
        steps = [create_step(experiments, UNITS)]
        steps += observations(experiments, exposed)
        if refresh:
            # Keep the twin empty throughout; only the target changes metadata.
            for fields in (None, FIELDS, None):
                experiments = copy.deepcopy(experiments)
                if fields is None:
                    experiments[0].pop("customFieldValues", None)
                else:
                    experiments[0]["customFieldValues"] = copy.deepcopy(fields)
                new_data = {"experiments": experiments}
                steps.append(step("refresh", {"newData": new_data}, None, [
                    {"type": "refresh", "data": {"experiments": [
                        {"id": e["id"], "name": e["name"]} for e in experiments]}}]))
                steps += observations(experiments, exposed)
        if holdouts:
            new_data = copy.deepcopy(data)
            del new_data["holdouts"]
            steps.append(step("refresh", {"newData": new_data}, None, [
                {"type": "refresh", "data": {"experiments": [
                    {"id": e["id"], "name": e["name"]} for e in experiments]}}]))
            steps += observations(experiments, exposed)
        steps.append(publish_step(list(exposed.values()), UNITS))
        scenarios.append({"name": f"{first_number + len(scenarios)}{GROUP_MARKER}{title}",
                          "description": description, "contextData": data, "steps": steps})

    for kind in ("ordinary", "full-on", "feature-flag"):
        add("Custom Fields " + kind.title(),
            f"{kind}: omitted customFieldValues and [] twins share seeds/split and return the same "
            "treatment, peeks, variables, empty field APIs and canonical exposure bodies. "
            "All switchback keys are absent on both unit experiments.", pair(kind))
    mixed = pair()
    populated = copy.deepcopy(mixed[1])
    populated.update(id=802, name="exp_optional_populated", customFieldValues=copy.deepcopy(FIELDS))
    for i, variant in enumerate(populated["variants"]):
        variant["config"] = json.dumps({populated["name"] + ".value": f"variant-{i}"})
    add("Custom Fields Mixed Context", "Missing custom fields do not hide or inherit another experiment's "
        "populated string, number, boolean and JSON fields; global keys include only populated keys.",
        mixed + [populated])
    refreshed = pair(omitted=())
    add("Custom Fields Refresh", "[] -> absent -> populated -> absent updates field values/types/global keys "
        "without changing treatment, variables or queuing a duplicate exposure; the twin stays empty.",
        refreshed, refresh=True)
    add("Holdouts", "Absent holdoutIds versus [] twins are unchanged with top-level holdouts: [] and then "
        "with holdouts removed on refresh. Neither form requires holdout support.",
        pair(omitted=("holdoutIds",)), holdouts=True)
    add("Audience And Assignment Rules", "Absent audience and assignmentRules versus empty-string twins "
        "have no targeting/rules, with identical treatment, variables, fields and exposures.",
        pair(omitted=("audience", "assignmentRules")))
    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with open(CANONICAL, encoding="utf-8") as src:
        text = src.read()
    existing = json.loads(text)
    kept = [s for s in existing if GROUP_MARKER not in s["name"]]
    if len(kept) != BASELINE_COUNT or baseline_digest(kept) != BASELINE_SHA256:
        print("retained prefix differs from the 7e39b734 baseline", file=sys.stderr)
        return 1
    updated, group = place_group(existing, (GROUP_MARKER,), build_group)
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if text != rendered:
            print("test_scenarios_complete.json is stale: run generate_absent_keys_scenarios.py")
            return 1
        print(f"up to date: {len(group)} absent-optional-key scenarios")
        return 0
    with open(CANONICAL, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(group)} absent-optional-key)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
