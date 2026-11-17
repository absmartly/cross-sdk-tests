"""Holdout Conformance group of test_scenarios_complete.json (H1-H8, generate_holdout_conformance_scenarios.py).

The expectations are recorded from the javascript-sdk reference. This module checks them without it:

* **Model replay.** Every group scenario is replayed under an independent Python model of the holdout
  contract (its own murmur3/md5 VariantAssigner), which must reproduce the recorded steps exactly.
* **Mutants.** Each scenario exists to catch one wrong implementation. The model replays every
  scenario under each mutant, and the mutant must disagree with the recorded expectations on its
  scenario (it may be caught elsewhere too):

    H1 ``keeps-duplicate-ids``          holdoutIds is not deduped (java-sdk today)
       ``drops-adjacent-repeats-only``  only a repeat of the previous id is dropped
       ``drops-first-id-repeats-only``  only a repeat of the first id is dropped
    H2 ``splitless-holdout-applies``    a holdout with an empty/missing split still covers
    H3 ``re-randomizes-on-edit``        a same-iteration seed/split edit redraws the arm
    H4 ``peek-fires-holdouts``          peek queues the holdout exposures
    H5 ``holdout-honours-full-on``      experiment-only fields of a holdout entry are applied
    H6 ``arm-cached-per-holdout-id``    the arm is cached per holdout, not per (holdout, unit type)
    H7 ``mismatch-skips-holdouts``      a strict audience mismatch does not fire the holdouts
    H8 ``suppressed-wins-variable-key`` a suppressed candidate wins a shared variable key
       ``only-winner-fires-holdouts``   only the winning candidate's holdouts fire

* **Digest.** The group is pinned by a digest, so a hand edit fails here even without the reference.
"""

import base64
import hashlib
import json
import os
import struct
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
import scenario_groups  # noqa: E402

SCENARIOS_PATH = os.path.join(REPO_ROOT, "test_scenarios_complete.json")
MARKER = scenario_groups.HOLDOUT_CONFORMANCE
FIRST = scenario_groups.HOLDOUT_CONFORMANCE_FIRST_NUMBER
# sha256 of json.dumps([group scenarios in file order], sort_keys=True). Update ONLY together with a
# regeneration from the pinned reference (generate_holdout_conformance_scenarios.py --check clean).
GROUP_DIGEST = "be604fe2a89ebc40cc9d77a87da5a6b00de3fd15d9015fdab767eb3eb1604719"

MUTANT_SCENARIO = {
    "keeps-duplicate-ids": "Duplicate holdoutIds",
    "drops-adjacent-repeats-only": "Duplicate holdoutIds",
    "drops-first-id-repeats-only": "Duplicate holdoutIds",
    "splitless-holdout-applies": "Empty Or Missing Split",
    "re-randomizes-on-edit": "Seed Or Split Edit",
    "peek-fires-holdouts": "Peek On A Covered Experiment",
    "holdout-honours-full-on": "Unknown Fields",
    "arm-cached-per-holdout-id": "Two Unit Types",
    "mismatch-skips-holdouts": "Strict Audience Mismatch",
    "suppressed-wins-variable-key": "Shared Variable Key",
    "only-winner-fires-holdouts": "Shared Variable Key",
}


# ---------------------------------------------------------------------------
# VariantAssigner (the SDKs' murmur3_32 over the md5 unit hash)
# ---------------------------------------------------------------------------

def _rotl(x, r):
    return ((x << r) | (x >> (32 - r))) & 0xffffffff


def murmur3_32(data):
    h, n = 0, len(data)
    for i in range(0, n & ~3, 4):
        k = _rotl((struct.unpack_from("<I", data, i)[0] * 0xcc9e2d51) & 0xffffffff, 15)
        h = (_rotl(h ^ ((k * 0x1b873593) & 0xffffffff), 13) * 5 + 0xe6546b64) & 0xffffffff
    tail, k = data[n & ~3:], 0
    for i, byte in enumerate(tail):
        k ^= byte << (8 * i)
    if tail:
        h ^= (_rotl((k * 0xcc9e2d51) & 0xffffffff, 15) * 0x1b873593) & 0xffffffff
    h ^= n
    h = ((h ^ (h >> 16)) * 0x85ebca6b) & 0xffffffff
    h = ((h ^ (h >> 13)) * 0xc2b2ae35) & 0xffffffff
    return h ^ (h >> 16)


