#!/usr/bin/env python3
"""Generate the Switchback scenario group of test_scenarios_complete.json.

The group is derived, never hand-edited:

  * Golden-vector scenarios drive every case in fixtures/switchback/golden-vectors.json (a
    byte-for-byte copy of the platform's lib/analytics/src/test/resources/switchback/
    golden-vectors.json) through the wrapper's /switchback/assign and /switchback/permutation
    endpoints.
  * Context scenarios exercise switchback serving through createContextWith/treatment/publish.
    Their expected variants come from the reference below applied to the SDK's hashed unit,
    and the reference must reproduce every golden vector before anything is written.
  * The inertness scenario is ungated: every SDK, switchback-aware or not, must serve its unit
    experiments unchanged when switchback experiments and fields are present in the payload.

Usage:
    python3 generate_switchback_scenarios.py          # rewrite the group in place
    python3 generate_switchback_scenarios.py --check  # exit 1 if the canonical file is stale

Scenarios whose name carries GROUP_MARKER are replaced; every other scenario is left untouched
and the file is serialized exactly as before (indent=2, ensure_ascii=False, trailing newline).
"""

import argparse
import base64
import hashlib
import json
import math
import os
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")
GOLDEN = os.path.join(HERE, "fixtures", "switchback", "golden-vectors.json")

GROUP_MARKER = " - Switchback "
CAPABILITY = "switchback"

ASSIGN_CASES = 882
PERMUTATION_CASES = 504

# ---------------------------------------------------------------------------
# Reference implementation (independent of any SDK)
# ---------------------------------------------------------------------------

