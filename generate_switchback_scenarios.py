#!/usr/bin/env python3
"""Generate the Switchback scenario group of test_scenarios_complete.json (R33, cluster-defined).

The group is derived, never hand-edited. Its inputs are byte-for-byte copies of platform fixtures
in fixtures/switchback/: golden-vectors.json (assignSwitchback and fisherYatesPermutation; abs
lib/utils blob b314abeb9e97) and attribute-path-cases.json (the cluster attribute path grammar; abs
collector blob 7b1d3fed5985). The independent reference below must reproduce every one of them
before anything is written.

The contract is R33 as the Collector serves it (absmartly/abs#5801 @ 9bd2b5ca35) and as java-sdk
implements it (absmartly/java-sdk#15, SWITCHBACK.md; head at time of writing: 91b9b31416):

  * switchbackClusters is an ordered object unitTypeName -> attributePath (declared order, unit
    type id ascending). Sources are iterated in that order, then the context units of each type.
    The first unit whose path yields a normalised value serves; its value is the cluster key. A
    later different value is a disagreement. switchbackClusterValues (null or [] = all) filters.
  * The variant is assignSwitchback(raw cluster key, floorDiv(now - startAt, periodMs), seed,
    arms): the key is never hashed, bucketed or replaced by the unit uid.
  * An ineligible cluster is variant 0 with no exposure; a traffic miss on an eligible cluster is
    recorded with eligible=false; pre-anchor and unservable schedules are silent controls (SDK
    choice; the Collector records it).

Scenarios:
  * Golden-vector scenarios drive every assign and permutation case through the wrapper's
    /switchback/assign and /switchback/permutation endpoints.
  * Context scenarios are built from a small context model (ContextModel) that computes every
    expected treatment, exposure and publish from the Python reference, never from an SDK. The
    same operations are replayed against wrong models (MUTANTS); generation fails unless every
    key-model mutant changes every eligible draw and every mutant fails at least one scenario.
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
import copy
import hashlib
import json
import math
import os
import sys
from collections import OrderedDict
from typing import NamedTuple, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")
GOLDEN = os.path.join(HERE, "fixtures", "switchback", "golden-vectors.json")
PATH_CASES = os.path.join(HERE, "fixtures", "switchback", "attribute-path-cases.json")

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
# Golden-vector scenarios
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


# ---------------------------------------------------------------------------
# R33 cluster attribute paths and values (Collector SwitchbackAttributePath / SwitchbackClusterKeys)
# ---------------------------------------------------------------------------


def normalize_cluster_value(value) -> Optional[str]:
    """Cluster key of an attribute value: a non-empty string as-is, an integer in base 10, and None
    (no value) for anything else, floats such as 3.0, booleans, objects, arrays and null included."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _index_segment(segment: str) -> Optional[int]:
    """Guava Ints.tryParse: optional '-', ASCII digits only, within signed 32-bit range."""
    digits = segment[1:] if segment.startswith("-") else segment
    if not digits or not all("0" <= c <= "9" for c in digits):
        return None
    value = int(segment, 10)
    return value if -(1 << 31) <= value < (1 << 31) else None


def parse_attribute_path(path: str) -> Optional[list]:
    """(key, index) segments, or None when malformed: '/' separates, a segment quoted with ' or "
    is a literal key, an unquoted integer segment is an array index. An unterminated quote, or a '.'
    in any segment key (quoted or not), is malformed: dotted paths are not a nesting syntax."""
    segments, i = [], 0
    while i < len(path):
        if path[i] == "/":
            i += 1
            continue
        if path[i] in "'\"":
            end = path.find(path[i], i + 1)
            if end < 0:
                return None
            segments.append((path[i + 1:end], None))
            i = end + 1
        else:
            end = path.find("/", i)
            stop = len(path) if end < 0 else end
            segments.append((path[i:stop], _index_segment(path[i:stop])))
            i = stop
    if any("." in key for key, _ in segments):
        return None
    return segments


def lax_parse_attribute_path(path: str) -> list:
    """The pre-R33 reading (no '.' rule, unterminated tail dropped), used only by the replay of
    index-position and dotted cases to show a lax reader would resolve them."""
    return [(s.strip("'\""), _index_segment(s)) for s in path.split("/") if s]


def supported_attribute_path(path) -> Optional[list]:
    """Segments of a path serving and analysis accept, else None: well formed, non-empty, and every
    index non-negative and between two key segments."""
    if not isinstance(path, str):
        return None
    segments = parse_attribute_path(path)
    if not segments:
        return None
    for i, (_, index) in enumerate(segments):
        if index is not None and (index < 0 or i == 0 or i == len(segments) - 1 or segments[i - 1][1] is not None):
            return None
    return segments


def resolve_attribute_path(attributes: dict, segments: list):
    """An index segment reads only lists, a key segment only objects; None when missing."""
    current = attributes
    for key, index in segments:
        if index is not None:
            current = current[index] if isinstance(current, list) and 0 <= index < len(current) else None
        else:
            current = current.get(key) if isinstance(current, dict) else None
        if current is None:
            return None
    return current


def load_path_cases() -> dict:
    with open(PATH_CASES, encoding="utf-8") as src:
        return json.load(src)


def verify_path_reference(paths: dict) -> None:
    for case in paths["cases"]:
        segments = supported_attribute_path(case["path"])
        value = resolve_attribute_path(paths["attributes"], segments or lax_parse_attribute_path(case["path"]))
        if (segments is not None) != case["supported"] or (segments is not None and value != case["value"]):
            raise SystemExit(f"attribute path reference disagrees with {case}: supported={segments is not None}, "
                             f"value={value!r}")


# ---------------------------------------------------------------------------
# Clock-free grids
# ---------------------------------------------------------------------------

# Wrappers have no clock control, so every grid below keeps "now" inside one known period from the
# time of writing until 2069-08-18T16:00Z (ANCHOR_PERIOD_0 + PERIOD_MS). The model reads NOW only to
# place it in that period; any instant in [2024, 2069) gives the same expectations.
HOUR_MS = 3_600_000
PERIOD_MS = 400_000 * HOUR_MS                   # 1,440,000,000,000 ms, about 45.6 years
ANCHOR_PERIOD_0 = 1_704_067_200_000             # 2024-01-01T00:00Z: now is in period 0
ANCHOR_PERIOD_1 = ANCHOR_PERIOD_0 - PERIOD_MS   # 1978-05-15T08:00Z: now is in period 1
ANCHOR_FUTURE = 4_102_444_800_000               # 2100-01-01T00:00Z: now is before the anchor
NOW = 1_794_268_800_000                         # 2026-11-10T00:00Z
MAX_ANCHOR_MS = 253_402_300_800_000             # 10000-01-01T00:00Z

# Per arm count: (seedHi, seedLo, split). Seeds cover a positive, a negative-hi and an all-ones-lo
# 64-bit seed; only split.length matters to the switchback draw.
ARMS = {
    2: (3603515, 233373850, [0.5, 0.5]),
    3: (-559038737, 1234567890, [0.34, 0.33, 0.33]),
    10: (2147483647, -1, [0.1] * 10),
}

# Experiments whose fixed cluster value collides with a wrong model at ARMS' seeds use
# (SEED_SEARCH_HI, k) for the first k that discriminates.
SEED_SEARCH_HI = 1_234_567


# ---------------------------------------------------------------------------
# Wrong cluster-key models (mutants of the draw). Each maps (key, serving uid, experiment) to the
# string a wrong SDK would pass to assignSwitchback, or None to draw nothing.
# ---------------------------------------------------------------------------


def _bucket_of_mutant(key: str, exp: dict) -> str:
    """The abandoned Phase 1E bucket model (100 buckets): never correct under R33."""
    prefix = f"sbcluster{SEPARATOR}{exp['seedHi']}:{exp['seedLo']}{SEPARATOR}"
    return str(int.from_bytes(hashlib.md5((prefix + key).encode("utf-8")).digest()[:4], "big") % 100)


KEY_MUTANTS = {
    "hashed_key": lambda key, uid, exp: hash_unit(key),
    "uid_as_key": lambda key, uid, exp: uid,
    "hashed_uid_as_key": lambda key, uid, exp: hash_unit(uid),
    "bucket_of": lambda key, uid, exp: _bucket_of_mutant(key, exp),
}


def draw(key: str, exp: dict, now: int = NOW) -> int:
    period = (now - exp["switchbackStartAt"]) // exp["switchbackPeriodMs"]
    return assign_switchback(key, period, experiment_seed(exp["seedHi"], exp["seedLo"]), len(exp["split"]))


