#!/usr/bin/env python3
"""Generate the Shared Variable Key scenario group of test_scenarios_complete.json.

A shared variable key is a variable key defined in the variant configs of more than one
experiment. Every SDK must resolve it exactly like the JavaScript SDK (owner decision,
2026-11-06). The JS reference (@absmartly/javascript-sdk, src/context.ts):

  * Index: for every experiment and every key of every variant config, the experiment is
    insertUniqueSorted into indexVariables[key] by experiment id, so each key's list is sorted
    and unique by id, independent of payload order.
  * variableValue(key, default): walk that list in order; for each experiment compute its
    assignment; if the assigned variant exists (assignment.variables !== undefined) queue its
    exposure unless already exposed, then if the key is in that variant's config AND the
    assignment is assigned or overridden, return the value. After the loop return default.
  * peekVariableValue(key, default): the same walk, never queues anything.

Consequences pinned here: lowest id wins when several assigned experiments define the key; the
payload order is irrelevant; an unassigned experiment (unit type absent, strict audience
mismatch) falls through AND is still exposed with assigned:false; a traffic-ineligible
experiment IS assigned (variant 0), so its control value is served when control defines the
key; an override on an otherwise-unassigned experiment wins; an assigned variant whose config
lacks the key falls through; peek returns the same value and queues nothing; a key no assigned
experiment defines returns the default with every visited experiment exposed.

The expected results are NOT computed here. Every scenario's steps are run through the real
JavaScript SDK, pinned in tools/shared-variable-keys/package.json, by
tools/shared-variable-keys/compute.js, and each step's `result` and `events` are recorded
verbatim (exposedAt timestamps dropped; the ready event projected to experiment id/name, as in
the rest of the suite). Run `npm ci --prefix tools/shared-variable-keys` first.

Usage:
    python3 generate_shared_variable_key_scenarios.py          # rewrite the group in place
    python3 generate_shared_variable_key_scenarios.py --check  # exit 1 if the canonical file is stale

Scenarios whose name carries GROUP_MARKER are replaced; every other scenario is left untouched,
the group is appended after the last of them, and the file is serialized exactly as before
(indent=2, ensure_ascii=False, trailing newline). The group is ungated: variableValue and
peekVariableValue are core actions every wrapper exposes.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")
TOOL_DIR = os.path.join(HERE, "tools", "shared-variable-keys")
COMPUTE = os.path.join(TOOL_DIR, "compute.js")

GROUP_MARKER = " - Shared Variable Key - "
JS_SDK_VERSION = "1.13.4"

UNIT = "svk-unit-1"
PRESENT_UNIT_TYPE = "session_id"
ABSENT_UNIT_TYPE = "user_id"  # never set on the context: experiments on it are unassigned
DEFAULT = "default"

STRICT_AUDIENCE = json.dumps({"filter": [{"eq": [{"var": "tier"}, {"value": "gold"}]}]}, separators=(",", ":"))
MISMATCHING_TIER = "silver"


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def experiment(exp_id: int, tag: str, keys_a: tuple, keys_b: tuple, unit_type: str = PRESENT_UNIT_TYPE,
               eligible: bool = True, strict_mismatch: bool = False) -> dict:
    """A two-variant experiment with split [0, 1]: a unit whose unit type is set is always served
    variant B (chooseVariant returns the only arm with weight), so no seed search is needed and
    every SDK agrees on the variant by construction. keys_a / keys_b are the keys variant A / B
    define, each with a value unique to (scenario, experiment, variant); no keys is a null config.
    unit_type=ABSENT_UNIT_TYPE: the context has no such unit, so the experiment is unassigned.
    eligible=False: trafficSplit [1, 0], the unit is assigned but ineligible and gets variant A.
    strict_mismatch=True: a strict audience the context's `tier` attribute never matches."""
    def config(keys, arm):
        return json.dumps({k: val(tag, exp_id, arm) for k in keys}, separators=(",", ":")) if keys else None

    return {
        "id": exp_id,
        "name": f"exp_svk_{tag}_{exp_id}",
        "iteration": 1,
        "unitType": unit_type,
        "seedHi": 0,
        "seedLo": 0,
        "split": [0, 1],
        "trafficSeedHi": 0,
        "trafficSeedLo": 0,
        "trafficSplit": [0, 1] if eligible else [1, 0],
        "fullOnVariant": 0,
        "applications": [{"name": "website"}],
        "variants": [{"name": "A", "config": config(keys_a, 0)}, {"name": "B", "config": config(keys_b, 1)}],
        "audience": STRICT_AUDIENCE if strict_mismatch else None,
        "audienceStrict": bool(strict_mismatch),
        "customFieldValues": None,
    }


