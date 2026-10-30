#!/usr/bin/env python3
"""Generate the Switchback scenario group of test_scenarios_complete.json.

The group is derived, never hand-edited. Its inputs are byte-for-byte copies of the platform's
lib/utils/src/test/resources/switchback/ fixtures in fixtures/switchback/:
golden-vectors.json (assignSwitchback and fisherYatesPermutation), bucket-golden-vectors.json
(bucketOf, the unit key and attribute keys) and attribute-path-cases.json (the attribute path
grammar). The reference below must reproduce every one of them before anything is written.

  * Golden-vector scenarios drive every assign and permutation case through the wrapper's
    /switchback/assign and /switchback/permutation endpoints.
  * Bucket-vector scenarios drive every bucketOf case through /switchback/bucket.
  * Context scenarios exercise switchback serving through createContextWith/treatment/publish.
    Each cluster key is the SDK's hashed unit or, with switchbackClusterAttribute, the context
    attribute at that path; its bucketOf bucket is the cluster unit of assignSwitchback. Uids and
    attribute values are chosen so the expected variant differs from what sticky assignment,
    per-value clustering, another bucket count or a little-endian bucket would serve.
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
from typing import NamedTuple, Optional

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


# Cluster buckets: every switchback hashes its cluster key (the unit hash, or a normalized
# attribute value) into one of CLUSTER_COUNTS buckets, and the bucket number is the cluster
# passed to assign_switchback. Pinned by fixtures/switchback/bucket-golden-vectors.json.
CLUSTER_COUNTS = (10, 50, 100)
BUCKET_CASES = 702
BUCKET_GOLDEN = os.path.join(HERE, "fixtures", "switchback", "bucket-golden-vectors.json")
PATH_CASES = os.path.join(HERE, "fixtures", "switchback", "attribute-path-cases.json")


def signed32(value: int) -> int:
    return ((value + (1 << 31)) & MASK32) - (1 << 31)


def cluster_digest(key: str, seed: int) -> bytes:
    """MD5(utf8("sbcluster␟<seedHi>:<seedLo>␟" + key)), where seedHi and seedLo are the signed
    32-bit halves of the 64-bit experiment seed in base 10."""
    prefix = f"sbcluster{SEPARATOR}{signed32(seed >> 32)}:{signed32(seed)}{SEPARATOR}"
    return hashlib.md5((prefix + key).encode("utf-8")).digest()


def bucket_of(key: str, seed: int, count: int) -> int:
    """The first four bytes of cluster_digest(key, seed), big-endian, mod count."""
    if count not in CLUSTER_COUNTS:
        raise ValueError(f"cluster count must be one of {CLUSTER_COUNTS}, got {count}")
    return int.from_bytes(cluster_digest(key, seed)[:4], "big") % count


def normalize_attribute(value) -> Optional[str]:
    """Bucket key of an attribute value: a non-empty string as-is, an integer in base 10, and None
    (ineligible) for anything else, floats such as 2.0 and booleans included."""
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


def parse_attribute_path(path: str) -> list:
    """(key, index) segments: '/' separates, a segment quoted with ' or " is a literal key, and an
    unquoted integer segment is an array index. An unterminated quoted tail is dropped."""
    segments, i = [], 0
    while i < len(path):
        if path[i] == "/":
            i += 1
            continue
        if path[i] in "'\"":
            end = path.find(path[i], i + 1)
            if end < 0:
                break
            segments.append((path[i + 1:end], None))
            i = end + 1
        else:
            end = path.find("/", i)
            stop = len(path) if end < 0 else end
            segments.append((path[i:stop], _index_segment(path[i:stop])))
            i = stop
    return segments


def supported_attribute_path(path: str) -> Optional[list]:
    """Segments of a path both serving and analysis accept, else None: every index is
    non-negative and sits between two key segments."""
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


def load_bucket_fixtures() -> tuple:
    with open(BUCKET_GOLDEN, encoding="utf-8") as src:
        buckets = json.load(src)
    with open(PATH_CASES, encoding="utf-8") as src:
        paths = json.load(src)
    if len(buckets["buckets"]) != BUCKET_CASES:
        raise SystemExit(f"expected {BUCKET_CASES} bucket vectors")
    return buckets, paths


def verify_bucket_reference(buckets: dict, paths: dict) -> None:
    for case in buckets["buckets"]:
        actual = bucket_of(case["key"], exact_long(case["seed"]), case["count"])
        if actual != case["expectedBucket"]:
            raise SystemExit(f"bucket_of disagrees with bucket vector {case}: got {actual}")
    for case in buckets["unitKeys"]:
        actual = hash_unit(cluster_unit_text(case["unitUid"]))
        if actual != case["key"]:
            raise SystemExit(f"hash_unit disagrees with unit key vector {case}: got {actual}")
    for case in buckets["attributeKeys"]:
        actual = normalize_attribute(case["value"])
        if actual != case["key"]:
            raise SystemExit(f"normalize_attribute disagrees with attribute key vector {case}: got {actual!r}")
    for case in paths["cases"]:
        segments = supported_attribute_path(case["path"])
        value = resolve_attribute_path(paths["attributes"], parse_attribute_path(case["path"]))
        if (segments is not None) != case["supported"] or value != case["value"]:
            raise SystemExit(f"attribute path reference disagrees with {case}: supported={segments is not None}, "
                             f"value={value!r}")


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

# Path cases whose key collides with a mutant under every (arms, count) at ARMS' seeds use
# (PATH_SEED_HI, k) for the first k that discriminates.
PATH_SEED_HI = 1_234_567


def key_variant(key: str, period: int, arm_count: int, count: int, seeds: Optional[tuple] = None) -> int:
    """The switchback draw for a cluster key: its bucket is the cluster unit of assignSwitchback.
    seeds is (seedHi, seedLo), ARMS[arm_count]'s by default."""
    seed = experiment_seed(*(seeds or ARMS[arm_count][:2]))
    return assign_switchback(str(bucket_of(key, seed, count)), period, seed, arm_count)