def wrong_key_variants(key: str, uid: str, exp: dict) -> dict:
    """mutant -> the variant that cluster-key model would draw, plus the sticky unit assignment of
    the serving uid (an SDK ignoring randomizationType)."""
    wrong = {name: draw(make(key, uid, exp), exp) for name, make in KEY_MUTANTS.items()}
    wrong["sticky"] = unit_variant(uid, exp["split"], exp["seedHi"], exp["seedLo"])
    return wrong


def discriminates(key: str, uid: str, exp: dict, avoid=()) -> bool:
    variant = draw(key, exp)
    return variant != 0 and variant not in avoid and variant not in wrong_key_variants(key, uid, exp).values()


def pick_value(make, uid: str, exp: dict, avoid=(), avoid_keys=lambda value: ()):
    """First make(k) whose draw is nonzero, differs from every wrong cluster-key model for the
    serving uid, from `avoid`, and from the draw of every wrong rendering in avoid_keys(value)."""
    for k in range(10_000):
        value = make(k)
        key = normalize_cluster_value(value)
        if key is None:
            raise SystemExit(f"attribute value {value!r} has no cluster key")
        if discriminates(key, uid, exp, avoid) and all(draw(w, exp) != draw(key, exp) for w in avoid_keys(value)):
            return value
    raise SystemExit(f"no discriminating attribute value for {make(0)!r}")


def quiet(*exps):
    """Predicate on a serving uid: for each experiment of at most 3 arms, every uid-derived wrong
    model (uid as key, hashed uid as key, sticky unit assignment) draws 0, which leaves room for a
    nonzero cluster draw that differs from all of them. 10-arm experiments need no constraint."""
    def predicate(uid):
        for exp in exps:
            if len(exp["split"]) <= 3:
                wrong = wrong_key_variants("-", uid, exp)
                if any(wrong[m] for m in ("uid_as_key", "hashed_uid_as_key", "sticky")):
                    return False
        return True
    return predicate


def pick_uid(prefix: str, predicate) -> str:
    """First '<prefix>-<k>' satisfying predicate(uid)."""
    for k in range(10_000):
        uid = f"{prefix}-{k}"
        if predicate(uid):
            return uid
    raise SystemExit(f"no uid for {prefix}")


def pick_seeds(key: str, uid: str, exp: dict, avoid=()) -> dict:
    """Seed overrides that make a fixed cluster key discriminate: the arm count's own seeds first."""
    for seeds in [(exp["seedHi"], exp["seedLo"])] + [(SEED_SEARCH_HI, k) for k in range(1, 1000)]:
        candidate = dict(exp, seedHi=seeds[0], seedLo=seeds[1])
        if discriminates(key, uid, candidate, avoid):
            return {"seedHi": seeds[0], "seedLo": seeds[1]}
    raise SystemExit(f"no discriminating seeds for key {key!r}")


def number_renderings(value: int) -> list:
    """Keys an SDK would draw from if it wrapped the integer to int32 or read it as a double:
    "-294967290", "4000000006.0", "4.000000006E9" (Java), "4.000000006e+09" (C/Go %g)."""
    digits = str(abs(value)).rstrip("0") or "0"
    exponent = len(str(abs(value))) - 1
    mantissa = ("-" if value < 0 else "") + digits[0] + "." + (digits[1:] or "0")
    wrapped = ((value + (1 << 31)) & MASK32) - (1 << 31)
    return [str(wrapped), f"{value}.0", f"{mantissa}E{exponent}", f"{mantissa}e{exponent:+03d}"]


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def experiment(exp_id: int, name: str, unit_type: Optional[str], arm_count: int, **overrides) -> dict:
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


def switchback_experiment(exp_id: int, name: str, clusters, arm_count: int = 3, anchor=ANCHOR_PERIOD_0,
                          values=None, unit_type: Optional[str] = None, **overrides) -> dict:
    """clusters is a list of (unitTypeName, attributePath) in declared order, emitted as the ordered
    switchbackClusters object; None omits the field. values is switchbackClusterValues (omitted when
    None). unitType defaults to the first declared unit type, as the Collector serves it."""
    fields = OrderedDict(randomizationType="switchback")
    if clusters is not None:
        fields["switchbackClusters"] = OrderedDict(clusters)
    if values is not None:
        fields["switchbackClusterValues"] = list(values)
    if anchor is not None:
        fields["switchbackStartAt"] = anchor
    fields.update(
        switchbackPeriodMs=PERIOD_MS,
        switchbackCarryoverDurationMs=0,
        switchbackBurnInDurationMs=0,
        switchbackBurnOutDurationMs=0,
        switchbackDesignType="balanced_block",
        switchbackEstimand="global_average_treatment_effect",
    )
    fields.update(overrides)
    if unit_type is None and clusters:
        unit_type = clusters[0][0]
    return experiment(exp_id, name, unit_type, arm_count, **fields)


def exposure(exp: dict, variant: int, unit: Optional[str] = None, assigned=True, eligible=True, overridden=False,
             full_on=False, custom=False, audience_mismatch=False) -> dict:
    return {
        "id": exp["id"],
        "name": exp["name"],
        "unit": unit if unit is not None else exp["unitType"],
        "variant": variant,
        "assigned": assigned,
        "eligible": eligible,
        "overridden": overridden,
        "fullOn": full_on,
        "custom": custom,
        "audienceMismatch": audience_mismatch,
    }


def ready_events(experiments: list) -> list:
    return [{"type": "ready", "data": {"experiments": [{"id": e["id"], "name": e["name"]} for e in experiments]}}]


def create_step(experiments: list, units: dict) -> dict:
    return {
        "action": "createContextWith",
        "params": {"units": units, "options": {"publishDelay": -1, "refreshPeriod": 0}},
        "expect": {"result": {"ready": True, "failed": False, "finalized": False}, "events": ready_events(experiments)},
    }


def treatment_step(exp: dict, result: int, exposed: Optional[dict] = None) -> dict:
    events = [] if exposed is None else [{"type": "exposure", "data": exposed}]
    return {"action": "treatment", "params": {"experimentName": exp["name"]}, "expect": {"result": result, "events": events}}


def attribute_step(name: str, value) -> dict:
    return {"action": "attribute", "params": {"name": name, "value": value}, "expect": {"result": None, "events": []}}


def publish_step(exposures: list, units: Optional[dict] = None) -> dict:
    """Every published exposure is pinned in full. The published units are pinned (hashed uids)
    when the context has a single unit; with several, their order is the SDK's map order."""
    data = {"exposures": exposures}
    if units is not None and len(units) == 1:
        data = {"hashed": True, "units": [{"type": t, "uid": hash_unit(u)} for t, u in units.items()], **data}
    return {"action": "publish", "params": {}, "expect": {"result": None, "events": [{"type": "publish", "data": data}]}}


def context_scenario(name: str, description: str, experiments: list, units: dict, steps: list,
                     holdouts: Optional[list] = None) -> dict:
    context_data = {"experiments": experiments}
    if holdouts is not None:
        context_data["holdouts"] = holdouts
    return {
        "name": name,
        "description": description,
        "contextData": context_data,
        "steps": [create_step(experiments, units)] + steps,
        "requires": [CAPABILITY],
    }


# ---------------------------------------------------------------------------
# Context model: the R33 serving rules (Collector ContextAPI + java-sdk SWITCHBACK.md), executed
# over a scenario's steps. Expectations are computed by this model and nothing else; replaying a
# scenario under a mutant shows which wrong SDK behaviour the scenario catches.
# ---------------------------------------------------------------------------

# Mutants of the serving rules, replayed over every context scenario. Each must fail at least one
# scenario (generation fails otherwise); test_switchback_harness.py replays them again from the file.
SERVING_MUTANTS = (
    "last_unit_serves",        # the last unit with a value serves, not the first
    "units_outer",             # units iterated outer (context order), sources inner
    "units_outer_reversed",    # the same with the context's units in the other order
    "no_disagreement",         # the first value serves even when another unit disagrees
    "ignore_allow_list",       # switchbackClusterValues ignored
    "empty_allow_list_blocks", # switchbackClusterValues [] treated as "nothing allowed"
    "ineligible_exposes",      # an ineligible cluster records an eligible=false exposure
    "pre_anchor_exposes",      # pre-anchor / unservable draws record an eligible=false exposure
    "traffic_miss_silent",     # a traffic miss on an eligible cluster records nothing
    "traffic_on_unit_type",    # traffic hashed on the experiment's unitType uid, not the serving uid
    "holdout_applies",         # a holdout holds the switchback out
    "ignore_override",
    "ignore_full_on",
    "ignore_rules",
    "strict_mismatch_assigned",  # a strict audience mismatch records assigned=true
    "rules_after_cluster_check_only_when_servable",  # rules skipped before the anchor
    "ignore_custom",
    "round_anchor",            # an unaligned anchor is ceiled to the hour and served
    "legacy_fields",           # switchbackClusterUnitType/Attribute read instead of switchbackClusters
    "lax_values",              # booleans, floats, objects and arrays keyed by their text
    "dotted_nesting",          # '.' read as a nesting separator
    "lax_index_position",      # an index at the tail or two in a row accepted
    "legacy_unit_fallback",    # a missing/unreadable cluster map falls back to unit assignment
    "no_refresh_epoch",        # a refresh with a changed cluster map keeps the cached assignment
    "unit_experiment_as_switchback",  # a unit experiment carrying switchback fields served as one
)
ALL_MUTANTS = tuple(KEY_MUTANTS) + ("sticky",) + SERVING_MUTANTS


