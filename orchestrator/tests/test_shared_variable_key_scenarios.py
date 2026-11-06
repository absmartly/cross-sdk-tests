"""Shared Variable Key group of test_scenarios_complete.json.

* Mutant replay: every group scenario is replayed under the JS rule (which must reproduce the
  recorded expectations exactly; this validates the replay model itself) and under each wrong
  resolution model an SDK has shipped or proposed. Each wrong model must disagree with the
  recorded expectations on at least one variableValue / peekVariableValue / pending step,
  value or exposure list, so the scenario can tell it apart from the JS rule.
* Digest: every scenario outside the group is byte-for-byte what it was (sha256 of the kept
  scenarios, sort_keys) - the generator must not touch anything else.
* Numbering: scenario numbers are 1..N contiguous, in file order, and the group is the tail.

The replay model is deliberately independent of the generator: it reads only the JSON. Its
assignment model covers exactly the payload shapes the generator emits (split [0, 1] or a
one-hot split, trafficSplit [0, 1] or [1, 0], an absent unit type, a strict audience on `tier`,
overrides) and refuses anything else.
"""

import hashlib
import json
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIOS_PATH = os.environ.get("SVK_SCENARIOS_PATH", os.path.join(REPO_ROOT, "test_scenarios_complete.json"))

GROUP_MARKER = " - Shared Variable Key - "
# sha256 of json.dumps([non-group scenarios], sort_keys=True). Main had 229 scenarios when the
# group was added. Update ONLY when another group legitimately changes the non-group scenarios
# (e.g. when a group lands before this one and this one is renumbered).
KEPT_DIGEST = "8ddb8e702cf86ddf9346560071e8ba75ca6b58901ad90ebe8f2acb9ac4b25e86"
KEPT_COUNT = 229
GROUP_SIZE = 16

READS = ("variableValue", "peekVariableValue")