def val(tag: str, exp_id: int, arm: int) -> str:
    """A value unique to (scenario, experiment, variant): a wrong model serves another one."""
    return f"{tag}-{exp_id}-{'AB'[arm]}"


def variable_value(key: str) -> tuple:
    return ("variableValue", {"key": key, "defaultValue": DEFAULT})


def peek_variable_value(key: str) -> tuple:
    return ("peekVariableValue", {"key": key, "defaultValue": DEFAULT})


PENDING = ("pending", {})
MISMATCH_ATTRIBUTE = ("attribute", {"name": "tier", "value": MISMATCHING_TIER})


def override(exp: dict, variant: int) -> tuple:
    return ("override", {"experimentName": exp["name"], "variant": variant})


READS = ("variableValue", "peekVariableValue")

# Keys. COLOR is the focal key of each scenario; SIZE is a fall-through probe: it is defined by
# an earlier experiment's variant the unit is NOT served (so the walk must skip it) and by a
# later experiment's served variant. Reading SIZE first makes every scenario visit and expose a
# non-winning experiment, so models that stop early, never fall through, read one experiment
# per key or expose only the winner give a different value or exposure list.
COLOR = "svk.color"
SIZE = "svk.size"


# ---------------------------------------------------------------------------
# Scenario specs: (title, description, payload experiments in payload order, steps)
# ---------------------------------------------------------------------------

def guard(exp_id: int, tag: str) -> dict:
    """The lowest-id experiment of a scenario: on a unit type the context lacks (unassigned), both
    variants defining SIZE. Every SIZE read visits it first, exposes it with assigned:false and
    must fall through it; a model that stops at (or serves) an unassigned experiment is caught in
    every scenario, not only in the unassigned ones."""
    return experiment(exp_id, tag, (SIZE,), (SIZE,), unit_type=ABSENT_UNIT_TYPE)


GUARD_NOTE = (f"The lowest id (guard) is on unit type {ABSENT_UNIT_TYPE}, which the context lacks, and defines "
              f"{SIZE} in both variants: it is exposed with assigned:false and never serves.")


def spec_lowest_id(tag: str, reverse: bool) -> tuple:
    g = guard(10, tag)
    lo = experiment(11, tag, (SIZE,), (COLOR,))
    hi = experiment(12, tag, (), (COLOR, SIZE))
    payload = [hi, lo, g] if reverse else [g, lo, hi]
    order = "descending id order (12, 11, 10)" if reverse else "id order (10, 11, 12)"
    title = ("Payload Order Irrelevant, Lowest Id Still Wins" if reverse
             else "Lowest Id Wins When Both Assigned")
    return (title,
            f"(a/b) Experiments 11 and 12, payload in {order}, both serve the unit variant B. {COLOR} is defined "
            f"by both B configs with different values: variableValue returns id 11's, the walk being sorted "
            f"by experiment id whatever the payload order, and exposes only 11. {SIZE} is defined by 10 (guard), "
            f"11's A (not served) and 12's B: read second, it visits 10 (exposed, unassigned), 11 (already "
            f"exposed, B lacks the key) and returns 12's value, exposing [10, 12]. " + GUARD_NOTE,
            payload, [variable_value(COLOR), variable_value(SIZE), PENDING])


def spec_unassigned(tag: str, how: str) -> tuple:
    if how == "unit":
        first = experiment(21, tag, (COLOR,), (COLOR,), unit_type=ABSENT_UNIT_TYPE)
        ids = (21, 22, 23)
        why = (f"experiment {ids[0]} is on unit type {ABSENT_UNIT_TYPE}, which the context lacks, so it is "
               f"unassigned (assigned:false, variant 0)")
        pre = []
    else:
        first = experiment(31, tag, (COLOR,), (COLOR,), strict_mismatch=True)
        ids = (31, 32, 33)
        why = (f"experiment {ids[0]} has a strict audience (tier == gold) and the context's tier is "
               f"{MISMATCHING_TIER}, so it is unassigned (assigned:false, audienceMismatch:true, variant 0)")
        pre = [MISMATCH_ATTRIBUTE]
    second = experiment(ids[1], tag, (SIZE,), (COLOR,))
    third = experiment(ids[2], tag, (), (SIZE,))
    title = ("Unassigned Lowest Id (Missing Unit Type) Falls Through And Is Exposed" if how == "unit"
             else "Unassigned Lowest Id (Strict Audience Mismatch) Falls Through And Is Exposed")
    return (title,
            f"(c) Payload order {ids[1]}, {ids[2]}, {ids[0]}. {why}; both its variants define {COLOR}, so a model "
            f"that ignores `assigned` would serve its control value. variableValue({COLOR}) skips it and returns "
            f"{ids[1]}'s B value; BOTH are exposed, {ids[0]} first with assigned:false. Then {SIZE} (defined by "
            f"{ids[1]}'s A, not served, and {ids[2]}'s B) falls through {ids[1]} to {ids[2]} and exposes only "
            f"{ids[2]}.",
            [second, third, first], pre + [variable_value(COLOR), variable_value(SIZE), PENDING])