def _audience_matches(audience: Optional[str], attributes: dict) -> Optional[bool]:
    """The audience filters these scenarios use: {"filter":[{"eq":[{"var":n},{"value":v}]}]}."""
    if not audience:
        return None
    (clause,) = json.loads(audience)["filter"]
    var, value = clause["eq"]
    return attributes.get(var["var"]) == value["value"]


def _assign_rule_variant(rules: Optional[str]) -> Optional[int]:
    if not rules:
        return None
    for rule in json.loads(rules)["rules"]:
        if rule["type"] == "assign" and not rule.get("environments") and "conditions" not in rule:
            return rule["variant"]
    return None


def _servable(exp: dict, mutant) -> bool:
    start, period = exp.get("switchbackStartAt"), exp.get("switchbackPeriodMs")
    if mutant == "round_anchor" and isinstance(start, int):
        start = -(-start // HOUR_MS) * HOUR_MS
        exp["switchbackStartAt"] = start
    return (isinstance(start, int) and 0 <= start < MAX_ANCHOR_MS and start % HOUR_MS == 0
            and isinstance(period, int) and period > 0 and len(exp["split"]) >= 2)


def _identity(exp: Optional[dict]):
    if exp is None:
        return None
    keys = ("id", "iteration", "randomizationType", "switchbackClusterValues", "switchbackStartAt",
            "switchbackPeriodMs", "switchbackDesignType", "unitType")
    return json.dumps([[exp.get(k) for k in keys], list((exp.get("switchbackClusters") or {}).items())])


class ContextModel:
    """One context: its units (in the order the scenario lists them), attributes, overrides, custom
    assignments, the per-experiment cached resolution and the pending exposures."""

    def __init__(self, context_data: dict, units: dict, mutant: Optional[str] = None):
        self.mutant = mutant
        self.experiments = {e["name"]: e for e in context_data["experiments"]}
        self.units = list(units.items())
        if mutant == "units_outer_reversed":
            self.units.reverse()
        self.unit_map = dict(units)
        self.attributes = {}
        self.overrides, self.customs = {}, {}
        self.cache = {}
        self.pending = []
        self.draws = []  # (key, serving uid, experiment) of every eligible schedule draw

    # -- serving -----------------------------------------------------------

    def _read(self, segments):
        return normalize_cluster_value(resolve_attribute_path(self.attributes, segments)) if segments else None

    def _lax_read(self, path):
        value = resolve_attribute_path(self.attributes, lax_parse_attribute_path(path))
        if value is None or value == "" or value == [] or value == {}:
            return None
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (dict, list)):
            return json.dumps(value, separators=(",", ":"))
        return str(value)

    def _sources(self, exp: dict):
        """[(unitType, segments or path)] in declared order, or None for the legacy path."""
        m = self.mutant
        clusters = exp.get("switchbackClusters")
        if m == "legacy_fields" and exp.get("switchbackClusterUnitType"):
            clusters = {exp["switchbackClusterUnitType"]: exp.get("switchbackClusterAttribute") or "-"}
        if not isinstance(clusters, dict) or not clusters:
            return None
        sources = []
        for unit_type, path in clusters.items():
            segments = supported_attribute_path(path)
            if segments is None and m in ("dotted_nesting", "lax_index_position", "lax_values") and isinstance(path, str):
                dotted = "." in path
                if (m == "dotted_nesting" and dotted) or (m == "lax_index_position" and not dotted):
                    sources.append((unit_type, ("lax", path.replace(".", "/") if dotted else path)))
                    continue
            if segments is None:
                return None
            sources.append((unit_type, segments))
        return sources

    def _value(self, source):
        _, segments = source
        if isinstance(segments, tuple):
            return self._lax_read(segments[1])
        if self.mutant == "lax_values":
            path = "/".join(k if i is None else str(i) for k, i in segments)
            return self._lax_read(path)
        return self._read(segments)

    def serving(self, exp: dict):
        """('absent' | 'ineligible' | 'eligible', unit type, uid, cluster key)."""
        sources = self._sources(exp)
        if sources is None:
            uid = self.unit_map.get(exp.get("unitType"))
            return ("absent", None, None, None) if uid is None else ("ineligible", exp["unitType"], uid, None)
        pairs = []
        if self.mutant in ("units_outer", "units_outer_reversed"):
            for unit_type, uid in self.units:
                pairs += [(unit_type, uid, s) for s in sources if s[0] == unit_type]
        else:
            for source in sources:
                pairs += [(t, u, source) for t, u in self.units if t == source[0]]
        if not pairs:
            return "absent", None, None, None
        first = pairs[0]
        valued = [(t, u, v) for t, u, s in pairs for v in [self._value(s)] if v is not None]
        if not valued:
            return "ineligible", first[0], first[1], None
        serving = valued[-1] if self.mutant == "last_unit_serves" else valued[0]
        if self.mutant != "no_disagreement" and any(v != serving[2] for _, _, v in valued):
            return "ineligible", first[0], first[1], None
        allowed = exp.get("switchbackClusterValues")
        if self.mutant != "ignore_allow_list" and allowed is not None:
            if (allowed or self.mutant == "empty_allow_list_blocks") and serving[2] not in allowed:
                return "ineligible", first[0], first[1], None
        return "eligible", serving[0], serving[1], serving[2]

    def resolve(self, exp: dict):
        """(variant, exposure or None) for one experiment, as getTreatment resolves it."""
        m = self.mutant
        exp = copy.deepcopy(exp)
        is_switchback = exp.get("randomizationType") == "switchback"
        if m == "unit_experiment_as_switchback" and exp.get("switchbackClusters") and not is_switchback:
            is_switchback = True
        if m == "sticky":
            is_switchback = False
        name = exp["name"]

        if is_switchback:
            status, unit_type, uid, key = self.serving(exp)
            if m == "legacy_unit_fallback" and self._sources(exp) is None:
                is_switchback = False
        if not is_switchback:
            return self._resolve_unit(exp)

        if name in self.overrides and m != "ignore_override":
            unit = unit_type or (next(iter(exp.get("switchbackClusters") or {}), None) or exp["unitType"])
            return self.overrides[name], exposure(exp, self.overrides[name], unit, assigned=False, overridden=True)
        if status == "absent":
            return 0, None
        if status == "ineligible":
            if m == "ineligible_exposes":
                return 0, exposure(exp, 0, unit_type, eligible=False)
            return 0, None

        rule = _assign_rule_variant(exp.get("assignmentRules"))
        servable = _servable(exp, m)
        before_anchor = servable and NOW < exp["switchbackStartAt"]
        if rule is not None and m != "ignore_rules" and not (
                m == "rules_after_cluster_check_only_when_servable" and (not servable or before_anchor)):
            out = exposure(exp, rule, unit_type, assigned=False)
            out["ruleOverride"] = True
            return rule, out
        mismatch = _audience_matches(exp.get("audience"), self.attributes) is False
        if exp.get("audienceStrict") and mismatch:
            # As for unit experiments: a strict mismatch is not an assignment (assigned=false).
            return 0, exposure(exp, 0, unit_type, assigned=m == "strict_mismatch_assigned", audience_mismatch=True)
        if exp["fullOnVariant"] and m != "ignore_full_on":
            return exp["fullOnVariant"], exposure(exp, exp["fullOnVariant"], unit_type, full_on=True,
                                                  audience_mismatch=mismatch)
        if not servable or before_anchor:
            if m == "pre_anchor_exposes":
                return 0, exposure(exp, 0, unit_type, eligible=False, audience_mismatch=mismatch)
            return 0, None
        if exp.get("holdoutIds") and m == "holdout_applies":
            return 0, None
        traffic_uid = self.unit_map.get(exp["unitType"], uid) if m == "traffic_on_unit_type" else uid
        if unit_variant(traffic_uid, exp["trafficSplit"], exp["trafficSeedHi"], exp["trafficSeedLo"]) != 1:
            if m == "traffic_miss_silent":
                return 0, None
            return 0, exposure(exp, 0, unit_type, eligible=False, audience_mismatch=mismatch)
        if name in self.customs and m != "ignore_custom":
            return self.customs[name], exposure(exp, self.customs[name], unit_type, custom=True,
                                                audience_mismatch=mismatch)
        if m in KEY_MUTANTS:
            variant = draw(KEY_MUTANTS[m](key, uid, exp), exp)
        else:
            variant = draw(key, exp)
            if m is None:
                self.draws.append((key, uid, exp))
        return variant, exposure(exp, variant, unit_type, audience_mismatch=mismatch)

    def _resolve_unit(self, exp: dict):
        """Ordinary unit assignment (no rules, audiences or holdouts in these scenarios' unit experiments)."""
        name, uid = exp["name"], self.unit_map.get(exp["unitType"])
        if name in self.overrides:
            return self.overrides[name], exposure(exp, self.overrides[name], assigned=False, overridden=True)
        if uid is None:
            return 0, exposure(exp, 0, assigned=False)
        if exp["fullOnVariant"]:
            return exp["fullOnVariant"], exposure(exp, exp["fullOnVariant"], full_on=True)
        if unit_variant(uid, exp["trafficSplit"], exp["trafficSeedHi"], exp["trafficSeedLo"]) != 1:
            return 0, exposure(exp, 0, eligible=False)
        variant = unit_variant(uid, exp["split"], exp["seedHi"], exp["seedLo"])
        return variant, exposure(exp, variant)

    # -- steps -------------------------------------------------------------

    def run(self, step: dict) -> dict:
        """The expect block of one step after the create step."""
        action, params = step["action"], step["params"]
        if action == "attribute":
            self.attributes[params["name"]] = params["value"]
            return {"result": None, "events": []}
        if action == "override":
            self.overrides[params["experimentName"]] = params["variant"]
            return {"result": None, "events": []}
        if action == "customAssignment":
            self.customs[params["experimentName"]] = params["variant"]
            return {"result": None, "events": []}
        if action == "pending":
            return {"result": len(self.pending), "events": []}
        if action == "treatment":
            name = params["experimentName"]
            exp = self.experiments[name]
            identity = _identity(exp)
            cached = self.cache.get(name)
            if cached is not None and (cached[0] == identity or self.mutant == "no_refresh_epoch"):
                return {"result": cached[1], "events": []}
            variant, exposed = self.resolve(exp)
            self.cache[name] = (identity, variant)
            if exposed is None:
                return {"result": variant, "events": []}
            self.pending.append(exposed)
            return {"result": variant, "events": [{"type": "exposure", "data": exposed}]}
        if action == "publish":
            if not self.pending:
                if self.mutant is None:
                    raise ValueError("publish with nothing pending")
                return {"result": None, "events": []}
            expected = publish_step(self.pending, self.unit_map)["expect"]
            self.pending = []
            return expected
        if action == "refresh":
            self.experiments = {e["name"]: e for e in params["newData"]["experiments"]}
            refreshed = ready_events(params["newData"]["experiments"])[0]
            return {"result": None, "events": [dict(refreshed, type="refresh")]}
        raise ValueError(f"model has no action {action}")


