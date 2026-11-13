"""System Attributes group of test_scenarios_complete.json (the 2026-11 §5 round).

The group pins that every SDK evaluates audiences with the client's configured ``application`` and
``environment`` names, always on, with user attributes overlaid on top (a user-set value wins), and
an unconfigured name left out of the map. It mirrors §4 tests 1, 3, 4 and 6 plus a variable read
through the matched variant. (The SDK applies the same names to assignment-rule conditions and drops
``app_version`` too; those two are covered by the SDKs' own unit tests and the rule/semver groups,
not re-exercised black-box here.)

Like the other SDK-round groups, this module is a black-box check of the *scenarios themselves*,
independent of the generator and of any wrapper. It reads only the JSON and:

* **Mutant replay.** Every group scenario is replayed under a Python model of the reference behaviour
  (which must reproduce the recorded expectations exactly — this validates the model) and under each
  mutant an SDK could ship:
    - ``does-not-inject``: the SDK never puts ``application``/``environment`` into the evaluation map
      (the pre-fix behaviour that sent every unit to variant 0 — the regression this round fixes); and
    - ``injects-but-overrides-user-values``: the SDK injects the system names but applies them *after*
      the user attributes, so a ``context.attribute("environment", …)`` loses to the configured name.
  Each mutant must disagree with the recorded expectations on at least one step of some scenario, so
  the group actually catches that bug. ``does-not-inject`` is killed by the matching scenarios (248,
  249, 253's application sub-case) and ``injects-but-overrides-user-values`` by the user-override
  scenarios (251, 252).
* **Digest.** Every scenario outside the group is byte-for-byte what it was (sha256 of the kept
  scenarios, ``sort_keys``) — the generator must not touch anything else.
* **Numbering & gating.** Scenario numbers are strictly ascending with the group as the contiguous
  tail; tests 1/3/4 and the variable read are ungated, and only the no-environment scenario carries
  ``requires: ["clientNoEnvironment"]``.

The replay model covers exactly the payload shapes the generator emits for this group (a one-hot
split ``[0.0, 1.0]``, ``trafficSplit [0, 1]``, ``fullOnVariant 0``, and either a strict ``eq``
audience on ``application``/``environment`` or the absence-sensitive ``not``/``null`` audience that
matches only when a name is present) and refuses anything else, so it cannot silently drift.
"""

import hashlib
import json
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIOS_PATH = os.environ.get(
    "SYSATTR_SCENARIOS_PATH", os.path.join(REPO_ROOT, "test_scenarios_complete.json")
)

GROUP_MARKER = " - System Attributes - "
# Groups appended after this one (generate_parity_round_scenarios.py, 300+). They are neither
# "kept" scenarios for the digest nor part of this group; this group must directly precede them.
LATER_GROUP_MARKERS = (" - Assignment Rules - ", " - Semver Comparison - ", " - X-Agent - ")
NO_ENVIRONMENT_CAPABILITY = "clientNoEnvironment"

# sha256 of json.dumps([non-group scenarios], sort_keys=True). The suite had 240 scenarios before
# this group was appended. Update ONLY when another group legitimately changes the non-group
# scenarios (e.g. a group lands before this one and this one is renumbered).
KEPT_DIGEST = "71a710e4a2b7f6bdb055f5a009079b373663ade43514bb2d8751e5e43e1dad7d"
KEPT_COUNT = 240
GROUP_SIZE = 6

READS = ("treatment", "peek", "variableValue", "peekVariableValue")
QUEUEING_READS = ("treatment", "variableValue")  # the reads that also queue an exposure