def switchback_variant(uid: str, period: int, arm_count: int, count: int) -> int:
    return key_variant(hash_unit(uid), period, arm_count, count)


def bucket_of_little_endian(key: str, seed: int, count: int) -> int:
    """bucket_of with the first four digest bytes read little-endian: a byte-order mutant."""
    return int.from_bytes(cluster_digest(key, seed)[:4], "little") % count


def wrong_key_variants(key: str, period: int, arm_count: int, count: int, seeds: Optional[tuple] = None) -> set:
    """What an SDK that drew from the key itself (per-value clustering), applied another bucket
    count or read the digest little-endian would serve."""
    seed = experiment_seed(*(seeds or ARMS[arm_count][:2]))
    wrong = {assign_switchback(key, period, seed, arm_count)}
    wrong |= {key_variant(key, period, arm_count, other, seeds) for other in CLUSTER_COUNTS if other != count}
    wrong.add(assign_switchback(str(bucket_of_little_endian(key, seed, count)), period, seed, arm_count))
    return wrong


def sticky_variant(uid: str, arm_count: int, seeds: Optional[tuple] = None) -> int:
    """What an SDK ignoring randomizationType would serve: the ordinary unit assignment."""
    seed_hi, seed_lo = seeds or ARMS[arm_count][:2]
    return unit_variant(uid, ARMS[arm_count][2], seed_hi, seed_lo)


def pick_uid(prefix: str, period: int, arm_count: int, count: int) -> str:
    """First '<prefix>-<k>' whose switchback variant is nonzero and differs from the sticky unit
    variant, from per-value clustering and from every other bucket count, so neither an ineligible
    fallback, plain unit assignment nor the wrong cluster model can pass by accident."""
    for k in range(10_000):
        uid = f"{prefix}-{k}"
        variant = switchback_variant(uid, period, arm_count, count)
        if (variant not in (0, sticky_variant(uid, arm_count))
                and variant not in wrong_key_variants(hash_unit(uid), period, arm_count, count)):
            return uid
    raise SystemExit(f"no discriminating uid for {prefix}")


def pick_value(make, uid: str, period: int, arm_count: int, count: int, avoid=(), avoid_keys=lambda value: ()):
    """First make(k) whose attribute-source variant is nonzero and differs from the unit-source
    draw for uid, from the sticky unit assignment of uid, from per-value clustering, from every
    other bucket count, from a little-endian bucket, from `avoid` and from the draw of every
    wrong rendering in avoid_keys(value)."""
    unit_draws = (0, switchback_variant(uid, period, arm_count, count), sticky_variant(uid, arm_count))
    for k in range(10_000):
        value = make(k)
        key = normalize_attribute(value)
        if key is None:
            raise SystemExit(f"attribute value {value!r} has no bucket key")
        variant = key_variant(key, period, arm_count, count)
        if (variant not in unit_draws and variant not in avoid
                and variant not in wrong_key_variants(key, period, arm_count, count)
                and all(key_variant(wrong, period, arm_count, count) != variant for wrong in avoid_keys(value))):
            return value
    raise SystemExit(f"no discriminating attribute value for {make(0)!r}")