def replay(scenario: dict, mutant: Optional[str] = None) -> tuple:
    """(expect blocks of every step after the create step, the model) under `mutant`."""
    create = scenario["steps"][0]
    model = ContextModel(scenario["contextData"], create["params"]["units"], mutant)
    return [model.run(step) for step in scenario["steps"][1:]], model


class Scenario:
    """Collects steps; expectations are filled in by the model when the scenario is built."""

    def __init__(self, name: str, description: str, experiments: list, units: dict, holdouts=None):
        self.name, self.description = name, description
        self.experiments, self.units, self.holdouts = experiments, units, holdouts
        self.ops = []

    def attr(self, name, value):
        self.ops.append({"action": "attribute", "params": {"name": name, "value": value}})
        return self

    def treat(self, *exps):
        for exp in exps:
            self.ops.append({"action": "treatment", "params": {"experimentName": exp["name"]}})
        return self

    def override(self, exp, variant):
        self.ops.append({"action": "override", "params": {"experimentName": exp["name"], "variant": variant}})
        return self

    def custom(self, exp, variant):
        self.ops.append({"action": "customAssignment", "params": {"experimentName": exp["name"], "variant": variant}})
        return self

    def pending(self):
        self.ops.append({"action": "pending", "params": {}})
        return self

    def publish(self):
        self.ops.append({"action": "publish", "params": {}})
        return self

    def refresh(self, experiments):
        self.ops.append({"action": "refresh", "params": {"newData": {"experiments": experiments}}})
        return self

    def build(self) -> dict:
        scenario = context_scenario(self.name, self.description, self.experiments, self.units,
                                    [dict(op, expect=None) for op in self.ops], self.holdouts)
        expects, model = replay(scenario)
        for step, expect in zip(scenario["steps"][1:], expects):
            step["expect"] = expect
        scenario["_draws"] = model.draws
        return scenario


# ---------------------------------------------------------------------------
# Context scenarios, one or more per R33 rule (RULES maps each rule to its builders' scenarios)
# ---------------------------------------------------------------------------

# Every 3-arm period-0 experiment with ARMS[3]'s seeds draws alike for a given key and uid.
QUIET3 = quiet(switchback_experiment(0, "quiet", [("unit", "path")], 3))


def single_source_scenarios() -> list:
    uid = pick_uid("single", quiet(switchback_experiment(0, "q", [("u", "p")], 2, anchor=ANCHOR_PERIOD_1), switchback_experiment(0, "q", [("u", "p")], 3)))
    exp3 = switchback_experiment(300, "exp_sb_single_a3", [("store_id", "city")], 3)
    exp10 = switchback_experiment(301, "exp_sb_single_a10", [("store_id", "city10")], 10)
    exp2 = switchback_experiment(302, "exp_sb_single_p1_a2", [("store_id", "city2")], 2, anchor=ANCHOR_PERIOD_1)
    city = pick_value(lambda k: f"city-{k}", uid, exp3)
    city10 = pick_value(lambda k: f"metro-{k}", uid, exp10)
    city2 = pick_value(lambda k: f"town-{k}", uid, dict(exp2))
    return [Scenario(
        "Single Source - Raw Attribute Value Is The Cluster Key",
        ("switchbackClusters {store_id: <path>} with one store_id unit. The cluster key is the raw attribute "
         "value: variant = assignSwitchback(value, periodIndex, seed, arms) with periodIndex = floorDiv(now - "
         "switchbackStartAt, switchbackPeriodMs) (period 0 for the 3- and 10-arm experiments, period 1 for the "
         "2-arm one). Each value is chosen so the draw is nonzero and differs from a hashed key, the uid as key, "
         "the hashed uid as key, a Phase 1E bucket and the sticky unit assignment. Each exposure is under the "
         "serving unit store_id."),
        [exp3, exp10, exp2], {"store_id": uid},
    ).attr("city", city).attr("city10", city10).attr("city2", city2).treat(exp3, exp10, exp2).treat(exp3)
        .publish().build()]


def declared_order_scenarios() -> list:
    """Two sources: the lower-declared source serves whatever order the context sets the units in."""
    session = pick_uid("order-session", QUIET3)
    user = pick_uid("order-user", lambda u: u != session and QUIET3(u))
    session_first = switchback_experiment(310, "exp_sb_order_session_first",
                                          [("session_id", "session_store"), ("user_id", "profile/store")], 3)
    user_first = switchback_experiment(311, "exp_sb_order_user_first",
                                       [("user_id", "profile/store"), ("session_id", "session_store")], 3)
    store = next(f"s-{k}" for k in range(10_000) if discriminates(f"s-{k}", session, session_first)
                 and discriminates(f"s-{k}", user, user_first))
    return [Scenario(
        "Declared Order - Lower-Declared Source Serves Whatever The Context Unit Order",
        ("Both units carry the same cluster value (agreement), so both experiments are eligible. The context "
         "sets user_id before session_id. exp_sb_order_session_first declares session_id first and must serve "
         "and expose under session_id; exp_sb_order_user_first declares user_id first and must serve under "
         "user_id. A units-outer iteration (context order) or a last-unit-serves model exposes the wrong unit in "
         "at least one of them, whatever order the SDK keeps its units in."),
        [session_first, user_first], {"user_id": user, "session_id": session},
    ).attr("session_store", store).attr("profile", {"store": store}).treat(session_first, user_first)
        .publish().build()]


