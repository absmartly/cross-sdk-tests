#!/usr/bin/env python3
"""Generate the Switchback scenario group of test_scenarios_complete.json.

The group is derived, never hand-edited: golden-vector scenarios drive every case in
fixtures/switchback/golden-vectors.json (a byte-for-byte copy of the platform's
lib/analytics/src/test/resources/switchback/golden-vectors.json) through the wrapper's
/switchback/assign and /switchback/permutation endpoints. The reference implementation below
must reproduce every golden vector before anything is written.

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


def build_group(golden: dict, first_number: int) -> list:
    scenarios = []
    for builder in BUILDERS:
        for scenario in builder(golden):
            scenario["name"] = f"{first_number + len(scenarios)} - Switchback - {scenario['name']}"
            scenarios.append(scenario)
    return scenarios


BUILDERS = [golden_scenarios]


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