def number_renderings(value: int) -> list:
    """Keys an SDK would hash if it wrapped the integer to int32 or read it as a double:
    "-294967290", "4000000006.0", "4.000000006E9" (Java), "4.000000006e+09" (C/Go %g)."""
    digits = str(abs(value)).rstrip("0") or "0"
    exponent = len(str(abs(value))) - 1
    mantissa = ("-" if value < 0 else "") + digits[0] + "." + (digits[1:] or "0")
    return [str(signed32(value)), f"{value}.0", f"{mantissa}E{exponent}", f"{mantissa}e{exponent:+03d}"]


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


def switchback_experiment(exp_id: int, name: str, unit_type: str, arm_count: int, anchor, count: Optional[int],
                          attribute: Optional[str] = None, **overrides) -> dict:
    """count and attribute are switchbackClusterCount and switchbackClusterAttribute; None omits
    the field, and a null attribute hashes the unit."""
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
    if count is not None:
        fields["switchbackClusterCount"] = count
    if attribute is not None:
        fields["switchbackClusterAttribute"] = attribute
    fields.update(overrides)
    return experiment(exp_id, name, unit_type, arm_count, **fields)


def exposure(exp: dict, variant: int, assigned=True, eligible=True, overridden=False, full_on=False,
             audience_mismatch=False) -> dict:
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
        "audienceMismatch": audience_mismatch,
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


def attribute_step(name: str, value) -> dict:
    return {"action": "attribute", "params": {"name": name, "value": value}, "expect": {"result": None, "events": []}}


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


def bucket_vector_scenarios(buckets: dict) -> list:
    """One scenario per bucket count: every bucket vector of that count through /switchback/bucket."""
    scenarios = []
    for count in CLUSTER_COUNTS:
        steps = [{
            "action": "switchbackBucket",
            "params": {"key": c["key"], "seed": c["seed"], "count": count},
            "expect": {"result": c["expectedBucket"], "events": []},
        } for c in buckets["buckets"] if c["count"] == count]
        scenarios.append({
            "name": f"Bucket Vectors - bucketOf - {count} Buckets",
            "description": (f"Every count-{count} case ({len(steps)}) of the platform bucket golden vectors, driven "
                            "through POST /switchback/bucket: int(MD5_HEX(utf8(\"sbcluster\" + U+001F + seedHi + "
                            "\":\" + seedLo + U+001F + key))[:8], 16) mod count. The 64-bit seed travels as a "
                            "decimal string; seedHi/seedLo are its signed 32-bit halves."),
            "contextData": {"experiments": []},
            "steps": steps,
            "requires": [CAPABILITY],
        })
    return scenarios


# Bucket count per cluster letter, so each period scenario covers every allowed count.
LETTER_COUNTS = {"a": 10, "b": 50, "c": 100}


def period_scenarios() -> list:
    scenarios = []
    for period, anchor in ((0, ANCHOR_PERIOD_0), (1, ANCHOR_PERIOD_1)):
        for arm_count in (2, 3, 10):
            experiments, units, steps, exposed = [], {}, [], []
            for slot, letter in enumerate("abc"):
                unit_type = f"store_{letter}"
                count = LETTER_COUNTS[letter]
                uid = pick_uid(f"p{period}-a{arm_count}-{letter}", period, arm_count, count)
                exp = switchback_experiment(100 + slot, f"exp_switchback_{letter}", unit_type, arm_count, anchor,
                                            count)
                variant = switchback_variant(uid, period, arm_count, count)
                experiments.append(exp)
                units[unit_type] = uid
                exposed.append(exposure(exp, variant))
                steps.append(treatment_step(exp, variant, exposed[-1]))
            steps.append(publish_step(exposed))
            scenarios.append(context_scenario(
                f"Period {period} - {arm_count} Arms - Unit Buckets 10, 50, 100",
                (f"switchbackStartAt {anchor} with switchbackPeriodMs {PERIOD_MS} (400,000 h) puts now in period "
                 f"{period} until 2069. Three unit-sourced switchbacks with switchbackClusterCount 10, 50 and 100: "
                 f"each variant is assignSwitchback(str(bucketOf(hashUnit(uid), seed, count)), {period}, seed, "
                 f"{arm_count}) with seed = (seedHi << 32) | seedLo. Every uid is chosen so that variant is nonzero "
                 "and differs from the sticky unit assignment, from per-value clustering on hashUnit(uid), from "
                 "the other two counts and from a little-endian bucket."),
                experiments, units, steps))
    return scenarios


