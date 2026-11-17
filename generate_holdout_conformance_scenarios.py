#!/usr/bin/env python3
"""Generate the Holdout Conformance group of test_scenarios_complete.json (H1-H8, 500+).

Scenarios 203-227 and 305 pin the core holdout contract. This group adds the cases every SDK holdout
port must also agree on before the fan-out starts:

  H1  a duplicate holdoutIds entry is one holdout (JS dedups at install): one holdout exposure, and a
      refresh that drops the duplicate is not a coverage change, so nothing re-exposes. Gated on
      `holdoutIdsDedup` as well, because java-sdk keeps the duplicate in its holdout-set identity.
  H2  a holdout with an empty or missing split is ignored, as if absent.
  H3  a same-iteration seed or split edit neither re-randomizes nor re-exposes a unit.
  H4  peek on a covered experiment queues no holdout exposure.
  H5  unknown fields inside a holdout entry are ignored, and so are the experiment-only fields a
      holdout never applies (traffic split, audience, full-on).
  H6  one holdout covering two experiments of different unit types publishes two holdout exposures,
      each with its own unit type's arm.
  H7  a strict audience mismatch on a covered, not-held-out experiment still fires the holdout.
  H8  a shared variable key whose first candidate is suppressed and second assigned returns the
      assigned value, and both holdouts fire.

Every expectation is recorded by running the steps through the javascript-sdk reference (the build
generate_sdk_round_2026_11_scenarios.py pins, via tools/sdk-round-2026-11/compute.js), not written by
hand. Holdout seeds were chosen with the reference's VariantAssigner for the fixed units below so each
case lands on the arm it is about (orchestrator/tests/test_holdout_conformance_scenarios.py replays
them with an independent model and checks it).

The group owns 500.. : 254-299 is left for the open #16/#20 renumbering, 300-399 is the parity range
and 400+ the Switchback group of cross-sdk-tests#13, which may still grow.

Usage:
    python3 generate_holdout_conformance_scenarios.py          # rewrite the group in place
    python3 generate_holdout_conformance_scenarios.py --check  # exit 1 if the canonical file is stale
"""

import argparse
import json
import os
import subprocess
import sys