def assign(uid, split, seed_hi, seed_lo):
    unit = base64.urlsafe_b64encode(hashlib.md5(str(uid).encode("utf-8")).digest()).rstrip(b"=")
    key = murmur3_32(unit)
    prob = murmur3_32(struct.pack("<III", seed_lo & 0xffffffff, seed_hi & 0xffffffff, key)) / 0xffffffff
    total = 0.0
    for i, weight in enumerate(split):
        total += weight
        if prob < total:
            return i
    return len(split) - 1


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class Model:
    """Two-arm holdouts over the group's experiment shape (one-hot split, traffic [0, 1], full-on 0,
    optional strict ``eq`` audience). ``mutant`` names one of MUTANT_SCENARIO's wrong implementations."""

    def __init__(self, scenario, mutant=None):
        create = scenario["steps"][0]
        self.units = dict(create["params"]["units"])
        self.mutant = mutant
        self.attrs = {}
        self.arms = {}          # cache key -> holdout arm entry
        self.assignments = {}   # experiment name -> assignment
        self.queue = []
        self.install(scenario["contextData"])

    def is_(self, name):
        return self.mutant == name

    def install(self, data):
        self.holdouts = {h["id"]: h for h in data.get("holdouts") or []
                         if h.get("split") or self.is_("splitless-holdout-applies")}
        self.experiments = {}
        for exp in data["experiments"]:
            assert exp["fullOnVariant"] == 0 and exp["trafficSplit"] == [0, 1], exp["name"]
            ids = [i for i in exp.get("holdoutIds") or [] if i in self.holdouts]
            if self.is_("keeps-duplicate-ids"):
                ids = sorted(ids)
            elif self.is_("drops-adjacent-repeats-only"):
                ids = sorted(i for n, i in enumerate(ids) if n == 0 or ids[n - 1] != i)
            elif self.is_("drops-first-id-repeats-only"):
                ids = sorted(i for n, i in enumerate(ids) if n == 0 or i != ids[0])
            else:
                ids = sorted(set(ids))
            self.experiments[exp["name"]] = (exp, ids)

    def holdout_arm(self, holdout_id, unit_type):
        live = self.holdouts[holdout_id]
        key = holdout_id if self.is_("arm-cached-per-holdout-id") else (holdout_id, unit_type)
        draw = (live["iteration"], live["seedHi"], live["seedLo"], tuple(live.get("split") or ()))
        cached = self.arms.get(key)
        same = cached and (cached["draw"] == draw if self.is_("re-randomizes-on-edit")
                           else cached["draw"][0] == draw[0])
        if same:
            return cached
        if self.is_("holdout-honours-full-on") and live.get("fullOnVariant"):
            arm = live["fullOnVariant"]
        elif not live.get("split"):
            arm = 0
        else:
            assert len(live["split"]) == 2, "model: two-arm holdouts only"
            arm = assign(self.units[unit_type], live["split"], live["seedHi"], live["seedLo"])
        entry = {"draw": draw, "exposed": False, "exposure": {
            "id": live["id"], "name": live["name"], "unit": unit_type, "variant": arm, "assigned": True,
            "eligible": True, "overridden": False, "fullOn": False, "custom": False,
            "audienceMismatch": False}}
        self.arms[key] = entry
        return entry

    def resolve(self, name):
        exp, ids = self.experiments[name]
        identity = [(i, self.holdouts[i]["iteration"]) for i in ids]
        cached = self.assignments.get(name)
        if cached and cached["identity"] == identity:
            return cached
        arms = [self.holdout_arm(i, exp["unitType"]) for i in ids]
        suppressed = any(a["exposure"]["variant"] == 0 for a in arms)
        mismatch = False
        if not suppressed and exp.get("audience"):
            (term,) = json.loads(exp["audience"])["filter"]
            var, value = term["eq"][0]["var"], term["eq"][1]["value"]
            mismatch = self.attrs.get(var) != value
        strict_miss = mismatch and exp["audienceStrict"]
        variant = 0 if suppressed or strict_miss else exp["split"].index(max(exp["split"]))
        configs = [json.loads(v["config"]) if v["config"] else {} for v in exp["variants"]]
        a = {"identity": identity, "arms": arms, "suppressed": suppressed, "mismatch": mismatch,
             "exposed": False, "variables": configs[variant], "exposure": {
                 "id": exp["id"], "name": name, "unit": exp["unitType"], "variant": variant,
                 "assigned": not (suppressed or strict_miss), "eligible": True, "overridden": False,
                 "fullOn": False, "custom": False, "audienceMismatch": mismatch}}
        self.assignments[name] = a
        return a

    def fire_holdouts(self, a, events):
        if self.is_("mismatch-skips-holdouts") and a["mismatch"]:
            return
        for arm in a["arms"]:
            if not arm["exposed"]:
                arm["exposed"] = True
                self.queue_exposure(arm["exposure"], events)

    def queue_exposure(self, exposure, events):
        self.queue.append(exposure)
        events.append({"type": "exposure", "data": dict(exposure)})

    def expose(self, a, events, fire_holdouts=True):
        if not a["exposed"]:
            a["exposed"] = True
            if not a["suppressed"]:
                self.queue_exposure(a["exposure"], events)
            if fire_holdouts:
                self.fire_holdouts(a, events)

    def variable(self, key, default, events, peek):
        candidates = sorted((exp["id"], name) for name, (exp, _) in self.experiments.items()
                            if any(key in json.loads(v["config"] or "{}") for v in exp["variants"]))
        fallback = None
        for _, name in candidates:
            a = self.resolve(name)
            if not peek:
                self.expose(a, events, fire_holdouts=not self.is_("only-winner-fires-holdouts"))
            assigned = a["exposure"]["assigned"]
            if key in a["variables"] and (assigned or self.is_("suppressed-wins-variable-key")):
                if not peek and self.is_("only-winner-fires-holdouts"):
                    self.fire_holdouts(a, events)
                return a["variables"][key]
            if a["suppressed"] and fallback is None:
                fallback = a["variables"]
        return fallback[key] if fallback and key in fallback else default

    def step(self, action, params):
        events = []
        result = None
        if action == "treatment":
            a = self.resolve(params["experimentName"])
            self.expose(a, events)
            result = a["exposure"]["variant"]
        elif action == "peek":
            a = self.resolve(params["experimentName"])
            if self.is_("peek-fires-holdouts"):
                self.fire_holdouts(a, events)
            result = a["exposure"]["variant"]
        elif action in ("variableValue", "peekVariableValue"):
            result = self.variable(params["key"], params["defaultValue"], events,
                                   peek=action == "peekVariableValue")
        elif action == "attribute":
            self.attrs[params["name"]] = params["value"]
        elif action == "refresh":
            self.install(params["newData"])
            events.append({"type": "refresh", "data": {"experiments": [
                {"id": e["id"], "name": e["name"]} for e in params["newData"]["experiments"]]}})
        elif action == "pending":
            result = len(self.queue)
        elif action == "publish":
            events.append({"type": "publish", "data": {"exposures": [
                {k: e[k] for k in ("id", "name", "unit", "variant")} for e in self.queue]}})
            self.queue = []
        else:
            raise AssertionError(f"model: unsupported action {action}")
        return {"result": result, "events": events}