def load():
    with open(SCENARIOS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Replay model
# ---------------------------------------------------------------------------

class Ctx:
    """A minimal context that evaluates the group's strict ``eq`` audiences against an attribute map
    built from the client's configured application/environment plus user attributes. ``inject`` and
    ``user_wins`` select the reference behaviour or one of the mutants."""

    def __init__(self, scenario, inject, user_wins):
        create = scenario["steps"][0]
        assert create["action"] == "createContextWith", scenario["name"]
        self.units = dict(create["params"]["units"])
        client = create["params"].get("client") or {}
        # A non-empty string application/environment is "configured"; anything else is absent.
        self.system = {}
        app = client.get("application")
        if isinstance(app, str) and app:
            self.system["application"] = app
        env = client.get("environment")
        if isinstance(env, str) and env:
            self.system["environment"] = env
        self.experiments = scenario["contextData"]["experiments"]
        self.user_attributes = {}
        self.exposed = {}
        self.pending = 0
        self.cache = {}
        self.inject = inject
        self.user_wins = user_wins

    def attributes_map(self):
        """Build the evaluation map. The reference puts system values first, then user attributes on
        top (user wins). The mutants either skip the system values entirely (``does-not-inject``) or
        apply them last so they beat a user value (``injects-but-overrides-user-values``)."""
        attrs = {}
        if self.inject and not self.user_wins:
            attrs.update(self.system)
            attrs.update(self.user_attributes)
        elif self.inject and self.user_wins:
            attrs.update(self.user_attributes)
            attrs.update(self.system)  # system overwrites the user value — the bug
        else:
            attrs.update(self.user_attributes)  # system names never injected — the bug
        return attrs

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
             "eligible": True, "overridden": False, "fullOn": False, "custom": False,
             "audienceMismatch": False}
        assert exp["fullOnVariant"] == 0, "replay model: fullOn not used by the group"
        audience = exp.get("audience")
        if audience:
            expr = json.loads(audience)
            flt = expr["filter"]
            assert len(flt) == 1, audience
            term = flt[0]
            attrs = self.attributes_map()
            if "eq" in term:
                var = term["eq"][0]["var"]
                value = term["eq"][1]["value"]
                assert var in ("application", "environment"), var
                a["audienceMismatch"] = attrs.get(var) != value
            elif "not" in term and "null" in term["not"]:
                # Absence-sensitive check: matches only when the variable is
                # present in the evaluation map. Catches a wrapper that leaks a
                # placeholder instead of leaving an unconfigured name absent.
                var = term["not"]["null"]["var"]
                assert var in ("application", "environment"), var
                a["audienceMismatch"] = var not in attrs
            else:
                raise AssertionError(f"replay model: unsupported audience {audience}")
        if exp["audienceStrict"] and a["audienceMismatch"]:
            pass
        elif exp["unitType"] in self.units:
            a["assigned"] = True
            traffic = exp["trafficSplit"]
            assert traffic == [0, 1], traffic
            split = exp["split"]
            assert sorted(split) == [0] * (len(split) - 1) + [1], split
            a["variant"] = split.index(max(split))
        a["variables"] = self.config(exp, a["variant"])
        self.cache[name] = a
        return a

    def exp_by_name(self, name):
        for exp in self.experiments:
            if exp["name"] == name:
                return exp
        raise AssertionError(f"replay model: unknown experiment {name}")

    def exposure_of(self, a):
        return {k: v for k, v in a.items() if k != "variables"}

    def expose(self, a, events):
        # The reference queues a fresh exposure the first time an experiment is served and again
        # whenever a re-evaluation (after an attribute change invalidates the cache) resolves it to a
        # different exposure — a flipped strict audience reassigns the unit and re-exposes.
        signature = json.dumps(self.exposure_of(a), sort_keys=True)
        if self.exposed.get(a["name"]) != signature:
            self.exposed[a["name"]] = signature
            self.pending += 1
            events.append({"type": "exposure", "data": self.exposure_of(a)})


def replay(scenario, inject, user_wins):
    """Replay every step after creation; return [(step index, action, result, events)]."""
    ctx = Ctx(scenario, inject=inject, user_wins=user_wins)
    out = []
    for i, step in enumerate(scenario["steps"][1:], start=1):
        action = step["action"]
        params = step.get("params", {})
        events = []
        if action in ("treatment", "peek"):
            a = ctx.assign(ctx.exp_by_name(params["experimentName"]))
            if action == "treatment":
                ctx.expose(a, events)
            result = a["variant"]
        elif action in ("variableValue", "peekVariableValue"):
            key = params["key"]
            # The group's variable read goes through a single experiment whose matched variant
            # defines the key; resolve it the same way treatment does.
            exp = next((e for e in ctx.experiments
                        if any(key in (ctx.config(e, v) or {}) for v in range(len(e["variants"])))), None)
            assert exp is not None, f"replay model: no experiment defines {key}"
            a = ctx.assign(exp)
            if action == "variableValue":
                ctx.expose(a, events)
            result = a["variables"].get(key, params.get("defaultValue")) if a["variables"] else params.get("defaultValue")
        elif action == "pending":
            result = ctx.pending
        elif action == "attribute":
            ctx.user_attributes[params["name"]] = params["value"]
            ctx.cache.clear()  # the reference re-evaluates audiences after an attribute changes
            result = None
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

class SystemAttributesGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        everything = load()
        cls.later = [s for s in everything if any(m in s["name"] for m in LATER_GROUP_MARKERS)]
        cls.scenarios = [s for s in everything if s not in cls.later]
        cls.group = [s for s in cls.scenarios if GROUP_MARKER in s["name"]]
        cls.kept = [s for s in cls.scenarios if GROUP_MARKER not in s["name"]]
        cls.everything = everything

    def test_group_present(self):
        self.assertEqual(len(self.group), GROUP_SIZE)
        for s in self.group:
            for step in s["steps"]:
                self.assertIn("events", step["expect"], f"{s['name']}: every step pins its events")

    def test_only_the_no_environment_scenario_is_gated(self):
        """Tests 1/3/4 and the variable read are ungated; only the no-environment scenario requires
        the clientNoEnvironment capability (an SDK whose client mandates an environment cannot build
        that case black-box)."""
        gated = [s for s in self.group if s.get("requires")]
        self.assertEqual(len(gated), 1, "exactly one group scenario is gated")
        self.assertEqual(gated[0].get("requires"), [NO_ENVIRONMENT_CAPABILITY])
        self.assertIn("No Environment Configured", gated[0]["name"])
        for s in self.group:
            if s is not gated[0]:
                self.assertNotIn("requires", s, s["name"])

    def test_reference_replay_matches_recorded_expectations(self):
        """The replay model under the reference behaviour reproduces exactly what the real JS SDK
        recorded for every group scenario (validates the model itself)."""
        for s in self.group:
            with self.subTest(scenario=s["name"]):
                self.assertTrue(same(replay(s, inject=True, user_wins=False), recorded(s)),
                                f"reference replay disagrees with the recorded expectations of {s['name']}")

    def test_does_not_inject_mutant_is_killed(self):
        """An SDK that never injects application/environment (the regression this round fixes) must
        disagree with the recorded expectations of at least one scenario — and specifically of the
        scenarios whose audience matches only because the system name was injected."""
        killed = [s["name"] for s in self.group
                  if not same(replay(s, inject=False, user_wins=False), recorded(s))]
        self.assertTrue(killed, "the does-not-inject mutant reproduced every scenario")
        # The matching-audience scenarios (environment match, application match) must catch it.
        self.assertTrue(any("Configured Environment Matches" in n for n in killed))
        self.assertTrue(any("Configured Application Matches" in n for n in killed))

    def test_injects_but_overrides_user_values_mutant_is_killed(self):
        """An SDK that injects the system names but lets them beat a user-set value must disagree
        with the recorded expectations of the user-override scenarios."""
        killed = [s["name"] for s in self.group
                  if not same(replay(s, inject=True, user_wins=True), recorded(s))]
        self.assertTrue(killed, "the injects-but-overrides-user-values mutant reproduced every scenario")
        self.assertTrue(any("User-Set Environment" in n for n in killed),
                        "a user-override scenario must kill the override mutant")

    def test_each_mutant_is_killed_by_a_dedicated_scenario(self):
        """Neither mutant is a no-op: each is caught, and the reference model is the only one that
        reproduces every scenario."""
        for inject, user_wins, label in ((False, False, "does-not-inject"),
                                         (True, True, "injects-but-overrides-user-values")):
            reproduced_all = all(same(replay(s, inject=inject, user_wins=user_wins), recorded(s))
                                 for s in self.group)
            self.assertFalse(reproduced_all, f"mutant {label} reproduced every scenario")

    def test_peek_queues_nothing(self):
        for s in self.group:
            for step in s["steps"]:
                if step["action"] in ("peek", "peekVariableValue"):
                    self.assertEqual(step["expect"]["events"], [], s["name"])

    def test_kept_scenarios_unchanged(self):
        self.assertEqual(len(self.kept), KEPT_COUNT)
        digest = hashlib.sha256(json.dumps(self.kept, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(digest, KEPT_DIGEST, "a scenario outside the System Attributes group changed")

    def test_numbering_ascending_and_group_last(self):
        numbers = []
        for s in self.scenarios:
            m = re.match(r"^(\d+) - ", s["name"])
            self.assertIsNotNone(m, s["name"])
            numbers.append(int(m.group(1)))
        # The suite has gaps from earlier deletions (e.g. #18), so numbers are strictly ascending
        # rather than contiguous; the group is the contiguous tail.
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(len(numbers), len(set(numbers)), "scenario numbers are unique")
        tail = self.scenarios[-len(self.group):]
        self.assertEqual(tail, self.group, "the group must be the contiguous tail of the file")
        # Only the later parity groups may follow it, all numbered above it.
        self.assertEqual(self.everything[:len(self.scenarios)], self.scenarios,
                         "only the later parity groups may follow this group")
        if self.later:
            last = int(self.group[-1]["name"].split(" - ", 1)[0])
            first_later = int(self.later[0]["name"].split(" - ", 1)[0])
            self.assertGreater(first_later, last)


if __name__ == "__main__":
    unittest.main()