MASK32 = 0xFFFFFFFF
MASK64 = (1 << 64) - 1
NORMALIZER = 1.0 / 0xFFFFFFFF
SEPARATOR = "\u001f"


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86_32, unsigned result."""

    def rotl(x, r):
        return ((x << r) | (x >> (32 - r))) & MASK32

    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & MASK32
    body = len(data) - len(data) % 4
    for i in range(0, body, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = rotl((k * c1) & MASK32, 15)
        h ^= (k * c2) & MASK32
        h = (rotl(h, 13) * 5 + 0xE6546B64) & MASK32
    tail = data[body:]
    if tail:
        k = int.from_bytes(tail, "little")
        k = rotl((k * c1) & MASK32, 15)
        h ^= (k * c2) & MASK32
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & MASK32
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & MASK32
    h ^= h >> 16
    return h


def le32(v: int) -> bytes:
    return (v & MASK32).to_bytes(4, "little")


def le64(v: int) -> bytes:
    return (v & MASK64).to_bytes(8, "little")


def hash_unit(uid: str) -> str:
    """The SDK's unit hash: base64url (no padding) of MD5(utf8(uid)), also the published uid."""
    digest = hashlib.md5(uid.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def base_hash(cluster_unit: str, cycle_index: int, seed: int) -> int:
    key = f"{cluster_unit}{SEPARATOR}{cycle_index}".encode("utf-8")
    return murmur3_32(le64(seed) + le32(murmur3_32(key)))


def permutation(arm_count: int, base: int) -> list:
    if arm_count < 2:
        raise ValueError(f"armCount must be >= 2, got {arm_count}")
    perm = list(range(arm_count))
    for i in range(arm_count - 1, 0, -1):
        step = murmur3_32(le32(base) + le32(i))
        j = min(i, math.floor(step * NORMALIZER * (i + 1)))
        perm[i], perm[j] = perm[j], perm[i]
    return perm


def assign_switchback(cluster_unit: str, period_index: int, seed: int, arm_count: int) -> int:
    # Python's // and % are floorDiv/floorMod for a positive divisor, exact on 64-bit values.
    cycle, slot = period_index // arm_count, period_index % arm_count
    return permutation(arm_count, base_hash(cluster_unit, cycle, seed))[slot]


def experiment_seed(seed_hi: int, seed_lo: int) -> int:
    """(seedHi << 32) | (seedLo & 0xffffffff) as a signed 64-bit value."""
    unsigned = ((seed_hi & MASK32) << 32) | (seed_lo & MASK32)
    return unsigned - (1 << 64) if unsigned >> 63 else unsigned


def unit_variant(uid: str, split: list, seed_hi: int, seed_lo: int) -> int:
    """The SDK's ordinary (sticky) unit assignment, used for unit experiments and to prove the
    switchback expectations differ from what an SDK ignoring randomizationType would serve."""
    unit_hash = murmur3_32(hash_unit(uid).encode("ascii"))
    prob = murmur3_32(le32(seed_lo) + le32(seed_hi) + le32(unit_hash)) * NORMALIZER
    cumulative = 0.0
    for i, weight in enumerate(split):
        cumulative += weight
        if prob < cumulative:
            return i
    return len(split) - 1


def cluster_unit_text(value) -> str:
    """The platform hashes a numeric cluster unit as its decimal string; strings pass through."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"clusterUnit must be a string or integer: {value!r}")
    return str(value)


def exact_long(value) -> int:
    """64-bit fixture fields are decimal strings; a JSON number would mean a lossy fixture."""
    if not isinstance(value, str):
        raise ValueError(f"64-bit field must be a decimal string: {value!r}")
    parsed = int(value, 10)
    if not -(1 << 63) <= parsed < (1 << 63):
        raise ValueError(f"64-bit field out of range: {value}")
    return parsed


def load_golden() -> dict:
    with open(GOLDEN, encoding="utf-8") as src:
        golden = json.load(src)
    if len(golden["assignSwitchback"]) != ASSIGN_CASES:
        raise SystemExit(f"expected {ASSIGN_CASES} assignSwitchback cases")
    if len(golden["fisherYatesPermutation"]) != PERMUTATION_CASES:
        raise SystemExit(f"expected {PERMUTATION_CASES} fisherYatesPermutation cases")
    return golden


def verify_reference(golden: dict) -> None:
    for case in golden["assignSwitchback"]:
        actual = assign_switchback(cluster_unit_text(case["clusterUnit"]), exact_long(case["periodIndex"]),
                                   exact_long(case["seed"]), case["armCount"])
        if actual != case["expectedVariant"]:
            raise SystemExit(f"reference disagrees with golden vector {case}: got {actual}")
    for case in golden["fisherYatesPermutation"]:
        base = base_hash(cluster_unit_text(case["clusterUnit"]), exact_long(case["cycleIndex"]),
                         exact_long(case["seed"]))
        actual = permutation(case["armCount"], base)
        if actual != case["expectedPermutation"]:
            raise SystemExit(f"reference disagrees with golden vector {case}: got {actual}")


# ---------------------------------------------------------------------------
# Scenario construction
# ---------------------------------------------------------------------------


def golden_scenarios(golden: dict) -> list:
    """One scenario per (function, armCount): 294 assign and 168 permutation steps each."""
    scenarios = []
    for arm_count in (2, 3, 10):
        cases = [c for c in golden["assignSwitchback"] if c["armCount"] == arm_count]
        steps = [{
            "action": "switchbackAssign",
            "params": {
                "clusterUnit": cluster_unit_text(c["clusterUnit"]),
                "periodIndex": c["periodIndex"],
                "seed": c["seed"],
                "armCount": arm_count,
            },
            "expect": {"result": c["expectedVariant"], "events": []},
        } for c in cases]
        scenarios.append({
            "name": f"Golden Vectors - assignSwitchback - {arm_count} Arms",
            "description": (f"Every {arm_count}-arm assignSwitchback case ({len(steps)}) of the platform golden "
                            "vectors, driven through POST /switchback/assign. 64-bit values travel as decimal "
                            "strings; integer cluster units are sent as their decimal string."),
            "contextData": {"experiments": []},
            "steps": steps,
            "requires": [CAPABILITY],
        })
    for arm_count in (2, 3, 10):
        cases = [c for c in golden["fisherYatesPermutation"] if c["armCount"] == arm_count]
        steps = [{
            "action": "switchbackPermutation",
            "params": {
                "clusterUnit": cluster_unit_text(c["clusterUnit"]),
                "cycleIndex": c["cycleIndex"],
                "seed": c["seed"],
                "armCount": arm_count,
            },
            "expect": {"result": c["expectedPermutation"], "orderedResult": True, "events": []},
        } for c in cases]
        scenarios.append({
            "name": f"Golden Vectors - fisherYatesPermutation - {arm_count} Arms",
            "description": (f"Every {arm_count}-arm fisherYatesPermutation case ({len(steps)}) of the platform "
                            "golden vectors, driven through POST /switchback/permutation. The permutation is "
                            "compared positionally."),
            "contextData": {"experiments": []},
            "steps": steps,
            "requires": [CAPABILITY],
        })
    return scenarios


# Clock-free grids. Wrappers have no clock control, so every grid below keeps "now" inside one
# known period from the time of writing until 2069-08-18T16:00Z (ANCHOR_PERIOD_0 + PERIOD_MS).
HOUR_MS = 3_600_000
PERIOD_MS = 400_000 * HOUR_MS                   # 1,440,000,000,000 ms, about 45.6 years
ANCHOR_PERIOD_0 = 1_704_067_200_000             # 2024-01-01T00:00Z: now is in period 0
ANCHOR_PERIOD_1 = ANCHOR_PERIOD_0 - PERIOD_MS   # 1978-05-15T08:00Z: now is in period 1
ANCHOR_FUTURE = 4_102_444_800_000               # 2100-01-01T00:00Z: now is before the anchor

# Per arm count: (seedHi, seedLo, split). Seeds cover a positive, a negative-hi and an all-ones-lo
# 64-bit seed; only split.length matters to the switchback draw.
ARMS = {
    2: (3603515, 233373850, [0.5, 0.5]),
    3: (-559038737, 1234567890, [0.34, 0.33, 0.33]),
    10: (2147483647, -1, [0.1] * 10),
}

EXPOSURE_KEYS = ("id", "name", "unit", "variant", "exposedAt", "assigned", "eligible", "overridden",
                 "fullOn", "custom", "audienceMismatch")


def switchback_variant(uid: str, period: int, arm_count: int) -> int:
    seed_hi, seed_lo, _ = ARMS[arm_count]
    return assign_switchback(hash_unit(uid), period, experiment_seed(seed_hi, seed_lo), arm_count)


def pick_uid(prefix: str, period: int, arm_count: int) -> str:
    """First '<prefix>-<k>' whose switchback variant is nonzero and differs from the sticky unit
    variant, so neither an ineligible fallback nor plain unit assignment can pass by accident."""
    seed_hi, seed_lo, split = ARMS[arm_count]
    for k in range(10_000):
        uid = f"{prefix}-{k}"
        variant = switchback_variant(uid, period, arm_count)
        if variant != 0 and variant != unit_variant(uid, split, seed_hi, seed_lo):
            return uid
    raise SystemExit(f"no discriminating uid for {prefix}")


def experiment(exp_id: int, name: str, unit_type: str, arm_count: int, **overrides) -> dict:
    seed_hi, seed_lo, split = ARMS[arm_count]
    data = {
        "id": exp_id,
        "name": name,
        "iteration": 1,
        "unitType": unit_type,
        "seedHi": seed_hi,
        "seedLo": seed_lo,
        "split": list(split),
        "trafficSeedHi": 1,
        "trafficSeedLo": 2,
        "trafficSplit": [0, 1],
        "fullOnVariant": 0,
        "applications": [{"name": "website"}],
        "variants": [{"name": chr(ord("A") + i), "config": None} for i in range(arm_count)],
        "audience": None,
        "audienceStrict": False,
        "customFieldValues": None,
    }
    data.update(overrides)
    return data


def switchback_experiment(exp_id: int, name: str, unit_type: str, arm_count: int, anchor, **overrides) -> dict:
    fields = {
        "randomizationType": "switchback",
        "switchbackClusterUnitType": unit_type,
        "switchbackPeriodMs": PERIOD_MS,
        "switchbackCarryoverDurationMs": 0,
        "switchbackBurnInDurationMs": 0,
        "switchbackBurnOutDurationMs": 0,
        "switchbackDesignType": "balanced_block",
        "switchbackEstimand": "global_average_treatment_effect",
    }
    if anchor is not None:
        fields["switchbackStartAt"] = anchor
    fields.update(overrides)
    return experiment(exp_id, name, unit_type, arm_count, **fields)


def exposure(exp: dict, variant: int, assigned=True, eligible=True, overridden=False, full_on=False) -> dict:
    return {
        "id": exp["id"],
        "name": exp["name"],
        "unit": exp["unitType"],
        "variant": variant,
        "assigned": assigned,
        "eligible": eligible,
        "overridden": overridden,
        "fullOn": full_on,
        "custom": False,
        "audienceMismatch": False,
    }


def create_step(experiments: list, units: dict) -> dict:
    return {
        "action": "createContextWith",
        "params": {"units": units, "options": {"publishDelay": -1, "refreshPeriod": 0}},
        "expect": {
            "result": {"ready": True, "failed": False, "finalized": False},
            "events": [{"type": "ready", "data": {"experiments": [{"id": e["id"], "name": e["name"]} for e in experiments]}}],
        },
    }


def treatment_step(exp: dict, result: int, exposed: Optional[dict] = None) -> dict:
    events = [] if exposed is None else [{"type": "exposure", "data": exposed}]
    return {"action": "treatment", "params": {"experimentName": exp["name"]}, "expect": {"result": result, "events": events}}


def publish_step(exposures: list) -> dict:
    summary = [{k: e[k] for k in ("id", "name", "variant")} for e in exposures]
    return {"action": "publish", "params": {}, "expect": {"result": None, "events": [{"type": "publish", "data": {"exposures": summary}}]}}


def context_scenario(name: str, description: str, experiments: list, units: dict, steps: list) -> dict:
    return {
        "name": name,
        "description": description,
        "contextData": {"experiments": experiments},
        "steps": [create_step(experiments, units)] + steps,
        "requires": [CAPABILITY],
    }


def period_scenarios(_golden: dict) -> list:
    scenarios = []
    for period, anchor in ((0, ANCHOR_PERIOD_0), (1, ANCHOR_PERIOD_1)):
        for arm_count in (2, 3, 10):
            experiments, units, steps, exposed = [], {}, [], []
            for slot, letter in enumerate("abc"):
                unit_type = f"store_{letter}"
                uid = pick_uid(f"p{period}-a{arm_count}-{letter}", period, arm_count)
                exp = switchback_experiment(100 + slot, f"exp_switchback_{letter}", unit_type, arm_count, anchor)
                variant = switchback_variant(uid, period, arm_count)
                experiments.append(exp)
                units[unit_type] = uid
                exposed.append(exposure(exp, variant))
                steps.append(treatment_step(exp, variant, exposed[-1]))
            steps.append(publish_step(exposed))
            scenarios.append(context_scenario(
                f"Period {period} - {arm_count} Arms - Three Clusters",
                (f"switchbackStartAt {anchor} with switchbackPeriodMs {PERIOD_MS} (400,000 h) puts now in period "
                 f"{period} until 2069. Each cluster's variant is assignSwitchback(hashUnit(uid), {period}, "
                 f"(seedHi << 32) | seedLo, {arm_count}); every uid is chosen so that variant is nonzero and differs "
                 "from the sticky unit assignment."),
                experiments, units, steps))
    return scenarios


def eligibility_scenarios(_golden: dict) -> list:
    pre_anchor = switchback_experiment(110, "exp_switchback_pre_anchor", "store_id", 3, ANCHOR_FUTURE)
    no_anchor = switchback_experiment(111, "exp_switchback_no_anchor", "store_id", 3, None)
    no_unit = switchback_experiment(112, "exp_switchback_no_cluster_unit", "warehouse_id", 3, ANCHOR_PERIOD_0)
    uid = pick_uid("eligibility", 0, 3)

    def ineligible(exp, description):
        return context_scenario(
            f"Ineligible - {exp['name']}",
            description,
            [exp],
            {"store_id": uid},
            [
                treatment_step(exp, 0, exposure(exp, 0, eligible=False)),
                treatment_step(exp, 0),
                publish_step([exposure(exp, 0, eligible=False)]),
            ])

    return [
        ineligible(pre_anchor, (
            f"switchbackStartAt {ANCHOR_FUTURE} (2100-01-01T00:00Z) is after now, so the experiment is "
            "ineligible: variant 0 with assigned=true, eligible=false, exposed once and never drawn from "
            "split/seed.")),
        ineligible(no_anchor, (
            "switchbackStartAt is absent (an experiment that has not started), so the grid cannot be "
            "evaluated: variant 0 with assigned=true, eligible=false, exposed once.")),
        context_scenario(
            "Unassigned - Cluster Unit Not Set",
            ("The context has no warehouse_id unit, so the switchback experiment is unassigned: variant 0 "
             "with assigned=false and eligible=true, and no fallback to any other unit."),
            [no_unit],
            {"store_id": uid},
            [
                treatment_step(no_unit, 0, exposure(no_unit, 0, assigned=False)),
                publish_step([exposure(no_unit, 0, assigned=False)]),
            ]),
    ]


def serving_scenarios(_golden: dict) -> list:
    uid = pick_uid("serving", 0, 3)
    variant = switchback_variant(uid, 0, 3)
    dedup = switchback_experiment(120, "exp_switchback_dedup", "store_id", 3, ANCHOR_PERIOD_0)
    full_on = switchback_experiment(121, "exp_switchback_full_on", "store_id", 3, ANCHOR_PERIOD_0, fullOnVariant=2)
    overridden = switchback_experiment(122, "exp_switchback_override", "store_id", 3, ANCHOR_PERIOD_0)
    override_variant = next(v for v in (1, 2) if v != variant)
    payload = switchback_experiment(123, "exp_switchback_payload", "store_id", 3, ANCHOR_PERIOD_0)
    baseline = experiment(124, "exp_switchback_payload_baseline", "store_id", 3)
    seed_hi, seed_lo, split = ARMS[3]
    baseline_variant = unit_variant(uid, split, seed_hi, seed_lo)
    units_published = [{"type": "store_id", "uid": hash_unit(uid)}]

    return [
        context_scenario(
            "Same Period - Exactly One Exposure",
            "Two getTreatment calls within one period return the same variant and expose once.",
            [dedup],
            {"store_id": uid},
            [
                treatment_step(dedup, variant, exposure(dedup, variant)),
                treatment_step(dedup, variant),
                publish_step([exposure(dedup, variant)]),
            ]),
        context_scenario(
            "Full-On Serves The Full-On Variant",
            "fullOnVariant=2 takes precedence over the switchback draw: variant 2 with fullOn=true.",
            [full_on],
            {"store_id": uid},
            [
                treatment_step(full_on, 2, exposure(full_on, 2, full_on=True)),
                publish_step([exposure(full_on, 2, full_on=True)]),
            ]),
        context_scenario(
            "Override Wins Over The Switchback Draw",
            (f"The period-0 draw for this cluster is {variant}; an override to {override_variant} is served "
             "instead with assigned=false and overridden=true."),
            [overridden],
            {"store_id": uid},
            [
                {"action": "override", "params": {"experimentName": overridden["name"], "variant": override_variant},
                 "expect": {"result": None, "events": []}},
                treatment_step(overridden, override_variant,
                               exposure(overridden, override_variant, assigned=False, overridden=True)),
                publish_step([exposure(overridden, override_variant, assigned=False, overridden=True)]),
            ]),
        context_scenario(
            "Exposure And Publish Payload Carry No New Fields",
            ("A unit experiment on the same cluster unit is exposed and published first as the baseline. The "
             "switchback exposure and its publish event must then have exactly the baseline's fields at every "
             "depth (sameShapeAs), with every exposure field value pinned, so a switchback-only field such as a "
             "period index fails the scenario."),
            [payload, baseline],
            {"store_id": uid},
            [
                treatment_step(baseline, baseline_variant, exposure(baseline, baseline_variant)),
                {"action": "publish", "params": {}, "expect": {"result": None, "events": [{"type": "publish", "data": {
                    "hashed": True, "units": units_published,
                    "exposures": [exposure(baseline, baseline_variant)]}}]}},
                {"action": "treatment", "params": {"experimentName": payload["name"]},
                 "expect": {"result": variant, "events": [
                     {"type": "exposure", "sameShapeAs": 1, "data": exposure(payload, variant)}]}},
                {"action": "publish", "params": {}, "expect": {"result": None, "events": [
                    {"type": "publish", "sameShapeAs": 2, "data": {
                        "hashed": True, "units": units_published, "exposures": [exposure(payload, variant)]}}]}},
            ]),
    ]


def inertness_scenarios(_golden: dict) -> list:
    """Ungated: must pass on every SDK. Unit experiments are served exactly as without any
    switchback data; the switchback experiment itself is never queried, because a non-capable
    SDK serves it a sticky unit assignment whose value differs per SDK."""
    seed_hi, seed_lo, split = ARMS[2]
    sb_fields = {
        "switchbackClusterUnitType": "session_id",
        "switchbackStartAt": ANCHOR_PERIOD_0,
        "switchbackPeriodMs": PERIOD_MS,
        "switchbackCarryoverDurationMs": 0,
        "switchbackBurnInDurationMs": 0,
        "switchbackBurnOutDurationMs": 0,
        "switchbackDesignType": "balanced_block",
        "switchbackEstimand": "global_average_treatment_effect",
    }

    # A uid whose unit variant is 1 and whose switchback draw is 0: a unit experiment carrying
    # switchback fields that were served as a switchback, or fell back to control, would differ.
    def discriminating(k):
        uid = f"inert-{k}"
        return unit_variant(uid, split, seed_hi, seed_lo) == 1 and switchback_variant(uid, 0, 2) == 0

    session_uid = next(f"inert-{k}" for k in range(10_000) if discriminating(k))
    store_uid = "inert-store-0"

    switchback = switchback_experiment(130, "exp_inert_switchback", "store_id", 2, ANCHOR_PERIOD_0)
    plain = experiment(131, "exp_inert_unit_plain", "session_id", 2)
    typed_unit = experiment(132, "exp_inert_unit_typed", "session_id", 2, randomizationType="unit", **sb_fields)
    untyped = experiment(133, "exp_inert_unit_untyped", "session_id", 2, **sb_fields)
    same_unit = experiment(134, "exp_inert_unit_store", "store_id", 2)
    experiments = [switchback, plain, typed_unit, untyped, same_unit]
    units = {"session_id": session_uid, "store_id": store_uid}

    steps, exposed = [], []
    for exp, uid in ((plain, session_uid), (typed_unit, session_uid), (untyped, session_uid), (same_unit, store_uid)):
        variant = unit_variant(uid, exp["split"], exp["seedHi"], exp["seedLo"])
        exposed.append(exposure(exp, variant))
        steps.append(treatment_step(exp, variant, exposed[-1]))
    steps.append(publish_step(exposed))

    scenario = context_scenario(
        "Inertness - Unit Experiments Unaffected By Switchback Data",
        ("INERTNESS BASELINE (must pass on every SDK, switchback-aware or not): the payload carries a "
         "switchback experiment and unit experiments, two of which also carry every switchback wire field "
         "(one with randomizationType \"unit\", one with it absent). Each unit experiment's treatment and "
         "exposure equal plain unit assignment, and the uid is chosen so the switchback draw would differ. "
         "The switchback experiment is never queried: an SDK without switchback serves it a sticky unit "
         "assignment that is documented and SDK-specific."),
        experiments, units, steps)
    del scenario["requires"]
    return [scenario]


def build_group(golden: dict, first_number: int) -> list:
    scenarios = []
    for builder in BUILDERS:
        for scenario in builder(golden):
            scenario["name"] = f"{first_number + len(scenarios)} - Switchback - {scenario['name']}"
            scenarios.append(scenario)
    return scenarios


BUILDERS = [golden_scenarios, period_scenarios, eligibility_scenarios, serving_scenarios, inertness_scenarios]


def write(scenarios: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Switchback scenario group.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    args = parser.parse_args()

    golden = load_golden()
    verify_reference(golden)

    with open(CANONICAL, encoding="utf-8") as src:
        existing = json.load(src)
    kept = [s for s in existing if GROUP_MARKER not in s["name"]]
    group = build_group(golden, len(kept) + 1)
    updated = kept + group

    if args.check:
        if updated != existing:
            print("test_scenarios_complete.json is stale: run generate_switchback_scenarios.py")
            return 1
        print(f"up to date: {len(group)} switchback scenarios")
        return 0

    write(updated, CANONICAL)
    print(f"wrote {len(updated)} scenarios ({len(group)} switchback)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
