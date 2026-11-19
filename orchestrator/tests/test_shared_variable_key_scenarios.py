"""Shared Variable Key group of test_scenarios_complete.json (340-366,
generate_shared_variable_key_scenarios.py).

The expectations are recorded from the java-sdk reference through its wrapper, which CI cannot run
without Docker. This test pins them instead:

* the recorded group is reproduced by the generator's independent replay model, and every wrong
  model of the bug class (MUTANTS: the dotnet-sdk 1.4.0 first-candidate fallback, payload order, a
  fall-through past a winner whose variant lacks the key, ...) fails at least one scenario;
* a hand edit to any scenario of the group fails the digest. Update GROUP_DIGEST only together with
  a regeneration whose `--check` is clean against the pinned java-sdk build;
* every scenario is gated on `sharedVariableKeys`, and the policy classifies it for every SDK.
"""

import hashlib
import json
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIOS_PATH = os.path.join(REPO_ROOT, "test_scenarios_complete.json")
POLICY_PATH = os.path.join(REPO_ROOT, "orchestrator", "expected-capabilities.json")

sys.path.insert(0, REPO_ROOT)
import generate_shared_variable_key_scenarios as generator  # noqa: E402
import scenario_groups  # noqa: E402

FIRST, LAST = 340, 366
# sha256 of json.dumps([group scenarios in file order], sort_keys=True).
GROUP_DIGEST = "e4007abf12dcc5c642aa7a577d8a531ebb581e50cd85d8938b493327187b53c0"


def digest(scenarios):
    return hashlib.sha256(json.dumps(scenarios, sort_keys=True).encode("utf-8")).hexdigest()


class SharedVariableKeyGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SCENARIOS_PATH, encoding="utf-8") as f:
            cls.everything = json.load(f)
        cls.group = generator.group_of(cls.everything)
        with open(POLICY_PATH, encoding="utf-8") as f:
            cls.policy = json.load(f)

    def test_numbering_and_placement(self):
        numbers = [int(re.match(r"^(\d+) - ", s["name"]).group(1)) for s in self.group]
        self.assertEqual(numbers, list(range(FIRST, LAST + 1)))
        start = self.everything.index(self.group[0])
        self.assertEqual(self.everything[start:start + len(self.group)], self.group, "the group is contiguous")
        before = self.everything[start - 1]["name"]
        after = self.everything[start + len(self.group)]["name"]
        self.assertTrue(any(m in before for m in scenario_groups.PARITY_MARKERS), before)
        self.assertIn(scenario_groups.SWITCHBACK, after)

    def test_group_unchanged(self):
        self.assertEqual(digest(self.group), GROUP_DIGEST,
                         "a Shared Variable Key scenario changed without regenerating it from java-sdk")

    def test_replay_model_reproduces_every_recording(self):
        for scenario in self.group:
            with self.subTest(scenario=scenario["name"]):
                self.assertEqual(generator.observed(scenario), generator.recorded(scenario))

    def test_every_mutant_is_killed(self):
        for mutant, killed in generator.killed_by(self.group).items():
            with self.subTest(mutant=mutant):
                self.assertTrue(killed, f"mutant {mutant} reproduces every scenario")

    def test_the_reported_case_kills_the_first_candidate_fallback(self):
        killed = generator.killed_by(self.group)["first_candidate_fallback"]
        self.assertIn(f"{FIRST}{generator.GROUP_MARKER}Strict Audience Mismatch On The Only Candidate "
                      "Returns The Default", killed)

    def test_rule_override_mutant_is_killed_by_shared_key_probe(self):
        probe = next(s for s in self.group if s["name"].startswith("365 - "))
        self.assertIn("assignmentRules", probe["requires"])
        self.assertNotEqual(generator.observed(probe, "ignore_rule_override"), generator.recorded(probe))
        exposure = next(e for st in probe["steps"] for e in st["expect"]["events"]
                        if e["type"] == "exposure")
        self.assertFalse(exposure["data"]["assigned"])
        self.assertTrue(exposure["data"]["ruleOverride"])

    def test_peek_queues_nothing(self):
        for scenario in self.group:
            for step in scenario["steps"]:
                if step["action"] in ("peek", "peekVariableValue"):
                    self.assertEqual(step["expect"]["events"], [], scenario["name"])

    def test_every_scenario_is_gated_and_classified(self):
        for scenario in self.group:
            self.assertEqual(scenario["requires"][0], generator.CAPABILITY, scenario["name"])
        self.assertIn(generator.CAPABILITY, self.policy["capabilities"])
        for sdk, entry in self.policy["sdks"].items():
            with self.subTest(sdk=sdk):
                self.assertNotEqual(generator.CAPABILITY in entry["required"],
                                    generator.CAPABILITY in entry.get("unsupported", {}))

    def test_the_model_rejects_fixtures_it_does_not_model(self):
        scenario = json.loads(json.dumps(self.group[0]))
        scenario["contextData"]["experiments"][0]["split"] = [0.5, 0.5]
        with self.assertRaises(ValueError):
            generator.observed(scenario)


if __name__ == "__main__":
    unittest.main()