def agreement_disagreement_scenarios() -> list:
    session = pick_uid("agree-session", QUIET3)
    user = pick_uid("agree-user", QUIET3)
    agree = switchback_experiment(320, "exp_sb_agree", [("session_id", "session_store"), ("user_id", "profile/store")], 3)
    disagree = switchback_experiment(321, "exp_sb_disagree",
                                     [("session_id", "session_store_d"), ("user_id", "profile/store_d")], 3)
    skipped = switchback_experiment(322, "exp_sb_first_without_value",
                                    [("session_id", "session_zone"), ("user_id", "profile/zone")], 3)
    value = pick_value(lambda k: f"agree-{k}", session, agree)
    # Each disagreeing value alone would draw nonzero, so serving either one is caught.
    a = next(f"left-{k}" for k in range(10_000) if discriminates(f"left-{k}", session, disagree))
    b = next(f"right-{k}" for k in range(10_000) if discriminates(f"right-{k}", user, disagree))
    zone = pick_value(lambda k: f"zone-{k}", user, skipped)
    return [
        Scenario(
            "Agreement - Equal Values From Two Units Serve The Cluster",
            ("session_store and profile/store hold the same value: the units agree and the first declared "
             "(session_id) serves with that raw value as the cluster key."),
            [agree], {"session_id": session, "user_id": user},
        ).attr("session_store", value).attr("profile", {"store": value}).treat(agree).publish().build(),
        Scenario(
            "Disagreement - Different Values Are An Ineligible Control With No Exposure",
            (f"session_store={a!r} and profile/store_d={b!r} disagree: variant 0 and no exposure (pending stays "
             "0). Either value alone would draw a nonzero variant, so a first-value-wins or last-value-wins SDK "
             "fails, and an SDK that records the ineligible control (eligible=false) fails the exposure check."),
            [disagree], {"session_id": session, "user_id": user},
        ).attr("session_store_d", a).attr("profile", {"store_d": b}).treat(disagree).treat(disagree).pending().build(),
        Scenario(
            "Unit Without A Value Is Skipped - The Next Unit Serves",
            ("session_id is declared first but session_zone is unset; user_id's profile/zone has a value, so "
             "user_id serves and the exposure is under user_id."),
            [skipped], {"session_id": session, "user_id": user},
        ).attr("profile", {"zone": zone}).treat(skipped).publish().build(),
    ]


INELIGIBLE_VALUES = [
    ("fraction", 1.5), ("integral_float", 3.0), ("bool", True), ("object", {"a": 1}), ("array", ["a"]),
    ("empty", ""), ("null", None),
]


def value_normalisation_scenarios() -> list:
    uid = pick_uid("values", QUIET3)
    ineligible, steps = [], []
    for offset, (label, value) in enumerate(INELIGIBLE_VALUES):
        base = switchback_experiment(330 + offset, f"exp_sb_value_{label}", [("store_id", f"v_{label}")], 3)
        # A lax reader would key the value's text; choose seeds so that draw is nonzero.
        lax = {"fraction": "1.5", "integral_float": "3", "bool": "true", "object": '{"a":1}', "array": '["a"]',
               "empty": "", "null": "null"}[label]
        seeds = next(s for s in [(base["seedHi"], base["seedLo"])] + [(SEED_SEARCH_HI, k) for k in range(1, 999)]
                     if draw(lax, dict(base, seedHi=s[0], seedLo=s[1])) != 0
                     and draw("3.0", dict(base, seedHi=s[0], seedLo=s[1])) != 0)
        ineligible.append(dict(base, seedHi=seeds[0], seedLo=seeds[1]))
    number_exp = switchback_experiment(340, "exp_sb_value_whole_number", [("store_id", "store_number")], 3)
    number = pick_value(lambda k: 4_000_000_000 + k, uid, number_exp, avoid_keys=number_renderings)
    agree_exp = switchback_experiment(341, "exp_sb_value_number_agrees_with_string",
                                      [("store_id", "store_number"), ("user_id", "profile/number")], 3)
    user = pick_uid("values-user", QUIET3)
    sc = Scenario(
        "Values - Strings And Whole Numbers Only",
        ("A non-empty string is the key as-is and a whole number is keyed by its decimal text: store_number="
         f"{number} draws assignSwitchback(\"{number}\", ...), unlike its int32 wrap or double renderings, and "
         f"agrees with the string \"{number}\" from another unit type. 1.5, 3.0, true, an object, an array, \"\" "
         "and null give no value: each such experiment is an ineligible control with no exposure, although its "
         "lax text rendering would draw nonzero."),
        ineligible + [number_exp, agree_exp], {"store_id": uid, "user_id": user},
    )
    for (label, value), exp in zip(INELIGIBLE_VALUES, ineligible):
        sc.attr(f"v_{label}", value)
    sc.attr("store_number", number).attr("profile", {"number": str(number)})
    sc.treat(*ineligible).pending().treat(number_exp, agree_exp).publish()
    return [sc.build()]


def path_scenarios(paths: dict) -> list:
    uid = pick_uid("path", QUIET3)
    extra_attributes = {"tags": ["a", "b"], "m": [[{"city": "x"}]], "dot": {"city": "dotted"},
                        "geo.city": "dotted-literal"}
    cases = [(c["path"], c["supported"], c["value"]) for c in paths["cases"]]
    cases += [("tags/0", False, None), ("m/0/0/city", False, None), ("geo.city", False, None),
              ("'geo.city'", False, None), ('"geo.city"', False, None), ("dot.city", False, None),
              ("'geo", False, None)]
    experiments = []
    for i, (path, supported, value) in enumerate(cases):
        exp = switchback_experiment(350 + i, f"exp_sb_path_{i}", [("store_id", path)], 3)
        if supported and value is not None:
            exp.update(pick_seeds(value, uid, exp))
        else:
            # Where a lax reader would find a value, make that draw nonzero.
            lax = resolve_attribute_path({**paths["attributes"], **extra_attributes},
                                         lax_parse_attribute_path(path.replace(".", "/")))
            lax_key = normalize_cluster_value(lax)
            if lax_key is not None:
                exp.update(next({"seedHi": SEED_SEARCH_HI, "seedLo": k} for k in range(1, 999)
                                if draw(lax_key, dict(exp, seedHi=SEED_SEARCH_HI, seedLo=k)) != 0))
        experiments.append(exp)
    sc = Scenario(
        "Attribute Paths - Platform Path Cases And Malformed Paths",
        ("One experiment per case of the platform's attribute-path-cases.json (fixture attributes set as "
         "top-level attributes): a supported path that resolves to a value serves that value as the cluster "
         "key; a supported path that resolves to nothing (\"+0\" and \"\\u0660\" are keys, not indices) is an "
         "ineligible control with no exposure; an unsupported path (a negative or misplaced index) makes the "
         "cluster map unreadable, which takes the legacy path (ineligible control under unitType, no exposure). "
         "Added cases: \"tags/0\" and \"m/0/0/city\" (index at the tail, two indices in a row), \"geo.city\", "
         "\"'geo.city'\", \"\\\"geo.city\\\"\" and \"dot.city\" (any '.' in a segment key is malformed, quoted "
         "or not, even though dot/city resolves) and \"'geo\" (unterminated quote). Where a lax reader would "
         "find a value, the seeds make that draw nonzero."),
        experiments, {"store_id": uid},
    )
    for name, value in {**paths["attributes"], **extra_attributes}.items():
        sc.attr(name, value)
    sc.treat(*experiments).publish()
    return [sc.build()]


def allow_list_scenarios() -> list:
    uid = pick_uid("allow", QUIET3)
    inside = switchback_experiment(370, "exp_sb_allow_in", [("store_id", "zone_in")], 3, values=["s-1"])
    outside = switchback_experiment(371, "exp_sb_allow_out", [("store_id", "zone_out")], 3, values=["s-1", "s-2"])
    null = switchback_experiment(372, "exp_sb_allow_null", [("store_id", "zone_null")], 3, switchbackClusterValues=None)
    empty = switchback_experiment(373, "exp_sb_allow_empty", [("store_id", "zone_empty")], 3, values=[])
    value_in = pick_value(lambda k: f"in-{k}", uid, inside)
    inside["switchbackClusterValues"] = ["s-1", value_in]
    value_out = pick_value(lambda k: f"out-{k}", uid, outside)
    value_null = pick_value(lambda k: f"null-{k}", uid, null)
    value_empty = pick_value(lambda k: f"empty-{k}", uid, empty)
    return [Scenario(
        "Allow-List - In, Out, Null And Empty",
        ("switchbackClusterValues filters the cluster key: a value in the list serves; a value outside it is an "
         "ineligible control with no exposure (its draw would be nonzero); null and [] both admit every value "
         "(the Collector builds its filter only for a non-empty list)."),
        [inside, outside, null, empty], {"store_id": uid},
    ).attr("zone_in", value_in).attr("zone_out", value_out).attr("zone_null", value_null)
        .attr("zone_empty", value_empty).treat(outside).pending().treat(inside, null, empty).publish().build()]