import generate_sdk_round_2026_11_scenarios as round2611
import scenario_groups
from generate_sdk_round_2026_11_scenarios import (
    PENDING, attribute, audience_filter, experiment, peek, refresh, treatment, variable_value,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")

FIRST_NUMBER = scenario_groups.HOLDOUT_CONFORMANCE_FIRST_NUMBER
MARKER = scenario_groups.HOLDOUT_CONFORMANCE

HOLDOUTS_CAPABILITY = "holdouts"
DEDUP_CAPABILITY = "holdoutIdsDedup"

UNIT = {"session_id": "svk-unit-1"}
# With split [0.5, 0.5]: svk-unit-1 lands on arm 0 (held out) with HELD and on arm 1 with NOT_HELD;
# svk-user-1 lands on arm 1 with HELD, so one holdout splits the two unit types (H6).
HELD = (1, 2)
NOT_HELD = (1, 1)
# Not held out for svk-user-1 (arm 1 of [0.5, 0.5]).
USER_NOT_HELD = ((1, 2), (1, 4))
# svk-unit-1 lands on arm 1 of split [0.2, 0.8] with NOT_HELD's seeds: the edit H3 makes to a held
# unit's holdout would move it out of the holdout if the SDK re-randomized.
EDITED_SPLIT = [0.2, 0.8]


def holdout(holdout_id, name, seed, *, split=None, unit_type="session_id", **extra):
    """The slim ContextHoldout shape the Collector serves (abs ContextAPI.getContextHoldout)."""
    entry = {"id": holdout_id, "name": name, "unitType": unit_type, "iteration": 1,
             "seedHi": seed[0], "seedLo": seed[1], "split": [0.5, 0.5] if split is None else split}
    entry.update(extra)
    return entry


def covered(exp, *holdout_ids):
    return dict(exp, holdoutIds=list(holdout_ids))


def configs(prefix):
    return [json.dumps({"color": f"{prefix}{i}"}, separators=(",", ":")) for i in range(2)]


def spec(title, description, experiments, holdouts, steps, *, units=None, requires=None,
         unordered_steps=()):
    """`unordered_steps`: indexes into `steps` whose exposures are pinned as a set
    (`unorderedEvents`), for one call that queues several exposures in an SDK-specific order."""
    return {"title": title, "description": description, "experiments": experiments,
            "holdouts": holdouts, "steps": steps, "units": units or UNIT,
            "requires": requires or [HOLDOUTS_CAPABILITY], "unordered_steps": set(unordered_steps)}


def specs():
    out = []

    dup = covered(experiment(501, "exp_hc_dup_ids"), 7001, 7001)
    dup_held = covered(experiment(502, "exp_hc_dup_ids_held"), 7002, 7002, 7002)
    # Its own unit type, so its holdouts' exposures are not already spent by the two above.
    dup_apart = covered(dict(experiment(503, "exp_hc_dup_ids_apart"), unitType="user_id"),
                        7003, 7004, 7003)
    dup_holdouts = [holdout(7001, "hc_dup_not_held", NOT_HELD), holdout(7002, "hc_dup_held", HELD),
                    holdout(7003, "hc_dup_apart_a", USER_NOT_HELD[0]),
                    holdout(7004, "hc_dup_apart_b", USER_NOT_HELD[1])]
    out.append(spec(
        "Duplicate holdoutIds Entries Are One Holdout",
        "holdoutIds lists the same holdout twice, three times, and twice apart ([7003, 7004, 7003]). "
        "The SDK dedups at install, as javascript-sdk does: treatment fires exactly one exposure per "
        "holdout, alongside each not-held-out experiment's own exposure and with the held-out one "
        "suppressed. A refresh that drops the duplicates is not a coverage change, so the covered "
        "experiments keep their exposure state and nothing re-exposes. Not yet the java-sdk "
        "behaviour: it keeps duplicates in the holdout-set identity, so the refresh re-exposes "
        "(gated on holdoutIdsDedup until the owner confirms this contract and java-sdk follows).",
        [dup, dup_held, dup_apart], dup_holdouts,
        [treatment("exp_hc_dup_ids"), treatment("exp_hc_dup_ids_held"),
         treatment("exp_hc_dup_ids_apart"),
         refresh({"experiments": [covered(dup, 7001), covered(dup_held, 7002),
                                  covered(dup_apart, 7003, 7004)],
                  "holdouts": dup_holdouts}),
         treatment("exp_hc_dup_ids"), treatment("exp_hc_dup_ids_held"),
         treatment("exp_hc_dup_ids_apart"), PENDING],
        units={"session_id": "svk-unit-1", "user_id": "svk-user-1"},
        requires=[HOLDOUTS_CAPABILITY, DEDUP_CAPABILITY]))

    empty = holdout(7101, "hc_empty_split", HELD, split=[])
    missing = holdout(7102, "hc_missing_split", HELD)
    del missing["split"]
    out.append(spec(
        "A Holdout With An Empty Or Missing Split Is Ignored",
        "Holdouts 7101 (split []) and 7102 (no split) cannot assign an arm, so they are dropped at "
        "install exactly like a dangling id: the experiment covered only by them assigns normally "
        "with no holdout exposure, and the one also covered by a valid not-held-out holdout fires "
        "only that holdout.",
        [covered(experiment(511, "exp_hc_splitless"), 7101, 7102),
         covered(experiment(512, "exp_hc_splitless_and_valid"), 7101, 7102, 7103)],
        [empty, missing, holdout(7103, "hc_valid", NOT_HELD)],
        [treatment("exp_hc_splitless"), treatment("exp_hc_splitless_and_valid"), PENDING]))

    held, not_held = holdout(7201, "hc_reseed_held", HELD), holdout(7202, "hc_reseed_not_held", NOT_HELD)
    reseed = [covered(experiment(521, "exp_hc_reseed_held"), 7201),
              covered(experiment(522, "exp_hc_reseed_not_held"), 7202),
              covered(experiment(523, "exp_hc_reseed_sibling"), 7201)]
    out.append(spec(
        "A Same-Iteration Seed Or Split Edit Does Not Re-Randomize Or Re-Expose",
        "The unit is held out by 7201 and not by 7202. A refresh keeps both iterations but swaps "
        "their seeds and changes 7201's split to [0.2, 0.8], under which a fresh draw would flip "
        "both verdicts. The arms stay pinned: the held-out experiment stays suppressed, the other "
        "keeps its variant, a sibling first evaluated after the refresh is still held out by 7201's "
        "original arm, and nothing new is queued.",
        reseed, [held, not_held],
        [treatment("exp_hc_reseed_held"), treatment("exp_hc_reseed_not_held"),
         refresh({"experiments": reseed,
                  "holdouts": [dict(held, seedHi=NOT_HELD[0], seedLo=NOT_HELD[1], split=EDITED_SPLIT),
                               dict(not_held, seedHi=HELD[0], seedLo=HELD[1])]}),
         treatment("exp_hc_reseed_held"), treatment("exp_hc_reseed_not_held"),
         treatment("exp_hc_reseed_sibling"), PENDING]))

    out.append(spec(
        "Peek On A Covered Experiment Queues No Holdout Exposure",
        "peek on a held-out and a not-held-out covered experiment returns their variants (0 and 1) "
        "and queues nothing, holdout exposures included. A later treatment still fires every "
        "exposure, so peek did not mark anything exposed.",
        [covered(experiment(531, "exp_hc_peek_held"), 7301),
         covered(experiment(532, "exp_hc_peek_not_held"), 7302)],
        [holdout(7301, "hc_peek_held", HELD), holdout(7302, "hc_peek_not_held", NOT_HELD)],
        [peek("exp_hc_peek_held"), peek("exp_hc_peek_not_held"), PENDING,
         treatment("exp_hc_peek_held"), treatment("exp_hc_peek_not_held"), PENDING]))

    out.append(spec(
        "Unknown Fields Inside A Holdout Entry Are Ignored",
        "Each holdout carries fields an SDK does not know (an object, a list, a number, a string) "
        "and experiment fields a holdout never applies: trafficSplit [1, 0], a strict audience the "
        "unit misses and fullOnVariant 1. All of them are ignored: 7401 still holds the unit out "
        "and 7402 still does not, each from its seed and split alone.",
        [covered(experiment(541, "exp_hc_unknown_held"), 7401),
         covered(experiment(542, "exp_hc_unknown_not_held"), 7402)],
        [holdout(7400 + i, f"hc_unknown_{label}", seed,
                 futureHoldoutSettings={"nested": [1, 2], "mode": "x"}, exclusionIds=[9, 10],
                 holdoutVersion=3, rolloutStage="beta", trafficSeedHi=5, trafficSeedLo=6,
                 trafficSplit=[1, 0], audienceStrict=True,
                 audience=audience_filter({"eq": [{"var": "country"}, {"value": "US"}]}),
                 fullOnVariant=1)
         for i, (label, seed) in ((1, ("held", HELD)), (2, ("not_held", NOT_HELD)))],
        [treatment("exp_hc_unknown_held"), treatment("exp_hc_unknown_not_held"), PENDING]))

    out.append(spec(
        "One Holdout Over Two Unit Types Publishes Two Holdout Exposures",
        "Holdout 7501 covers a session_id experiment and a user_id experiment. Its arm is drawn per "
        "covered unit type and cached per (holdout, unit type): the session unit is held out (arm "
        "0) and the user unit is not (arm 1), so the first experiment is suppressed, the second "
        "assigns normally, and the publish carries two 7501 exposures with different units and "
        "arms.",
        [covered(experiment(551, "exp_hc_session"), 7501),
         covered(dict(experiment(552, "exp_hc_user"), unitType="user_id"), 7501)],
        [holdout(7501, "hc_two_unit_types", HELD)],
        [treatment("exp_hc_session"), treatment("exp_hc_user"), ("publish", {})],
        units={"session_id": "svk-unit-1", "user_id": "svk-user-1"}))

    strict_us = audience_filter({"eq": [{"var": "country"}, {"value": "US"}]})
    out.append(spec(
        "A Strict Audience Mismatch On A Covered Experiment Still Fires The Holdout",
        "The unit (country CA) misses a strict US audience on two covered experiments. Not held "
        "out by 7601, the first is evaluated and rejected by its audience (variant 0, "
        "assigned:false, audienceMismatch:true) and its exposure and 7601's both fire. Held out by "
        "7602, the second is suppressed before the audience is considered: no exposure of its own, "
        "7602's fires.",
        [covered(experiment(561, "exp_hc_strict_not_held", audience=strict_us, audience_strict=True), 7601),
         covered(experiment(562, "exp_hc_strict_held", audience=strict_us, audience_strict=True), 7602)],
        [holdout(7601, "hc_strict_not_held", NOT_HELD), holdout(7602, "hc_strict_held", HELD)],
        [attribute("country", "CA"), treatment("exp_hc_strict_not_held"),
         treatment("exp_hc_strict_held"), PENDING]))

    out.append(spec(
        "Shared Variable Key: Suppressed First Candidate Loses, Both Holdouts Fire",
        "Experiments 571 (held out by 7701) and 572 (not held out by 7702) both define color; 571 "
        "is visited first. peekVariableValue returns 572's assigned value and queues nothing. "
        "variableValue returns it too and fires 7701's exposure for the suppressed candidate it "
        "visited, 572's own exposure and 7702's, in any order: javascript-sdk queues each "
        "candidate's own exposure before its holdouts, java-sdk every visited candidate's holdouts "
        "first, and neither order means anything to the Collector. Treatment on either afterwards "
        "queues nothing.",
        [covered(experiment(571, "exp_hc_key_suppressed", configs=configs("first_")), 7701),
         covered(experiment(572, "exp_hc_key_assigned", configs=configs("second_")), 7702)],
        [holdout(7701, "hc_key_held", HELD), holdout(7702, "hc_key_not_held", NOT_HELD)],
        [("peekVariableValue", {"key": "color", "defaultValue": "default"}), PENDING,
         variable_value("color", "default"), treatment("exp_hc_key_suppressed"),
         treatment("exp_hc_key_assigned"), PENDING],
        unordered_steps=[2]))
    return out


# ---------------------------------------------------------------------------
# JS reference run
# ---------------------------------------------------------------------------

def run_reference(group):
    cases = [{"units": s["units"],
              "contextData": {"experiments": s["experiments"], "holdouts": s["holdouts"]},
              "steps": [{"action": a, "params": p} for a, p in s["steps"]]} for s in group]
    env = dict(os.environ, SDK_PATH=round2611.SDK_PATH)
    proc = subprocess.run(["node", round2611.COMPUTE], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"compute.js failed ({proc.returncode}):\n{proc.stderr}")
    out = json.loads(proc.stdout)
    if out["sdkVersion"] != round2611.JS_SDK_VERSION:
        raise SystemExit(f"JS reference is {out['sdkVersion']}, expected {round2611.JS_SDK_VERSION}")
    if len(out["cases"]) != len(group):
        raise SystemExit("JS reference returned an incomplete scenario list")
    return out["cases"]


def publish_events(title, events):
    """Project a publish to its exposures (id, name, unit, variant), as the hand-written holdout
    scenarios do; the rest of the request is SDK plumbing this group does not pin."""
    projected = []
    for event in events:
        if event["type"] != "publish":
            raise SystemExit(f"{title}: unexpected {event['type']} event from publish")
        exposures = [{k: e[k] for k in ("id", "name", "unit", "variant")}
                     for e in event["data"]["exposures"]]
        projected.append({"type": "publish", "data": {"exposures": exposures}})
    return projected


def build_scenario(s, ran, number):
    title = s["title"]
    if ran["ready"] != {"ready": True, "failed": False, "finalized": False}:
        raise SystemExit(f"{title}: JS context not ready: {ran['ready']}")
    if len(ran["steps"]) != len(s["steps"]):
        raise SystemExit(f"{title}: JS reference returned an incomplete step list")
    out_steps = [{"action": "createContextWith",
                  "params": {"units": s["units"], "options": {"publishDelay": -1, "refreshPeriod": 0}},
                  "expect": {"result": ran["ready"],
                             "events": round2611.ready_events(ran["readyEvents"])}}]
    for index, ((action, params), got) in enumerate(zip(s["steps"], ran["steps"])):
        if action == "refresh":
            events = round2611.refresh_events(got["events"])
        elif action == "publish":
            events = publish_events(title, got["events"])
        else:
            events = got["events"]
            for event in events:
                if event["type"] != "exposure":
                    raise SystemExit(f"{title}: unexpected {event['type']} event from {action}")
                # No group experiment has assignment rules, and SDKs without them never emit the
                # flag; scenario 305 pins rules with holdouts.
                event["data"].pop("ruleOverride", None)
        expect = {"result": got["result"], "events": events}
        if index in s["unordered_steps"]:
            expect["unorderedEvents"] = True
        out_steps.append({"action": action, "params": params, "expect": expect})
    return {
        "name": f"{number}{MARKER}{title}",
        "description": f"{s['description']} Expected results recorded from "
                       f"@absmartly/javascript-sdk {round2611.JS_SDK_VERSION} (javascript-sdk main).",
        "contextData": {"experiments": s["experiments"], "holdouts": s["holdouts"]},
        "steps": out_steps,
        "requires": s["requires"],
    }


def build_group(first_number):
    round2611.ensure_reference_build()
    group = specs()
    titles = [s["title"] for s in group]
    if len(set(titles)) != len(titles):
        raise SystemExit("duplicate scenario titles in the Holdout Conformance group")
    return [build_scenario(s, ran, first_number + i)
            for i, (s, ran) in enumerate(zip(group, run_reference(group)))]


def assemble(existing, build):
    """The canonical file with the group rebuilt at FIRST_NUMBER.., in number order among the other
    scenarios. Another scenario numbered inside the rebuilt range is a collision, never renumbered."""
    rest = [s for s in existing if MARKER not in s["name"]]
    before = [s for s in rest if scenario_groups.number(s) < FIRST_NUMBER]
    after = [s for s in rest if scenario_groups.number(s) >= FIRST_NUMBER]
    group = build(FIRST_NUMBER)
    last = scenario_groups.number(group[-1])
    clashes = sorted(scenario_groups.number(s) for s in after if scenario_groups.number(s) <= last)
    if clashes:
        raise SystemExit(f"scenarios {clashes} collide with the Holdout Conformance range "
                         f"{FIRST_NUMBER}-{last}")
    return before + group + after, group


def main():
    parser = argparse.ArgumentParser(description="Generate the Holdout Conformance scenario group.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    parser.add_argument("--file", default=CANONICAL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as src:
        raw = src.read()
    updated, group = assemble(json.loads(raw), build_group)
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if rendered != raw:
            print("test_scenarios_complete.json is stale: run generate_holdout_conformance_scenarios.py")
            return 1
        print(f"up to date: {len(group)} Holdout Conformance scenarios")
        return 0

    with open(args.file, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(group)} Holdout Conformance)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
