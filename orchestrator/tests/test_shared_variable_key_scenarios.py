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
import subprocess
import unittest
from unittest import mock

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

    def rule_scenario(self, rule):
        scenario = json.loads(json.dumps(next(s for s in self.group if s["name"].startswith("365 - "))))
        scenario["contextData"]["experiments"][0]["assignmentRules"] = json.dumps({"rules": [rule]})
        return scenario

    def test_the_model_rejects_a_conditional_rule(self):
        rule = {"name": "us", "type": "assign", "variant": 1,
                "conditions": {"eq": [{"var": "Country"}, {"value": "US"}]}}
        with self.assertRaises(ValueError):
            generator.observed(self.rule_scenario(rule))

    def test_the_model_rejects_a_split_rule(self):
        # Only the fields of the modelled assign rule, so the type check alone must refuse it.
        with self.assertRaises(ValueError):
            generator.observed(self.rule_scenario({"name": "s", "type": "split", "variant": 1}))

    def test_the_model_rejects_more_than_one_rule(self):
        scenario = self.rule_scenario({"name": "a", "type": "assign", "variant": 1})
        scenario["contextData"]["experiments"][0]["assignmentRules"] = json.dumps({"rules": [
            {"name": "a", "type": "assign", "variant": 1}, {"name": "b", "type": "assign", "variant": 0}]})
        with self.assertRaises(ValueError):
            generator.observed(scenario)


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class RecordingProvenanceTest(unittest.TestCase):
    """Recording refuses a reference it cannot prove is the pinned java-sdk."""

    def test_a_checkout_at_another_commit_is_refused(self):
        # A clean tree, so only the commit check can refuse it.
        runs = [completed("0" * 40 + "\n"), completed("")]
        with mock.patch.object(generator.subprocess, "run", side_effect=runs):
            with self.assertRaises(SystemExit):
                generator.ensure_reference_checkout()

    def test_a_failing_git_status_is_refused(self):
        runs = [completed(generator.JAVA_SDK_COMMIT + "\n"), completed("", 128, "not a git repository")]
        with mock.patch.object(generator.subprocess, "run", side_effect=runs):
            with self.assertRaises(SystemExit):
                generator.ensure_reference_checkout()

    def test_a_clean_checkout_at_the_pin_is_accepted(self):
        runs = [completed(generator.JAVA_SDK_COMMIT + "\n"), completed("")]
        with mock.patch.object(generator.subprocess, "run", side_effect=runs):
            generator.ensure_reference_checkout()

    def health(self, body):
        response = mock.Mock()
        response.json.return_value = body
        return mock.patch("requests.get", return_value=response)

    def test_a_wrapper_built_from_another_commit_is_refused(self):
        for body in ({"sdkCommit": "0" * 40}, {"sdkCommit": None}, {}):
            with self.subTest(body=body), self.health(body), self.assertRaises(SystemExit):
                generator.ensure_reference_wrapper("http://wrapper.invalid")

    def test_a_wrapper_built_from_the_pin_is_accepted(self):
        with self.health({"sdkCommit": generator.JAVA_SDK_COMMIT}):
            generator.ensure_reference_wrapper("http://wrapper.invalid")


class SdkCommitPinTest(unittest.TestCase):
    """The stacked dotnet-sdk dependency is pinned to an immutable head, and both CI and local setup
    check it out."""

    def read(self, *path):
        with open(os.path.join(REPO_ROOT, *path), encoding="utf-8") as f:
            return f.read()

    def test_pins_are_full_shas(self):
        block = re.search(r"declare -A SDK_COMMITS=\((.*?)\)", self.read("sdk-branches.sh"), re.S)
        self.assertIsNotNone(block)
        pins = dict(re.findall(r'\["([^"]+)"\]="([^"]*)"', block.group(1)))
        self.assertIn("dotnet-sdk", pins)
        for repo, sha in pins.items():
            self.assertRegex(sha, r"^[0-9a-f]{40}$", repo)

    def test_ci_and_local_setup_check_the_pin_out(self):
        for path in ((".github", "workflows", "cross-sdk.yml"), ("setup-sdks.sh",)):
            text = self.read(*path)
            with self.subTest(path="/".join(path)):
                self.assertIn('fetch --depth 1 origin "${SDK_COMMITS[$repo]}"', text)
                self.assertIn('checkout --detach "${SDK_COMMITS[$repo]}"', text)


if __name__ == "__main__":
    unittest.main()