def as_harness_sees(scenario, outcomes):
    """Steps marked `unorderedEvents` are matched by the harness as a set, so compare them as one."""
    for step, outcome in zip(scenario["steps"][1:], outcomes):
        if step["expect"].get("unorderedEvents"):
            outcome["events"] = sorted(outcome["events"], key=lambda e: json.dumps(e, sort_keys=True))
    return outcomes


def replay(scenario, mutant=None):
    model = Model(scenario, mutant)
    return as_harness_sees(scenario, [model.step(s["action"], s["params"]) for s in scenario["steps"][1:]])


def recorded(scenario):
    return as_harness_sees(scenario, [{"result": s["expect"]["result"], "events": list(s["expect"]["events"])}
                                      for s in scenario["steps"][1:]])


def same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


class HoldoutConformanceGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SCENARIOS_PATH, encoding="utf-8") as f:
            cls.everything = json.load(f)
        cls.group = [s for s in cls.everything if MARKER in s["name"]]

    def scenario(self, fragment):
        (match,) = [s for s in self.group if fragment in s["name"]]
        return match

    def test_numbering_and_gating(self):
        numbers = [scenario_groups.number(s) for s in self.group]
        self.assertEqual(numbers, list(range(FIRST, FIRST + 8)))
        everything = [scenario_groups.number(s) for s in self.everything]
        self.assertEqual(everything, sorted(everything))
        for s in self.group:
            dedup = "Duplicate holdoutIds" in s["name"]
            self.assertEqual(s["requires"], ["holdouts", "holdoutIdsDedup"] if dedup else ["holdouts"],
                             s["name"])

    def test_reference_model_reproduces_every_scenario(self):
        for s in self.group:
            with self.subTest(scenario=s["name"]):
                self.assertTrue(same(replay(s), recorded(s)),
                                f"model disagrees with the recorded expectations of {s['name']}")

    def test_each_mutant_is_killed_by_its_scenario(self):
        for mutant, fragment in MUTANT_SCENARIO.items():
            with self.subTest(mutant=mutant):
                s = self.scenario(fragment)
                self.assertFalse(same(replay(s, mutant), recorded(s)),
                                 f"mutant {mutant} reproduced {s['name']}")

    def test_only_the_shared_key_exposures_are_unordered(self):
        """Only 507's variableValue queues exposures in an SDK-specific order (JS: each candidate's own
        exposure before its holdouts; Java: every visited candidate's holdouts first)."""
        unordered = [(s["name"], step["action"]) for s in self.group for step in s["steps"]
                     if step["expect"].get("unorderedEvents")]
        self.assertEqual(unordered, [(self.scenario("Shared Variable Key")["name"], "variableValue")])

    def test_peeks_queue_nothing(self):
        for s in self.group:
            for step in s["steps"]:
                if step["action"] in ("peek", "peekVariableValue"):
                    self.assertEqual(step["expect"]["events"], [], s["name"])

    def test_group_unchanged(self):
        digest = hashlib.sha256(json.dumps(self.group, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(digest, GROUP_DIGEST,
                         "a Holdout Conformance scenario changed without regenerating it from the reference")


if __name__ == "__main__":
    unittest.main()