def shared_bucket_scenarios() -> list:
    """Two units in one bucket share the variant although their per-value draws differ."""
    count, arm_count = 10, 3
    seed_hi, seed_lo, _ = ARMS[arm_count]
    seed = experiment_seed(seed_hi, seed_lo)
    first = pick_uid("shared-a", 0, arm_count, count)
    bucket = bucket_of(hash_unit(first), seed, count)
    per_value = assign_switchback(hash_unit(first), 0, seed, arm_count)
    second = next(uid for uid in (f"shared-b-{k}" for k in range(10_000))
                  if bucket_of(hash_unit(uid), seed, count) == bucket
                  and assign_switchback(hash_unit(uid), 0, seed, arm_count) != per_value)
    variant = switchback_variant(first, 0, arm_count, count)
    exp_a = switchback_experiment(105, "exp_switchback_shared_a", "store_a", arm_count, ANCHOR_PERIOD_0, count)
    exp_b = switchback_experiment(106, "exp_switchback_shared_b", "store_b", arm_count, ANCHOR_PERIOD_0, count)
    exposed = [exposure(exp_a, variant), exposure(exp_b, variant)]
    return [context_scenario(
        "Units In One Bucket Share The Variant",
        (f"Two experiments with the same seed and switchbackClusterCount {count} on different unit types. Both "
         f"uids hash into bucket {bucket}, so both serve the bucket's period-0 variant {variant}, while "
         "per-value clustering on the two hashed uids would serve different variants."),
        [exp_a, exp_b],
        {"store_a": first, "store_b": second},
        [
            treatment_step(exp_a, variant, exposed[0]),
            treatment_step(exp_b, variant, exposed[1]),
            publish_step(exposed),
        ])]


def eligibility_scenarios() -> list:
    pre_anchor = switchback_experiment(110, "exp_switchback_pre_anchor", "store_id", 3, ANCHOR_FUTURE, 100)
    no_anchor = switchback_experiment(111, "exp_switchback_no_anchor", "store_id", 3, None, 100)
    no_unit = switchback_experiment(112, "exp_switchback_no_cluster_unit", "warehouse_id", 3, ANCHOR_PERIOD_0, 100)
    no_count = switchback_experiment(113, "exp_switchback_no_cluster_count", "store_id", 3, ANCHOR_PERIOD_0, None)
    bad_count = switchback_experiment(114, "exp_switchback_cluster_count_1000", "store_id", 3, ANCHOR_PERIOD_0,
                                      1000)
    # Every ineligible case must differ from what a sticky SDK, or one falling back to any allowed
    # count, would serve.
    uid = next(uid for uid in (f"eligibility-{k}" for k in range(10_000))
               if sticky_variant(uid, 3) != 0 and all(switchback_variant(uid, 0, 3, c) for c in CLUSTER_COUNTS))

    def ineligible_steps(experiments):
        steps = []
        for exp in experiments:
            steps += [treatment_step(exp, 0, exposure(exp, 0, eligible=False)), treatment_step(exp, 0)]
        return steps + [publish_step([exposure(exp, 0, eligible=False) for exp in experiments])]

    def ineligible(exp, description):
        return context_scenario(f"Ineligible - {exp['name']}", description, [exp], {"store_id": uid},
                                ineligible_steps([exp]))

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
        context_scenario(
            "Ineligible - Cluster Count Not 10, 50 Or 100",
            ("switchbackClusterCount absent, and switchbackClusterCount 1000: only 10, 50 and 100 are servable, "
             "so each experiment is ineligible (variant 0, assigned=true, eligible=false, exposed once) and "
             "never falls back to per-value clustering or to another count. The uid draws nonzero under every "
             "allowed count and under sticky unit assignment."),
            [no_count, bad_count],
            {"store_id": uid},
            ineligible_steps([no_count, bad_count])),
    ]