def schedule_scenarios() -> list:
    uid = pick_uid("schedule", lambda u: unit_variant(u, ARMS[3][2], *ARMS[3][:2]) != 0)
    pre = switchback_experiment(380, "exp_sb_pre_anchor", [("store_id", "city")], 3, anchor=ANCHOR_FUTURE)
    unaligned = switchback_experiment(381, "exp_sb_unaligned_anchor", [("store_id", "city")], 3,
                                      anchor=ANCHOR_PERIOD_0 + 1)
    no_anchor = switchback_experiment(382, "exp_sb_no_anchor", [("store_id", "city")], 3, anchor=None)
    bad_period = switchback_experiment(383, "exp_sb_zero_period", [("store_id", "city")], 3, switchbackPeriodMs=0)
    one_arm = switchback_experiment(384, "exp_sb_one_arm", [("store_id", "city")], 2, split=[1.0],
                                    variants=[{"name": "A", "config": None}])
    rounded = dict(unaligned, switchbackStartAt=ANCHOR_PERIOD_0 + HOUR_MS)
    city = next(f"sched-{k}" for k in range(10_000) if draw(f"sched-{k}", rounded) != 0)
    return [Scenario(
        "Schedule - Pre-Anchor And Unservable Schedules Are Silent Controls",
        ("The cluster is eligible (city is set) but the draw cannot run: now is before switchbackStartAt "
         "(2100-01-01), the anchor is not hour aligned (anchor+1 ms; never rounded, though the hour-ceiled anchor "
         "would draw nonzero), the anchor is absent, the period is 0, or the split has one arm. Each is variant "
         "0 and records no exposure (the SDK's documented pre-anchor choice), and never falls back to sticky "
         "unit assignment (the uid's unit draw is nonzero)."),
        [pre, unaligned, no_anchor, bad_period, one_arm], {"store_id": uid},
    ).attr("city", city).treat(pre, unaligned, no_anchor, bad_period, one_arm).pending().build()]


def traffic_scenarios() -> list:
    split = [0.5, 0.5]
    probe = switchback_experiment(390, "x", [("session_id", "s")], 3, trafficSplit=split)

    def traffic(u):
        return unit_variant(u, split, probe["trafficSeedHi"], probe["trafficSeedLo"])

    miss = pick_uid("traffic-miss", lambda u: traffic(u) == 0)
    hit = pick_uid("traffic-hit", lambda u: traffic(u) == 1 and QUIET3(u))
    session_serves = switchback_experiment(390, "exp_sb_traffic_session_serves",
                                           [("session_id", "session_store"), ("user_id", "profile/store")], 3,
                                           trafficSplit=split, unit_type="user_id")
    user_serves = switchback_experiment(391, "exp_sb_traffic_user_serves",
                                        [("user_id", "profile/store"), ("session_id", "session_store")], 3,
                                        trafficSplit=split, unit_type="session_id")
    store = next(f"t-{k}" for k in range(10_000) if discriminates(f"t-{k}", hit, user_serves))
    return [Scenario(
        "Traffic - Miss On An Eligible Cluster Is Recorded Eligible=False Under The Serving Unit",
        ("trafficSplit 50/50, hashed on the serving unit's uid. Both units agree on the cluster value. In "
         "exp_sb_traffic_session_serves session_id (declared first) serves and its uid misses traffic: variant 0 "
         "and an exposure under session_id with eligible=false is recorded. In exp_sb_traffic_user_serves user_id "
         "serves and its uid is in traffic, so the cluster's draw is served. Each experiment's unitType names the "
         "other unit, so hashing traffic on unitType flips both outcomes."),
        [session_serves, user_serves], {"session_id": miss, "user_id": hit},
    ).attr("session_store", store).attr("profile", {"store": store}).treat(session_serves, user_serves)
        .publish().build()]


def _other(variant: int, arms: int = 3) -> int:
    return next(v for v in range(1, arms) if v != variant)


def precedence_scenarios() -> list:
    uid = pick_uid("precedence", QUIET3)
    probe = switchback_experiment(400, "probe", [("store_id", "city")], 3)
    city = pick_value(lambda k: f"prec-{k}", uid, probe)
    drawn = draw(city, probe)
    other = _other(drawn)
    assign_rule = json.dumps({"rules": [{"type": "assign", "environments": [], "variant": other}]},
                             separators=(",", ":"))
    audience = json.dumps({"filter": [{"eq": [{"var": "tier"}, {"value": "gold"}]}]}, separators=(",", ":"))
    overridden = switchback_experiment(400, "exp_sb_override", [("store_id", "city")], 3)
    full_on = switchback_experiment(401, "exp_sb_full_on", [("store_id", "city")], 3, fullOnVariant=other)
    ruled = switchback_experiment(402, "exp_sb_assignment_rule", [("store_id", "city")], 3, assignmentRules=assign_rule)
    ruled_pre = switchback_experiment(403, "exp_sb_assignment_rule_pre_anchor", [("store_id", "city")], 3,
                                      anchor=ANCHOR_FUTURE, assignmentRules=assign_rule)
    strict = switchback_experiment(404, "exp_sb_audience_strict", [("store_id", "city")], 3, audience=audience,
                                   audienceStrict=True)
    custom = switchback_experiment(405, "exp_sb_custom", [("store_id", "city")], 3)
    full_on_pre = switchback_experiment(406, "exp_sb_full_on_pre_anchor", [("store_id", "city")], 3,
                                        anchor=ANCHOR_FUTURE, fullOnVariant=other)
    experiments = [overridden, full_on, ruled, ruled_pre, strict, custom, full_on_pre]
    return [Scenario(
        "Precedence - Override, Assignment Rule, Strict Audience, Full-On And Custom Over The Draw",
        (f"The cluster's period-0 draw is {drawn}. An override to {other} wins (assigned=false, overridden=true); "
         f"an assign rule to {other} wins (assigned=false, ruleOverride=true), also before the anchor; a strict "
         "audience mismatch (tier silver, audience tier gold) serves 0 with assigned=false and "
         "audienceMismatch=true, recorded, as for unit experiments; "
         f"fullOnVariant={other} wins (fullOn=true), also before the anchor; a custom assignment to {other} is "
         "served after traffic (custom=true). Every exposure is under the serving unit store_id."),
        experiments, {"store_id": uid},
    ).attr("city", city).attr("tier", "silver").override(overridden, other).custom(custom, other)
        .treat(*experiments).publish().build()]


def holdout_scenarios() -> list:
    uid = pick_uid("holdout", QUIET3)
    exp = switchback_experiment(410, "exp_sb_holdout_ignored", [("store_id", "city")], 3, holdoutIds=[1])
    city = pick_value(lambda k: f"held-{k}", uid, exp)
    holdouts = [{"id": 1, "name": "holdout_sb_everyone", "iteration": 1, "unitType": "store_id", "seedHi": 13,
                 "seedLo": 111, "split": [1.0, 0.0]}]
    return [Scenario(
        "Holdouts Never Apply To A Switchback",
        ("The switchback is covered by a holdout whose split [1, 0] holds every unit out (variant 0). Holdouts "
         "never apply to a switchback: the cluster's draw is served, exactly one exposure (the switchback's own) "
         "is recorded and the holdout records none."),
        [exp], {"store_id": uid}, holdouts=holdouts,
    ).attr("city", city).treat(exp).publish().build()]