def spec_ineligible(tag: str) -> tuple:
    g = guard(40, tag)
    ineligible = experiment(41, tag, (COLOR,), (SIZE,), eligible=False)
    other = experiment(42, tag, (), (COLOR, SIZE))
    return ("Traffic-Ineligible Lowest Id Is Assigned And Serves Its Control Value",
            f"Payload order 42, 40, 41. Experiment 41 has trafficSplit [1, 0]: the unit IS assigned "
            f"(assigned:true, eligible:false) and served variant A. {SIZE}, defined by 40 (guard), 41's B and "
            f"42's B, falls through 40 and 41 (A lacks it) to 42 with exposures [40, 41, 42]. {COLOR}, defined "
            f"by 41's A and 42's B, then returns 41's control value with no new exposure: an ineligible "
            f"assignment is still assigned, so it wins over the higher id. " + GUARD_NOTE,
            [other, g, ineligible], [variable_value(SIZE), variable_value(COLOR), PENDING])


def spec_override(tag: str) -> tuple:
    g = guard(50, tag)
    overridden = experiment(51, tag, (SIZE,), (COLOR,), unit_type=ABSENT_UNIT_TYPE)
    other = experiment(52, tag, (), (COLOR, SIZE))
    return ("Override On Unassigned Lowest Id Wins",
            f"(d) Payload order 52, 51, 50. Experiment 51 is on unit type {ABSENT_UNIT_TYPE}, which the context "
            f"lacks (unassigned), and is overridden to variant B. {COLOR} (51's B, 52's B) returns 51's B value, "
            f"exposing only 51 (overridden:true, assigned:false, variant 1): assigned OR overridden qualifies. "
            f"{SIZE} (50 guard, 51's A, 52's B) then falls through 50 and the overridden variant B, which lacks "
            f"it, to 52, exposing [50, 52]. " + GUARD_NOTE,
            [other, overridden, g], [override(overridden, 1), variable_value(COLOR), variable_value(SIZE), PENDING])


def spec_missing_key(tag: str) -> tuple:
    g = guard(60, tag)
    lacking = experiment(61, tag, (COLOR,), (SIZE,))
    other = experiment(62, tag, (), (COLOR,))
    return ("Assigned Lowest Id Whose Variant Lacks The Key Falls Through",
            f"(e) Payload order 62, 61, 60. Experiment 61 assigns the unit variant B, whose config lacks {COLOR} "
            f"(only 61's A defines it). variableValue({COLOR}) visits 61 (exposed), falls through and returns "
            f"62's B value; exposures [61, 62]. {SIZE} (60 guard, 61's B) then exposes 60 and returns 61's B "
            f"value. " + GUARD_NOTE,
            [other, lacking, g], [variable_value(COLOR), variable_value(SIZE), PENDING])


def spec_default(tag: str) -> tuple:
    absent = experiment(71, tag, (COLOR,), (COLOR,), unit_type=ABSENT_UNIT_TYPE)
    strict = experiment(72, tag, (COLOR,), (COLOR,), strict_mismatch=True)
    lacking = experiment(73, tag, (COLOR, SIZE), ())
    other = experiment(74, tag, (), (SIZE,))
    return ("Key Only In Unassigned Or Unserved Variants Returns Default, Every Visited Experiment Exposed",
            f"(g) Payload order 74, 73, 72, 71. {COLOR} is defined by 71 (unit type {ABSENT_UNIT_TYPE} absent, "
            f"unassigned), 72 (strict audience mismatch, unassigned) and 73's A (73 serves B, null config). "
            f"variableValue({COLOR}) returns the default and exposes 71, 72 and 73 in id order. {SIZE} (73's A, "
            f"74's B) then falls through 73 to 74 and exposes only 74.",
            [other, lacking, strict, absent],
            [MISMATCH_ATTRIBUTE, variable_value(COLOR), variable_value(SIZE), PENDING])