def serving_scenarios() -> list:
    uid = pick_uid("serving", 0, 3, 100)
    variant = switchback_variant(uid, 0, 3, 100)
    dedup = switchback_experiment(120, "exp_switchback_dedup", "store_id", 3, ANCHOR_PERIOD_0, 100)
    # Full-on and the override each serve a variant the switchback draw would not, so neither can
    # pass by serving the draw.
    other_variant = next(v for v in (1, 2) if v != variant)
    full_on = switchback_experiment(121, "exp_switchback_full_on", "store_id", 3, ANCHOR_PERIOD_0, 100,
                                    fullOnVariant=other_variant)
    overridden = switchback_experiment(122, "exp_switchback_override", "store_id", 3, ANCHOR_PERIOD_0, 100)
    override_variant = other_variant
    payload = switchback_experiment(123, "exp_switchback_payload", "store_id", 3, ANCHOR_PERIOD_0, 100)
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
            (f"The period-0 draw for this cluster is {variant}; fullOnVariant={other_variant} takes precedence "
             f"over it: variant {other_variant} with fullOn=true."),
            [full_on],
            {"store_id": uid},
            [
                treatment_step(full_on, other_variant, exposure(full_on, other_variant, full_on=True)),
                publish_step([exposure(full_on, other_variant, full_on=True)]),
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


def attribute_source_scenarios() -> list:
    """Bucket keys read from context attributes through switchbackClusterAttribute."""
    uid = pick_uid("attribute", 0, 3, 100)
    city = pick_value(lambda k: f"city-{k}", uid, 0, 3, 100)
    number = pick_value(lambda k: 4_000_000_000 + k, uid, 0, 2, 50, avoid_keys=number_renderings)
    nested = pick_value(lambda k: f"nested-{k}", uid, 0, 10, 10)
    other_item = pick_value(lambda k: f"item-b-{k}", uid, 0, 10, 100)
    first_item = pick_value(lambda k: f"item-a-{k}", uid, 0, 10, 100,
                            avoid={key_variant(other_item, 0, 10, 100)})
    sources = [
        (switchback_experiment(140, "exp_switchback_attr_string", "store_id", 3, ANCHOR_PERIOD_0, 100, "city"),
         "city", city, city),
        (switchback_experiment(141, "exp_switchback_attr_number", "store_id", 2, ANCHOR_PERIOD_0, 50,
                               "store_number"),
         "store_number", number, str(number)),
        (switchback_experiment(142, "exp_switchback_attr_nested", "store_id", 10, ANCHOR_PERIOD_0, 10, "geo/city"),
         "geo", {"city": nested, "region": "north"}, nested),
        (switchback_experiment(143, "exp_switchback_attr_array_index", "store_id", 10, ANCHOR_PERIOD_0, 100,
                               "items/0/city"),
         "items", [{"city": first_item}, {"city": other_item}], first_item),
    ]
    steps = [attribute_step(name, value) for _, name, value, _ in sources]
    exposed = []
    for exp, _, _, key in sources:
        variant = key_variant(key, 0, len(exp["split"]), exp["switchbackClusterCount"])
        exposed.append(exposure(exp, variant))
        steps.append(treatment_step(exp, variant, exposed[-1]))
    steps.append(publish_step(exposed))
    return [context_scenario(
        "Attribute Source - String, Whole Number, Nested And Array-Index Paths",
        ("Four attribute-sourced switchbacks on one store_id unit: switchbackClusterAttribute \"city\" (a "
         "top-level string, 100 buckets), \"store_number\" (a whole number above 2^31, keyed as its base-10 "
         "string, 50 buckets), \"geo/city\" (a nested key, 10 buckets) and \"items/0/city\" (an array index "
         "between two keys, 100 buckets). Each variant is assignSwitchback(str(bucketOf(key, seed, count)), 0, "
         "seed, arms); every value is chosen so that variant is nonzero and differs from the unit-sourced draw, "
         "from the sticky unit assignment, from per-value clustering on the key, from the other counts and from "
         "a little-endian bucket, and items[1].city would draw differently from items[0].city. The whole "
         "number's int32 wrap and its double renderings (\"<n>.0\", \"<m>E9\", \"<m>e+09\") would each serve "
         "a different variant."),
        [exp for exp, _, _, _ in sources],
        {"store_id": uid},
        steps)]


def attribute_ineligible_scenarios() -> list:
    """No bucket key: variant 0, assigned=true, eligible=false. Each experiment's count and arms are
    chosen so every lax reading of its value (and the unit hash) would draw nonzero."""
    uid = pick_uid("attribute-ineligible", 0, 3, 100)
    cases = [
        ("missing", "absent_attr", None, "absent_attr", []),
        ("empty", "empty_attr", "", "empty_attr", [""]),
        ("fraction", "fraction_attr", 1.5, "fraction_attr", ["1.5", "1"]),
        ("bool", "bool_attr", True, "bool_attr", ["true", "True", "1"]),
        ("object", "object_attr", {"city": "Porto"}, "object_attr", ['{"city":"Porto"}', "Porto"]),
        ("negative_index", "items", [{"city": "Lisbon"}, {"city": "Faro"}], "items/-1/city", ["Faro", "Lisbon"]),
    ]
    experiments, steps, exposed = [], [], []
    for offset, (label, name, value, path, lax_keys) in enumerate(cases):
        lax_keys = lax_keys + [hash_unit(uid)]
        config = next(((arms, count) for arms in (3, 10, 2) for count in CLUSTER_COUNTS
                       if all(key_variant(k, 0, arms, count) != 0 for k in lax_keys)), None)
        if config is None:
            raise SystemExit(f"no discriminating configuration for the {label} case")
        exp = switchback_experiment(150 + offset, f"exp_switchback_attr_{label}", "store_id", config[0],
                                    ANCHOR_PERIOD_0, config[1], path)
        experiments.append(exp)
        if value is not None:
            steps.append(attribute_step(name, value))
    for exp in experiments:
        exposed.append(exposure(exp, 0, eligible=False))
        steps += [treatment_step(exp, 0, exposed[-1]), treatment_step(exp, 0)]
    steps.append(publish_step(exposed))
    return [context_scenario(
        "Attribute Source - No Bucket Key Is Ineligible",
        ("Attribute-sourced switchbacks whose key is missing (never set), an empty string, a non-integral "
         "number (1.5), a boolean, an object, or behind the unsupported path \"items/-1/city\" (a negative "
         "index). Each serves variant 0 with assigned=true and eligible=false, exposed once, and never falls "
         "back to the unit. Count and arms per experiment are chosen so that the unit-sourced draw and every "
         "lax reading of the value (\"1.5\", \"true\", the last array element, ...) would serve a nonzero "
         "variant. An integral float such as 2.0 is not a case here: JSON-number runtimes (JavaScript and its "
         "frameworks) cannot tell 2.0 from 2. The integral-float rule is covered by bucket-golden-vectors.json "
         "attributeKeys and each typed SDK's own suite."),
        experiments,
        {"store_id": uid},
        steps)]


def index_position_cases() -> tuple:
    """Paths the grammar rejects for the index position (an index at the tail, or two in a row),
    each over an attribute a lax reader would resolve: derived from the reference, so the copied
    fixture stays byte-identical."""
    attributes = {"tags": ["a", "b"], "m": [[{"city": "x"}]]}
    cases = []
    for path in ("tags/0", "m/0/0/city"):
        lax_value = resolve_attribute_path(attributes, parse_attribute_path(path))
        if supported_attribute_path(path) is not None or lax_value is None:
            raise SystemExit(f"index-position case {path} must be unsupported and resolve laxly")
        cases.append({"path": path, "supported": False, "value": lax_value})
    return attributes, cases


def attribute_path_case_scenarios(paths: dict) -> list:
    """Every case of fixtures/switchback/attribute-path-cases.json plus the index-position cases: a
    supported path with a value is served from that value's bucket, anything else is ineligible."""
    extra_attributes, extra_cases = index_position_cases()
    attributes = {**paths["attributes"], **extra_attributes}
    cases = paths["cases"] + extra_cases
    uid = pick_uid("attribute-path", 0, 3, 100)
    unit_key = hash_unit(uid)
    leaves = sorted({case["value"] for case in cases if case["value"] is not None}
                    | {"Lisbon", "Faro", "Porto", "minus-one-key", "plus-one-key"})
    candidates = leaves + [unit_key]
    experiments, exposed, steps = [], [], [attribute_step(n, v) for n, v in attributes.items()]
    for i, case in enumerate(cases):
        key = case["value"] if case["supported"] else None

        # Hard: an eligible case draws nonzero and unlike the unit-sourced draw, the sticky unit
        # assignment, per-value clustering, the other counts and a little-endian bucket; an ineligible
        # one sits where every leaf and the unit would draw nonzero. Soft: the most other leaves
        # drawing differently.
        def score(arms, count, seeds):
            served = 0 if key is None else key_variant(key, 0, arms, count, seeds)
            others = [key_variant(c, 0, arms, count, seeds) for c in candidates if c != key]
            if key is None:
                return 0 if 0 not in others else None
            if (served in (0, key_variant(unit_key, 0, arms, count, seeds), sticky_variant(uid, arms, seeds))
                    or served in wrong_key_variants(key, 0, arms, count, seeds)):
                return None
            return sum(other != served for other in others)

        # The arm count's own seeds first; with 2 to 10 arms some keys collide with a mutant under
        # every count, and only then is the seed varied.
        for seeds in [None] + [(PATH_SEED_HI, k) for k in range(1, 100)]:
            scored = [(score(arms, count, seeds), arms, count) for arms in (10, 3, 2) for count in CLUSTER_COUNTS]
            scored = [entry for entry in scored if entry[0] is not None]
            if scored:
                break
        else:
            raise SystemExit(f"no discriminating configuration for path case {case}")
        _, arms, count = max(scored, key=lambda entry: entry[0])
        overrides = {} if seeds is None else {"seedHi": seeds[0], "seedLo": seeds[1]}
        exp = switchback_experiment(160 + i, f"exp_switchback_path_{i}", "store_id", arms, ANCHOR_PERIOD_0,
                                    count, case["path"], **overrides)
        experiments.append(exp)
        variant = 0 if key is None else key_variant(key, 0, arms, count, seeds)
        exposed.append(exposure(exp, variant, eligible=key is not None))
        steps.append(treatment_step(exp, variant, exposed[-1]))
    steps.append(publish_step(exposed))
    return [context_scenario(
        "Attribute Source - Platform Attribute Path Cases",
        ("Every case of the platform's attribute-path-cases.json, one experiment per path, with the fixture's "
         "attributes set as top-level attributes, plus two index-position cases over tags=[\"a\",\"b\"] and "
         "m=[[{\"city\":\"x\"}]]: \"tags/0\" (an index at the tail) and \"m/0/0/city\" (two indices in a row) "
         "are unsupported although a lax reader would resolve them to \"a\" and \"x\". A supported path that "
         "resolves to a value is served from that value's bucket; an unsupported path (a negative or "
         "misplaced index) or a path that resolves to nothing (\"+0\" and \"\\u0660\" are keys, not indices) "
         "is ineligible. Count and arms per experiment are "
         "chosen so an eligible case draws nonzero and unlike the unit-sourced draw, the sticky unit "
         "assignment, per-value clustering, the other counts and a little-endian bucket, and unlike as many "
         "other leaves as possible; an ineligible case sits where every leaf and the unit would draw nonzero."),
        experiments,
        {"store_id": uid},
        steps)]


def attribute_timing_scenarios() -> list:
    """The cluster key is read once per period slot; wrappers cannot advance the clock, so the
    next-period re-read stays in the SDKs' own suites."""
    uid = pick_uid("attribute-timing", 0, 10, 100)
    first = pick_value(lambda k: f"first-{k}", uid, 0, 10, 100)
    first_variant = key_variant(first, 0, 10, 100)
    second = pick_value(lambda k: f"second-{k}", uid, 0, 10, 100, avoid={first_variant})
    if key_variant(second, 0, 10, 100) == first_variant:
        raise SystemExit("the mid-period attribute must draw a different variant than the first")
    # The audience re-exposure keeps the period's cached cluster key instead of re-reading city:
    # java-sdk SWITCHBACK.md ("The cluster key is read once per period slot ... even when an
    # audience change re-resolves the slot") and ContextSwitchbackClusterTest
    # setAttributeMidPeriodDoesNotReassignWithinThePeriod /
    # audienceRevalidationAfterSetAttributeKeepsThePeriodsBucket.
    audience = json.dumps({"filter": [{"eq": [{"var": "tier"}, {"value": "gold"}]}]}, separators=(",", ":"))
    moved = switchback_experiment(170, "exp_switchback_attr_mid_period", "store_id", 10, ANCHOR_PERIOD_0, 100,
                                  "city", audience=audience)
    late = switchback_experiment(171, "exp_switchback_attr_late", "store_id", 10, ANCHOR_PERIOD_0, 100, "zone")
    late_value = pick_value(lambda k: f"zone-{k}", uid, 0, 10, 100)
    return [
        context_scenario(
            "Attribute Source - Mid-Period setAttribute Keeps The Bucket",
            (f"city={first!r} draws variant {first_variant}. setAttribute(city, {second!r}) later in the same "
             f"period would draw {key_variant(second, 0, 10, 100)}, but the period's bucket is already fixed: "
             "the treatment stays the same with no new exposure. A non-strict audience flip (tier gold → "
             "silver) then re-resolves the slot and re-exposes with audienceMismatch=true, still on the "
             "first bucket. The next period reads the attribute again (not testable without clock control)."),
            [moved],
            {"store_id": uid},
            [
                attribute_step("tier", "gold"),
                attribute_step("city", first),
                treatment_step(moved, first_variant, exposure(moved, first_variant)),
                attribute_step("city", second),
                treatment_step(moved, first_variant),
                attribute_step("tier", "silver"),
                treatment_step(moved, first_variant, exposure(moved, first_variant, audience_mismatch=True)),
                publish_step([exposure(moved, first_variant),
                              exposure(moved, first_variant, audience_mismatch=True)]),
            ]),
        context_scenario(
            "Attribute Source - Attribute Set After An Ineligible Read Does Not Reassign Within The Period",
            (f"zone is unset at the first treatment, so the period resolves ineligible (variant 0, "
             f"eligible=false). setAttribute(zone, {late_value!r}), which would draw "
             f"{key_variant(late_value, 0, 10, 100)}, does not reassign within the period: the treatment stays "
             "0 with no new exposure."),
            [late],
            {"store_id": uid},
            [
                treatment_step(late, 0, exposure(late, 0, eligible=False)),
                attribute_step("zone", late_value),
                treatment_step(late, 0),
                publish_step([exposure(late, 0, eligible=False)]),
            ]),
    ]


def inertness_scenarios() -> list:
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
        "switchbackClusterCount": 10,
    }

    # A uid whose unit variant is 1 and whose switchback draw is 0: a unit experiment carrying
    # switchback fields that were served as a switchback, or fell back to control, would differ.
    def discriminating(k):
        uid = f"inert-{k}"
        return unit_variant(uid, split, seed_hi, seed_lo) == 1 and switchback_variant(uid, 0, 2, 10) == 0

    session_uid = next(f"inert-{k}" for k in range(10_000) if discriminating(k))
    store_uid = "inert-store-0"

    switchback = switchback_experiment(130, "exp_inert_switchback", "store_id", 2, ANCHOR_PERIOD_0, 100)
    plain = experiment(131, "exp_inert_unit_plain", "session_id", 2)
    typed_unit = experiment(132, "exp_inert_unit_typed", "session_id", 2, randomizationType="unit",
                            switchbackClusterAttribute="geo/city", **sb_fields)
    untyped = experiment(133, "exp_inert_unit_untyped", "session_id", 2, **sb_fields)
    same_unit = experiment(134, "exp_inert_unit_store", "store_id", 2)
    experiments = [switchback, plain, typed_unit, untyped, same_unit]
    units = {"session_id": session_uid, "store_id": store_uid}

    # Each unit experiment is exposed and published on its own. The plain experiment (steps 1 and
    # 2) is the shape baseline: the others' exposure and publish events must have exactly its
    # fields at every depth, and every published exposure is pinned in full.
    steps = []
    for exp, uid in ((plain, session_uid), (typed_unit, session_uid), (untyped, session_uid), (same_unit, store_uid)):
        variant = unit_variant(uid, exp["split"], exp["seedHi"], exp["seedLo"])
        exposed = exposure(exp, variant)
        treatment = treatment_step(exp, variant, exposed)
        publish = {"action": "publish", "params": {},
                   "expect": {"result": None, "events": [{"type": "publish", "data": {"exposures": [exposed]}}]}}
        if steps:
            treatment["expect"]["events"][0]["sameShapeAs"] = 1
            publish["expect"]["events"][0]["sameShapeAs"] = 2
        steps += [treatment, publish]

    scenario = context_scenario(
        "Inertness - Unit Experiments Unaffected By Switchback Data",
        ("INERTNESS BASELINE (must pass on every SDK, switchback-aware or not): the payload carries a "
         "switchback experiment and unit experiments, two of which also carry every switchback wire field, "
         "switchbackClusterCount and switchbackClusterAttribute included (one with randomizationType \"unit\", "
         "one with it absent). Each unit experiment's treatment, "
         "exposure and published exposure equal plain unit assignment in every field, the uid is chosen so "
         "the switchback draw would differ, and each exposure and publish event has exactly the fields of "
         "the plain unit experiment's at every depth. The switchback experiment is never queried: an SDK "
         "without switchback serves it a sticky unit assignment that is documented and SDK-specific."),
        experiments, units, steps)
    del scenario["requires"]
    return [scenario]