def load():
    with open(SCENARIOS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Replay model
# ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, scenario):
        create = scenario["steps"][0]
        assert create["action"] == "createContextWith", scenario["name"]
        self.units = dict(create["params"]["units"])
        self.experiments = scenario["contextData"]["experiments"]
        self.by_name = {e["name"]: e for e in self.experiments}
        self.attributes = {}
        self.overrides = {}
        self.exposed = set()
        self.pending = 0
        self.cache = {}

    def config(self, exp, variant):
        if variant >= len(exp["variants"]):
            return None
        raw = exp["variants"][variant]["config"]
        return json.loads(raw) if raw else {}

    def assign(self, exp):
        name = exp["name"]
        if name in self.cache:
            return self.cache[name]
        a = {"id": exp["id"], "name": name, "unit": exp["unitType"], "variant": 0, "assigned": False,
             "eligible": True, "overridden": False, "fullOn": False, "custom": False, "audienceMismatch": False}
        if name in self.overrides:
            a["overridden"] = True
            a["variant"] = self.overrides[name]
        else:
            assert exp["fullOnVariant"] == 0, "replay model: fullOn not used by the group"
            audience = exp.get("audience")
            if audience:
                expr = json.loads(audience)
                assert expr == {"filter": [{"eq": [{"var": "tier"}, {"value": "gold"}]}]}, audience
                a["audienceMismatch"] = self.attributes.get("tier") != "gold"
            if exp["audienceStrict"] and a["audienceMismatch"]:
                pass
            elif exp["unitType"] in self.units:
                a["assigned"] = True
                traffic = exp["trafficSplit"]
                assert traffic in ([0, 1], [1, 0]), traffic
                a["eligible"] = traffic == [0, 1]
                if a["eligible"]:
                    split = exp["split"]
                    assert sorted(split) == [0] * (len(split) - 1) + [1], split
                    a["variant"] = split.index(1)
        a["variables"] = self.config(exp, a["variant"])
        self.cache[name] = a
        return a

    def expose(self, a, events):
        if a["name"] not in self.exposed:
            self.exposed.add(a["name"])
            self.pending += 1
            events.append({"type": "exposure", "data": {k: v for k, v in a.items() if k != "variables"}})

    def defining(self, key):
        """Experiments any of whose variant configs defines key, in payload order."""
        return [e for e in self.experiments
                if any(key in self.config(e, i) for i in range(len(e["variants"])))]


def qualifies(a, key):
    return a["variables"] is not None and key in a["variables"] and (a["assigned"] or a["overridden"])


def js_rule(ctx, key, default, expose):
    """The JS SDK: sorted unique by id; expose every visited assignment; first qualifying wins."""
    events = []
    for exp in sorted(ctx.defining(key), key=lambda e: e["id"]):
        a = ctx.assign(exp)
        if a["variables"] is not None:
            if expose:
                ctx.expose(a, events)
            if qualifies(a, key):
                return a["variables"][key], events
    return default, events


def walk(order, stop_unassigned=False, stop_assigned=False, expose_winner_only=False, ignore_assigned=False):
    """A resolution model walking `order(experiments)`; flags turn on each wrong behaviour."""
    def model(ctx, key, default, expose):
        events, visited = [], []
        for exp in order(ctx.defining(key)):
            a = ctx.assign(exp)
            if a["variables"] is None:
                continue
            visited.append(a)
            if expose and not expose_winner_only:
                ctx.expose(a, events)
            served = a["assigned"] or a["overridden"]
            if (ignore_assigned or served) and key in a["variables"]:
                if expose and expose_winner_only:
                    ctx.expose(a, events)
                return a["variables"][key], events
            if stop_unassigned and not served:
                return default, events
            if stop_assigned and served:
                return a["variables"].get(key, default), events
        return default, events
    return model


by_id = lambda exps: sorted(exps, key=lambda e: e["id"])
payload = lambda exps: list(exps)

WRONG_MODELS = {
    # Old Python / Ruby / PHP / Swift 1.x: key -> the LAST experiment in the payload defining it.
    "last-in-payload": walk(lambda exps: exps[-1:]),
    # go-sdk#10 today: key -> the FIRST experiment in the payload defining it.
    "first-in-payload": walk(lambda exps: exps[:1]),
    # Only the lowest id is consulted: no fall-through at all.
    "lowest-id-only": walk(lambda exps: by_id(exps)[:1]),
    # Falls through a missing key but returns default as soon as an unassigned one is met.
    "no-fallthrough-on-unassigned": walk(by_id, stop_unassigned=True),
    # swift-sdk#7 today: the first assigned experiment ends the walk, key present or not.
    "first-assigned-without-key-check": walk(by_id, stop_assigned=True),
    # Same value as JS, but exposes only the experiment whose value is returned.
    "expose-only-the-winner": walk(by_id, expose_winner_only=True),
    # The JS walk over payload order instead of id order.
    "payload-order-walk": walk(payload),
    # Serves the key from an unassigned experiment's (control) variant.
    "ignore-assigned": walk(by_id, ignore_assigned=True),
}


def replay(scenario, model):
    """Replay every step after creation; return [(step index, action, result, events)]."""
    ctx = Ctx(scenario)
    out = []
    for i, step in enumerate(scenario["steps"][1:], start=1):
        action, params = step["action"], step["params"]
        if action in READS:
            result, events = model(ctx, params["key"], params["defaultValue"], action == "variableValue")
        elif action == "pending":
            result, events = ctx.pending, []
        elif action == "attribute":
            ctx.attributes[params["name"]] = params["value"]
            ctx.cache.clear()  # JS re-evaluates audiences after attribute changes; nothing is assigned yet here
            result, events = None, []
        elif action == "override":
            ctx.overrides[params["experimentName"]] = params["variant"]
            ctx.cache.pop(params["experimentName"], None)
            result, events = None, []
        else:
            raise AssertionError(f"replay model: unsupported action {action} in {scenario['name']}")
        out.append((i, action, result, events))
    return out


def recorded(scenario):
    return [(i, s["action"], s["expect"]["result"], s["expect"]["events"])
            for i, s in enumerate(scenario["steps"][1:], start=1)]


def same(a, b):
    """JSON equality with bool kept distinct from numbers."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class SharedVariableKeyGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load()
        cls.group = [s for s in cls.scenarios if GROUP_MARKER in s["name"]]
        cls.kept = [s for s in cls.scenarios if GROUP_MARKER not in s["name"]]

    def test_group_present_and_ungated(self):
        self.assertEqual(len(self.group), GROUP_SIZE)
        for s in self.group:
            self.assertNotIn("requires", s, s["name"])
            for step in s["steps"]:
                self.assertIn("events", step["expect"], f"{s['name']}: every step pins its events")

    def test_js_rule_replay_matches_recorded_expectations(self):
        """The replay model under the JS rule reproduces exactly what the real JS SDK recorded."""
        for s in self.group:
            with self.subTest(scenario=s["name"]):
                self.assertTrue(same(replay(s, js_rule), recorded(s)),
                                f"JS-rule replay disagrees with the recorded expectations of {s['name']}")

    def test_every_wrong_model_is_killed_by_every_scenario(self):
        """Every scenario kills every wrong model. The single exception is payload-order-walk on
        scenarios whose payload is already in id order (the (a) baseline and its peek twin): there
        the payload walk IS the id walk; the reversed-payload scenarios kill it instead."""
        for s in self.group:
            expected = recorded(s)
            ids = [e["id"] for e in s["contextData"]["experiments"]]
            for name, model in WRONG_MODELS.items():
                if name == "payload-order-walk" and ids == sorted(ids):
                    continue
                with self.subTest(scenario=s["name"], model=name):
                    got = replay(s, model)
                    self.assertFalse(same(got, expected),
                                     f"wrong model {name!r} reproduces every step of {s['name']}")

    def test_payload_order_walk_is_killed(self):
        unsorted = [s for s in self.group
                    if [e["id"] for e in s["contextData"]["experiments"]] != sorted(e["id"] for e in s["contextData"]["experiments"])]
        self.assertGreaterEqual(len(unsorted), len(self.group) - 2)

    def test_peek_scenarios_queue_nothing(self):
        for s in self.group:
            for step in s["steps"]:
                if step["action"] == "peekVariableValue":
                    self.assertEqual(step["expect"]["events"], [], s["name"])

    def test_kept_scenarios_unchanged(self):
        self.assertEqual(len(self.kept), KEPT_COUNT)
        digest = hashlib.sha256(json.dumps(self.kept, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(digest, KEPT_DIGEST, "a scenario outside the Shared Variable Key group changed")

    def test_numbering_contiguous_and_group_last(self):
        numbers = []
        for s in self.scenarios:
            m = re.match(r"^(\d+) - ", s["name"])
            self.assertIsNotNone(m, s["name"])
            numbers.append(int(m.group(1)))
        self.assertEqual(numbers, list(range(1, len(self.scenarios) + 1)))
        tail = self.scenarios[-len(self.group):]
        self.assertEqual(tail, self.group, "the group must be the contiguous tail of the file")


if __name__ == "__main__":
    unittest.main()