def legacy_scenarios() -> list:
    uid = pick_uid("legacy", lambda u: unit_variant(u, ARMS[3][2], *ARMS[3][:2]) != 0)
    missing = switchback_experiment(420, "exp_sb_clusters_missing", None, 3, unit_type="store_id")
    empty = switchback_experiment(421, "exp_sb_clusters_empty", [], 3, unit_type="store_id")
    empty["switchbackClusters"] = {}
    absent = switchback_experiment(422, "exp_sb_no_linked_unit", [("warehouse_id", "city")], 3)
    legacy_only = switchback_experiment(423, "exp_sb_only_legacy_fields", None, 3, unit_type="store_id",
                                        switchbackClusterUnitType="store_id", switchbackClusterCount=10,
                                        switchbackClusterAttribute="city")
    # R33 fields plus the abandoned Phase 1E fields pointing at another unit and attribute.
    mixed = switchback_experiment(424, "exp_sb_legacy_fields_ignored", [("store_id", "city")], 10,
                                  switchbackClusterUnitType="user_id", switchbackClusterCount=100,
                                  switchbackClusterAttribute="zone")
    user = pick_uid("legacy-user", QUIET3)
    city = pick_value(lambda k: f"legacy-{k}", uid, mixed)
    zone = pick_value(lambda k: f"lzone-{k}", user, mixed, avoid={draw(city, mixed)})
    return [Scenario(
        "Legacy Path - Missing Or Empty Cluster Map, No Linked Unit, Phase 1E Fields Ignored",
        ("switchbackClusters absent or {} (also when only the abandoned switchbackClusterUnitType/Count/Attribute "
         "fields are present) takes the Collector's legacy path: a store_id unit (the declared unitType) gets an "
         "ineligible control, variant 0 and no exposure, never sticky unit assignment (the uid's unit draw is "
         "nonzero). A cluster map whose only unit type the context lacks does not apply: variant 0, no exposure. "
         "An experiment with switchbackClusters {store_id: city} that also carries the Phase 1E fields pointing "
         "at user_id/zone is served from city under store_id; reading the legacy fields would draw differently."),
        [missing, empty, absent, legacy_only, mixed], {"store_id": uid, "user_id": user},
    ).attr("city", city).attr("zone", zone).treat(missing, empty, absent, legacy_only).pending().treat(mixed)
        .publish().build()]


def refresh_scenarios() -> list:
    uid = pick_uid("refresh", QUIET3)
    before = switchback_experiment(430, "exp_sb_refresh", [("store_id", "city")], 3)
    after = switchback_experiment(430, "exp_sb_refresh", [("store_id", "zone")], 3)
    city = pick_value(lambda k: f"rcity-{k}", uid, before)
    zone = pick_value(lambda k: f"rzone-{k}", uid, after, avoid={draw(city, before)})
    unchanged = switchback_experiment(431, "exp_sb_refresh_unchanged", [("store_id", "city")], 3)
    unchanged.update(pick_seeds(city, uid, unchanged, avoid={0}))
    return [Scenario(
        "Refresh - A Changed Cluster Map Re-Resolves And Re-Exposes",
        (f"city={city!r} draws {draw(city, before)}. A refresh changes the cluster path to zone ({zone!r} draws "
         f"{draw(zone, after)}): a new epoch, so the next treatment re-resolves and records a new exposure even "
         "though the period is unchanged. An unchanged switchback keeps its cached assignment and exposes once."),
        [before, unchanged], {"store_id": uid},
    ).attr("city", city).attr("zone", zone).treat(before, unchanged).publish()
        .refresh([after, unchanged]).treat(after, unchanged).publish().build()]


def dedup_and_payload_scenarios() -> list:
    uid = pick_uid("payload", QUIET3)
    payload = switchback_experiment(440, "exp_sb_payload", [("store_id", "city")], 3)
    baseline = experiment(441, "exp_sb_payload_baseline", "store_id", 3)
    city = pick_value(lambda k: f"pay-{k}", uid, payload)
    variant = draw(city, payload)
    baseline_variant = unit_variant(uid, baseline["split"], baseline["seedHi"], baseline["seedLo"])
    units_published = [{"type": "store_id", "uid": hash_unit(uid)}]
    sc = context_scenario(
        "Exposure And Publish Payload Carry No New Fields",
        ("A unit experiment on the same unit is exposed and published first as the baseline. The switchback "
         "exposure and its publish event must then have exactly the baseline's fields at every depth "
         "(sameShapeAs), with every exposure field value pinned, so a switchback-only field such as a period "
         "index or cluster key fails the scenario. A second treatment in the same period exposes nothing."),
        [payload, baseline], {"store_id": uid},
        [
            attribute_step("city", city),
            treatment_step(baseline, baseline_variant, exposure(baseline, baseline_variant)),
            {"action": "publish", "params": {}, "expect": {"result": None, "events": [{"type": "publish", "data": {
                "hashed": True, "units": units_published, "exposures": [exposure(baseline, baseline_variant)]}}]}},
            {"action": "treatment", "params": {"experimentName": payload["name"]},
             "expect": {"result": variant, "events": [
                 {"type": "exposure", "sameShapeAs": 2, "data": exposure(payload, variant)}]}},
            treatment_step(payload, variant),
            {"action": "publish", "params": {}, "expect": {"result": None, "events": [
                {"type": "publish", "sameShapeAs": 3, "data": {
                    "hashed": True, "units": units_published, "exposures": [exposure(payload, variant)]}}]}},
        ])
    sc["_draws"] = [(city, uid, payload)]
    return [sc]


def inertness_scenarios() -> list:
    """Ungated: must pass on every SDK. Unit experiments are served exactly as without any
    switchback data; the switchback experiment itself is never queried, because a non-capable
    SDK serves it a sticky unit assignment whose value differs per SDK."""
    seed_hi, seed_lo, split = ARMS[2]
    probe = switchback_experiment(130, "probe", [("session_id", "city")], 2)

    # A uid whose unit variant is 1 while the cluster draw of the city it sits in is 0: a unit
    # experiment carrying switchback fields that was served as a switchback would differ.
    session_uid = pick_uid("inert", lambda u: unit_variant(u, split, seed_hi, seed_lo) == 1)
    city = next(f"inert-city-{k}" for k in range(10_000) if draw(f"inert-city-{k}", probe) == 0)
    store_uid = "inert-store-0"
    sb_fields = OrderedDict(
        switchbackClusters=OrderedDict([("session_id", "city"), ("store_id", "city")]),
        switchbackClusterValues=[city],
        switchbackStartAt=ANCHOR_PERIOD_0,
        switchbackPeriodMs=PERIOD_MS,
        switchbackCarryoverDurationMs=0,
        switchbackBurnInDurationMs=0,
        switchbackBurnOutDurationMs=0,
        switchbackDesignType="balanced_block",
        switchbackEstimand="global_average_treatment_effect",
        switchbackClusterUnitType="session_id",
        switchbackClusterCount=10,
        switchbackClusterAttribute="city",
    )

    switchback = switchback_experiment(130, "exp_inert_switchback", [("store_id", "city")], 2)
    plain = experiment(131, "exp_inert_unit_plain", "session_id", 2)
    typed_unit = experiment(132, "exp_inert_unit_typed", "session_id", 2, randomizationType="unit", **sb_fields)
    untyped = experiment(133, "exp_inert_unit_untyped", "session_id", 2, **sb_fields)
    same_unit = experiment(134, "exp_inert_unit_store", "store_id", 2)
    experiments = [switchback, plain, typed_unit, untyped, same_unit]
    units = {"session_id": session_uid, "store_id": store_uid}

    # Each unit experiment is exposed and published on its own. The plain experiment (steps 2 and
    # 3) is the shape baseline: the others' exposure and publish events must have exactly its
    # fields at every depth, and every published exposure is pinned in full.
    steps = [attribute_step("city", city)]
    for exp, uid in ((plain, session_uid), (typed_unit, session_uid), (untyped, session_uid), (same_unit, store_uid)):
        variant = unit_variant(uid, exp["split"], exp["seedHi"], exp["seedLo"])
        exposed = exposure(exp, variant)
        treatment = treatment_step(exp, variant, exposed)
        publish = {"action": "publish", "params": {},
                   "expect": {"result": None, "events": [{"type": "publish", "data": {"exposures": [exposed]}}]}}
        if len(steps) > 1:
            treatment["expect"]["events"][0]["sameShapeAs"] = 2
            publish["expect"]["events"][0]["sameShapeAs"] = 3
        steps += [treatment, publish]

    scenario = context_scenario(
        "Inertness - Unit Experiments Unaffected By Switchback Data",
        ("INERTNESS BASELINE (must pass on every SDK, switchback-aware or not): the payload carries a "
         "switchback experiment and unit experiments, two of which also carry every R33 switchback wire field "
         "(an ordered switchbackClusters over both context units, switchbackClusterValues admitting the city "
         "that is set) and the abandoned Phase 1E fields (one with randomizationType \"unit\", one with it "
         "absent). Each unit experiment's treatment, exposure and published exposure equal plain unit "
         "assignment in every field; the uid's unit variant is 1 while the city's cluster draw is 0, so serving "
         "a unit experiment as a switchback fails. Each exposure and publish event has exactly the fields of the "
         "plain unit experiment's at every depth. The switchback experiment is never queried: an SDK without "
         "switchback serves it a sticky unit assignment that is documented and SDK-specific."),
        experiments, units, steps)
    del scenario["requires"]
    return [scenario]