def peek_variant(spec: tuple) -> tuple:
    """The same context, every read first done with peekVariableValue: same values, no events,
    pending 0; then the variableValue reads, which must expose exactly as in the base scenario."""
    title, description, exps, steps = spec
    setup = [s for s in steps if s[0] not in READS and s is not PENDING]
    reads = [s for s in steps if s[0] in READS]
    peeks = [peek_variable_value(p["key"]) for _, p in reads]
    return (f"Peek - {title}",
            f"(f) peekVariableValue returns the same values as variableValue and queues nothing (pending stays "
            f"0); the variableValue reads that follow then expose exactly as without the peeks. Context: "
            + description,
            exps, setup + peeks + [PENDING] + reads + [PENDING])


def build_specs() -> list:
    base = [
        spec_lowest_id("lo", reverse=False),
        spec_lowest_id("rev", reverse=True),
        spec_unassigned("nounit", "unit"),
        spec_unassigned("aud", "audience"),
        spec_ineligible("inel"),
        spec_override("ovr"),
        spec_missing_key("nokey"),
        spec_default("dflt"),
    ]
    return base + [peek_variant(s) for s in base]


# ---------------------------------------------------------------------------
# JS reference run
# ---------------------------------------------------------------------------

def run_reference(specs: list) -> list:
    cases = [{"units": {PRESENT_UNIT_TYPE: UNIT}, "contextData": {"experiments": exps},
              "steps": [{"action": a, "params": p} for a, p in steps]}
             for _, _, exps, steps in specs]
    if not os.path.isdir(os.path.join(TOOL_DIR, "node_modules", "@absmartly", "javascript-sdk")):
        raise SystemExit("JS reference not installed: run `npm ci --prefix tools/shared-variable-keys`")
    proc = subprocess.run(["node", COMPUTE], input=json.dumps({"cases": cases}), capture_output=True,
                          text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"compute.js failed ({proc.returncode}):\n{proc.stderr}")
    out = json.loads(proc.stdout)
    if out["sdkVersion"] != JS_SDK_VERSION:
        raise SystemExit(f"JS reference is {out['sdkVersion']}, expected {JS_SDK_VERSION}")
    return out["cases"]


def ready_events(events: list) -> list:
    projected = []
    for event in events:
        if event["type"] != "ready":
            raise SystemExit(f"unexpected event during context creation: {event}")
        exps = [{"id": e["id"], "name": e["name"]} for e in event["data"]["experiments"]]
        projected.append({"type": "ready", "data": {"experiments": exps}})
    return projected


def build_scenario(spec: tuple, ran: dict) -> dict:
    title, description, exps, steps = spec
    if ran["ready"] != {"ready": True, "failed": False, "finalized": False}:
        raise SystemExit(f"{title}: JS context not ready: {ran['ready']}")
    out_steps = [{
        "action": "createContextWith",
        "params": {"units": {PRESENT_UNIT_TYPE: UNIT}, "options": {"publishDelay": -1, "refreshPeriod": 0}},
        "expect": {"result": ran["ready"], "events": ready_events(ran["readyEvents"])},
    }]
    for (action, params), got in zip(steps, ran["steps"]):
        for event in got["events"]:
            if event["type"] != "exposure":
                raise SystemExit(f"{title}: unexpected {event['type']} event from {action}")
        out_steps.append({"action": action, "params": params,
                          "expect": {"result": got["result"], "events": got["events"]}})
    return {
        "name": title,
        "description": description + f" Expected results recorded from @absmartly/javascript-sdk {JS_SDK_VERSION}.",
        "contextData": {"experiments": exps},
        "steps": out_steps,
    }


def build_group(first_number: int) -> list:
    specs = build_specs()
    titles = [s[0] for s in specs]
    if len(set(titles)) != len(titles):
        raise SystemExit("duplicate scenario titles")
    group = []
    for spec, ran in zip(specs, run_reference(specs)):
        scenario = build_scenario(spec, ran)
        scenario["name"] = f"{first_number + len(group)}{GROUP_MARKER}{scenario['name']}"
        group.append(scenario)
    return group


def write(scenarios: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Shared Variable Key scenario group.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    parser.add_argument("--file", default=CANONICAL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as src:
        raw = src.read()
    existing = json.loads(raw)
    kept = [s for s in existing if GROUP_MARKER not in s["name"]]
    group = build_group(len(kept) + 1)
    updated = kept + group
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if rendered != raw:
            print("test_scenarios_complete.json is stale: run generate_shared_variable_key_scenarios.py")
            return 1
        print(f"up to date: {len(group)} shared variable key scenarios")
        return 0

    with open(args.file, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(group)} shared variable key)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