class Fixtures(NamedTuple):
    golden: dict
    buckets: dict
    paths: dict


def load_fixtures() -> Fixtures:
    """Every copied fixture, each verified against the reference; builders read only these."""
    golden = load_golden()
    verify_reference(golden)
    buckets, paths = load_bucket_fixtures()
    verify_bucket_reference(buckets, paths)
    return Fixtures(golden, buckets, paths)


def builders(fixtures: Fixtures) -> list:
    return [
        lambda: golden_scenarios(fixtures.golden),
        lambda: bucket_vector_scenarios(fixtures.buckets),
        period_scenarios, shared_bucket_scenarios, eligibility_scenarios, serving_scenarios,
        attribute_source_scenarios, attribute_ineligible_scenarios,
        lambda: attribute_path_case_scenarios(fixtures.paths),
        attribute_timing_scenarios, inertness_scenarios,
    ]


def build_group(fixtures: Fixtures, first_number: int) -> list:
    scenarios = []
    for builder in builders(fixtures):
        for scenario in builder():
            scenario["name"] = f"{first_number + len(scenarios)} - Switchback - {scenario['name']}"
            scenarios.append(scenario)
    return scenarios


def write(scenarios: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Switchback scenario group.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    args = parser.parse_args()

    fixtures = load_fixtures()

    with open(CANONICAL, encoding="utf-8") as src:
        existing = json.load(src)
    kept = [s for s in existing if GROUP_MARKER not in s["name"]]
    group = build_group(fixtures, len(kept) + 1)
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