# ---------------------------------------------------------------------------
# Rule map, discrimination checks and assembly
# ---------------------------------------------------------------------------

# Each R33 rule -> the scenario names (without number) that discriminate it. Reported in README and
# checked: every name must exist in the group.
RULES = OrderedDict([
    ("assignSwitchback / fisherYatesPermutation golden vectors", [
        "Golden Vectors - assignSwitchback - 2 Arms", "Golden Vectors - assignSwitchback - 3 Arms",
        "Golden Vectors - assignSwitchback - 10 Arms", "Golden Vectors - fisherYatesPermutation - 2 Arms",
        "Golden Vectors - fisherYatesPermutation - 3 Arms", "Golden Vectors - fisherYatesPermutation - 10 Arms"]),
    ("single source, single unit; raw value is the key; periodIndex = floorDiv(now - startAt, periodMs)", [
        "Single Source - Raw Attribute Value Is The Cluster Key"]),
    ("two sources: lower-declared source serves whatever the context unit order (an SDK context holds one uid "
     "per unit type, so two units of one type cannot be expressed)", [
        "Declared Order - Lower-Declared Source Serves Whatever The Context Unit Order"]),
    ("agreement serves", ["Agreement - Equal Values From Two Units Serve The Cluster"]),
    ("disagreement => ineligible, no exposure", [
        "Disagreement - Different Values Are An Ineligible Control With No Exposure"]),
    ("unit without a value is skipped", ["Unit Without A Value Is Skipped - The Next Unit Serves"]),
    ("normalisation: strings, whole numbers as decimal text; float/bool/object/array/empty/null => no value", [
        "Values - Strings And Whole Numbers Only"]),
    ("attribute paths: every attribute-path-cases.json case, nested/array, '.' rejected, index position", [
        "Attribute Paths - Platform Path Cases And Malformed Paths"]),
    ("switchbackClusterValues: in / out / null / []", ["Allow-List - In, Out, Null And Empty"]),
    ("pre-anchor, unaligned anchor, missing anchor, bad period, <2 arms => silent ineligible control", [
        "Schedule - Pre-Anchor And Unservable Schedules Are Silent Controls"]),
    ("traffic miss on an eligible cluster => recorded eligible=false; traffic hashed on the serving unit", [
        "Traffic - Miss On An Eligible Cluster Is Recorded Eligible=False Under The Serving Unit"]),
    ("precedence: override, assignment rule (also pre-anchor), strict audience, full-on (also pre-anchor), custom", [
        "Precedence - Override, Assignment Rule, Strict Audience, Full-On And Custom Over The Draw"]),
    ("holdouts never apply", ["Holdouts Never Apply To A Switchback"]),
    ("missing/empty cluster map => legacy ineligible control; no linked unit => not applicable; "
     "Phase 1E fields ignored", [
        "Legacy Path - Missing Or Empty Cluster Map, No Linked Unit, Phase 1E Fields Ignored"]),
    ("refresh with a changed cluster map re-resolves", ["Refresh - A Changed Cluster Map Re-Resolves And Re-Exposes"]),
    ("one exposure per period; exposure/publish payload has no switchback fields", [
        "Exposure And Publish Payload Carry No New Fields"]),
    ("ungated inertness: unit experiments carrying switchback fields are served as unit experiments", [
        "Inertness - Unit Experiments Unaffected By Switchback Data"]),
])


class Fixtures(NamedTuple):
    golden: dict
    paths: dict


def load_fixtures() -> Fixtures:
    """Every copied fixture, each verified against the reference; builders read only these."""
    golden = load_golden()
    verify_reference(golden)
    paths = load_path_cases()
    verify_path_reference(paths)
    return Fixtures(golden, paths)


def builders(fixtures: Fixtures) -> list:
    return [
        lambda: golden_scenarios(fixtures.golden),
        single_source_scenarios, declared_order_scenarios, agreement_disagreement_scenarios,
        value_normalisation_scenarios, lambda: path_scenarios(fixtures.paths), allow_list_scenarios,
        schedule_scenarios, traffic_scenarios, precedence_scenarios, holdout_scenarios, legacy_scenarios,
        refresh_scenarios, dedup_and_payload_scenarios, inertness_scenarios,
    ]


def is_context_scenario(scenario: dict) -> bool:
    return scenario["steps"][0]["action"] == "createContextWith"


def strip_shape_refs(expect: dict) -> dict:
    expect = copy.deepcopy(expect)
    for event in expect.get("events", []):
        event.pop("sameShapeAs", None)
    return expect


def failing_steps(scenario: dict, mutant: Optional[str]) -> list:
    """Step indices whose checked-in expectation the model under `mutant` does not reproduce."""
    expected = [strip_shape_refs(step["expect"]) for step in scenario["steps"][1:]]
    actual, _ = replay(scenario, mutant)
    return [i + 1 for i, (e, a) in enumerate(zip(expected, actual)) if e != a]


def check_discrimination(scenarios: list, draws: dict) -> dict:
    """Generation-time mutant replay. Every eligible schedule draw must be nonzero and differ from
    every wrong cluster-key model and the sticky unit assignment of its serving uid; the correct
    model must reproduce every context scenario; every mutant must fail at least one scenario.
    Returns mutant -> names of the scenarios it fails."""
    for name, entries in draws.items():
        for key, uid, exp in entries:
            variant = draw(key, exp)
            wrong = {m: v for m, v in wrong_key_variants(key, uid, exp).items() if v == variant}
            if variant == 0 or wrong:
                raise SystemExit(f"{name}: draw of {key!r} for {exp['name']} is {variant}, equal to {wrong}")
    context = [s for s in scenarios if is_context_scenario(s)]
    for scenario in context:
        if failing_steps(scenario, None):
            raise SystemExit(f"model does not reproduce {scenario['name']}: steps {failing_steps(scenario, None)}")
    caught = {m: [s["name"] for s in context if failing_steps(s, m)] for m in ALL_MUTANTS}
    survivors = [m for m, names in caught.items() if not names]
    if survivors:
        raise SystemExit(f"mutants no scenario catches: {survivors}")
    inert = [s for s in context if "requires" not in s]
    if len(inert) != 1 or failing_steps(inert[0], "unit_experiment_as_switchback") == []:
        raise SystemExit("the inertness scenario must catch a unit experiment served as a switchback")
    return caught


def build_group(fixtures: Fixtures, first_number: int, with_report: bool = False):
    scenarios, draws = [], {}
    for builder in builders(fixtures):
        for scenario in builder():
            scenario["name"] = f"{first_number + len(scenarios)} - Switchback - {scenario['name']}"
            draws[scenario["name"]] = scenario.pop("_draws", [])
            scenarios.append(scenario)
    names = [s["name"].split(" - Switchback - ", 1)[1] for s in scenarios]
    missing = [n for ns in RULES.values() for n in ns if n not in names]
    if missing:
        raise SystemExit(f"RULES names no scenario: {missing}")
    caught = check_discrimination(scenarios, draws)
    return (scenarios, caught) if with_report else scenarios


def rule_map(scenarios: list) -> list:
    """(rule, [scenario numbers]) for README and the report."""
    numbers = {s["name"].split(" - Switchback - ", 1)[1]: int(s["name"].split(" - ", 1)[0]) for s in scenarios}
    return [(rule, [numbers[n] for n in names]) for rule, names in RULES.items()]


def write(scenarios: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Switchback scenario group.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    parser.add_argument("--rules", action="store_true", help="print the rule -> scenario map and mutant results")
    args = parser.parse_args()

    fixtures = load_fixtures()

    with open(CANONICAL, encoding="utf-8") as src:
        existing = json.load(src)
    kept = [s for s in existing if GROUP_MARKER not in s["name"]]
    group, caught = build_group(fixtures, len(kept) + 1, with_report=True)
    updated = kept + group

    if args.rules:
        for rule, numbers in rule_map(group):
            print(f"{rule}: {numbers}")
        for mutant, names in caught.items():
            print(f"mutant {mutant}: fails {[int(n.split(' - ', 1)[0]) for n in names]}")

    if args.check:
        if updated != existing:
            print("test_scenarios_complete.json is stale: run generate_switchback_scenarios.py")
            return 1
        print(f"up to date: {len(group)} switchback scenarios")
        return 0

    write(updated, CANONICAL)
    print(f"wrote {len(updated)} scenarios ({len(group)} switchback, {group[0]['name'].split(' - ')[0]}-"
          f"{group[-1]['name'].split(' - ')[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
