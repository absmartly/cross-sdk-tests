"""Shared Variable Key group of test_scenarios_complete.json (340-368,
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

FIRST, LAST = 340, 368
# sha256 of json.dumps([group scenarios in file order], sort_keys=True).
GROUP_DIGEST = "e7b5cc414d5c18bed2fc325988a9d0a92980d4404da057fd8c81377f2beac294"


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

    def test_the_wrong_models_are_the_documented_set(self):
        self.assertEqual(len(generator.MUTANTS), 19)
        self.assertEqual(len(set(generator.MUTANTS)), len(generator.MUTANTS))
        self.assertIn("full_on_escapes_holdout", generator.MUTANTS)
        self.assertIn("ignore_rule_override", generator.MUTANTS)
        self.assertIn("last_suppressed", generator.MUTANTS)

    def test_an_unknown_holdout_id_does_not_hold_the_experiment_out(self):
        # As java-sdk: a holdoutIds entry with no matching holdout is ignored.
        scenario = json.loads(json.dumps(next(s for s in self.group if s["name"].startswith("357 - "))))
        scenario["contextData"]["experiments"][0]["holdoutIds"] = [404]
        reads = [r[1] for r in generator.observed(scenario) if r[0] in (1, 2)]
        self.assertEqual(reads, ["held-treatment", "held-treatment"])

    def test_the_model_rejects_a_holdout_it_does_not_model(self):
        scenario = json.loads(json.dumps(next(s for s in self.group if s["name"].startswith("357 - "))))
        scenario["contextData"]["holdouts"][0]["split"] = [0.0, 1.0]
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

    def test_a_dirty_checkout_at_the_pin_is_refused(self):
        runs = [completed(generator.JAVA_SDK_COMMIT + "\n"), completed(" M core-api/Context.java\n")]
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

    def test_build_group_checks_provenance_before_recording(self):
        for guard in ("ensure_reference_checkout", "ensure_reference_wrapper"):
            with self.subTest(guard=guard), \
                    mock.patch.object(generator, "ensure_reference_checkout"), \
                    mock.patch.object(generator, "ensure_reference_wrapper"), \
                    mock.patch.object(generator, "record") as record:
                getattr(generator, guard).side_effect = SystemExit("refused")
                with self.assertRaises(SystemExit):
                    generator.build_group("http://wrapper.invalid")
                record.assert_not_called()

    def test_build_group_refuses_a_recording_the_model_does_not_reproduce(self):
        with open(SCENARIOS_PATH, encoding="utf-8") as f:
            recorded = {s["name"].split(generator.GROUP_MARKER, 1)[1]: s["steps"]
                        for s in generator.group_of(json.load(f))}

        def tampered(spec, base_url):
            steps = json.loads(json.dumps(recorded[spec["title"]]))
            if spec["title"].startswith("Strict Audience Mismatch On The Only Candidate"):
                read = next(st for st in steps if st["action"] == "variableValue")
                read["expect"]["result"] = "Search food"   # the 1.4.0 leak
            return steps

        with mock.patch.object(generator, "ensure_reference_checkout"), \
                mock.patch.object(generator, "ensure_reference_wrapper"), \
                mock.patch.object(generator, "record", side_effect=tampered):
            with self.assertRaises(SystemExit):
                generator.build_group("http://wrapper.invalid")

    def test_build_group_accepts_the_checked_in_recording(self):
        with open(SCENARIOS_PATH, encoding="utf-8") as f:
            group = generator.group_of(json.load(f))
        recorded = {s["name"].split(generator.GROUP_MARKER, 1)[1]: s["steps"] for s in group}
        with mock.patch.object(generator, "ensure_reference_checkout"), \
                mock.patch.object(generator, "ensure_reference_wrapper"), \
                mock.patch.object(generator, "record", side_effect=lambda spec, _: recorded[spec["title"]]):
            self.assertEqual(generator.build_group("http://wrapper.invalid"), group)


class ReplayDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        with open(SCENARIOS_PATH, encoding="utf-8") as src:
            self.group = generator.group_of(json.load(src))

    def test_replay_reuses_one_mutant_map_for_survivors_and_counts(self):
        results = {m: ([self.group[0]["name"]] if i else []) for i, m in enumerate(generator.MUTANTS)}
        with mock.patch.object(generator, "killed_by", return_value=results) as killed, \
                mock.patch.object(generator, "observed", wraps=generator.observed) as observed, \
                mock.patch.object(sys, "argv", ["generate_shared_variable_key_scenarios.py", "--replay"]), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(generator.main(), 1)
        killed.assert_called_once_with(self.group)
        self.assertEqual(observed.call_count, len(self.group), "model agreement must still be checked")
        self.assertTrue(all(len(call.args) == 1 for call in observed.call_args_list))
        printed.assert_any_call(f"{generator.MUTANTS[0]}: killed by 0 scenario(s)")
        printed.assert_any_call(f"mutant {generator.MUTANTS[0]} survives every scenario")

    def test_cached_mutant_results_do_not_hide_model_disagreement(self):
        results = {m: [self.group[0]["name"]] for m in generator.MUTANTS}
        with mock.patch.object(generator, "killed_by") as killed, \
                mock.patch.object(generator, "observed", return_value=["wrong model result"]):
            failures = generator.replay_failures(self.group, results)
        killed.assert_not_called()
        self.assertEqual(failures, [f"model disagrees with the recording: {s['name']}" for s in self.group])


class RecordingCleanupTest(unittest.TestCase):
    def test_record_deletes_the_context_on_success_and_failures_without_recording_cleanup(self):
        spec = {"title": "cleanup", "experiments": [], "holdouts": [], "units": {}, "requires": [],
                "steps": [("treatment", {"experimentName": "e"})]}
        ready = {"contextId": "ctx-123", "ready": True, "failed": False, "finalized": False}
        for failure in (None, "not-ready", "projection", "request"):
            with self.subTest(failure=failure):
                created = mock.Mock()
                created.json.return_value = {"result": {**ready, "ready": failure != "not-ready"}, "events": []}
                if failure == "projection":
                    created.json.return_value["events"] = [{"type": "unexpected"}]
                action = mock.Mock()
                action.json.return_value = {"result": 1, "events": []}
                if failure == "request":
                    action.raise_for_status.side_effect = RuntimeError("recording failed")
                with mock.patch("requests.put"), \
                        mock.patch("requests.post", side_effect=[created, action]), \
                        mock.patch("requests.delete") as delete:
                    if failure:
                        with self.assertRaises(RuntimeError if failure == "request" else SystemExit):
                            generator.record(spec, "http://wrapper.invalid")
                    else:
                        steps = generator.record(spec, "http://wrapper.invalid")
                        self.assertEqual([st["action"] for st in steps], ["createContextWith", "treatment"])
                        self.assertEqual(steps[1]["expect"], {"result": 1, "events": []})
                    delete.assert_called_once_with("http://wrapper.invalid/context/ctx-123", timeout=5)
                    delete.return_value.raise_for_status.assert_called_once_with()

    def test_record_does_not_delete_a_context_before_creation_succeeds(self):
        spec = {"experiments": [], "holdouts": [], "units": {}}
        with mock.patch("requests.put"), mock.patch("requests.post") as create, \
                mock.patch("requests.delete") as delete:
            create.return_value.raise_for_status.side_effect = RuntimeError("create failed")
            with self.assertRaises(RuntimeError):
                generator.record(spec, "http://wrapper.invalid")
            delete.assert_not_called()


class ProjectionTest(unittest.TestCase):
    def test_an_exposure_keeps_every_compared_field_and_drops_the_timestamp(self):
        data = {"id": 1, "name": "e", "unit": "session_id", "variant": 1, "assigned": True,
                "eligible": True, "overridden": False, "fullOn": False, "custom": False,
                "audienceMismatch": False, "ruleOverride": True, "exposedAt": 123}
        event = {"type": "exposure", "data": data, "timestamp": 123}
        expected = {k: v for k, v in data.items() if k not in ("ruleOverride", "exposedAt")}
        self.assertEqual(generator.project_event(event), {"type": "exposure", "data": expected})
        self.assertEqual(generator.project_event(event, include_rule_override=True),
                         {"type": "exposure", "data": {**expected, "ruleOverride": True}})

    def test_ready_events_keep_only_experiment_ids_and_names(self):
        event = {"type": "ready", "data": {"experiments": [{"id": 1, "name": "e", "split": [1]}]}}
        self.assertEqual(generator.project_event(event),
                         {"type": "ready", "data": {"experiments": [{"id": 1, "name": "e"}]}})


class RecordingReferenceTest(unittest.TestCase):
    """JAVA_SDK_COMMIT is on java-sdk's 1.x API, so it is recorded through java-v1-wrapper (the java lane's
    java-wrapper needs 2.x and cannot build it), from the java-v1 lane's checkout."""

    def test_the_recorder_fetches_the_pin_before_checkout(self):
        fetch = "git -C ../java-sdk-v1 fetch --depth 1 origin <JAVA_SDK_COMMIT>"
        checkout = "git -C ../java-sdk-v1 checkout --detach <JAVA_SDK_COMMIT>"
        self.assertIn(fetch, generator.__doc__)
        self.assertIn(checkout, generator.__doc__)
        self.assertLess(generator.__doc__.index(fetch), generator.__doc__.index(checkout))

    def test_the_recorder_names_the_1x_wrapper_and_checkout(self):
        self.assertEqual(os.path.basename(generator.JAVA_SDK_PATH), "java-sdk-v1")
        self.assertIn("docker compose up -d java-v1-sdk", generator.__doc__)
        self.assertNotIn("docker compose up -d java-sdk ", generator.__doc__)
        with open(os.path.join(REPO_ROOT, "java-v1-wrapper", "Dockerfile"), encoding="utf-8") as f:
            self.assertIn("ARG SDK_SOURCE_DIR=java-sdk-v1", f.read())
        with open(os.path.join(REPO_ROOT, "java-v1-wrapper", "src", "main", "java", "com", "absmartly", "wrapper",
                               "WrapperController.java"), encoding="utf-8") as f:
            controller = f.read()
        self.assertIn("ABSmartly.create(", controller)  # the 1.x entry point
        self.assertIn('response.put("sdkCommit", SDK_COMMIT);', controller)


class SdkRevisionPinTest(unittest.TestCase):
    """Exact-commit pins (SDK_REVISIONS in sdk-branches.sh) are full SHAs, and both CI and local
    setup check them out."""

    def read(self, *path):
        with open(os.path.join(REPO_ROOT, *path), encoding="utf-8") as f:
            return f.read()

    def test_pins_are_full_shas(self):
        block = re.search(r"declare -A SDK_REVISIONS=\((.*?)\)", self.read("sdk-branches.sh"), re.S)
        self.assertIsNotNone(block)
        pins = dict(re.findall(r'\["([^"]+)"\]="([^"]*)"', block.group(1)))
        for repo, sha in pins.items():
            self.assertRegex(sha, r"^[0-9a-f]{40}$", repo)

    def test_the_java_wrappers_report_no_commit_for_a_modified_tree(self):
        for wrapper in ("java-wrapper", "java-v1-wrapper"):
            with self.subTest(wrapper=wrapper):
                dockerfile = self.read(wrapper, "Dockerfile")
                self.assertIn('if head=$(git rev-parse HEAD 2>/dev/null) && status=$(git status --porcelain 2>/dev/null)',
                              dockerfile)
                self.assertIn('&& [ -z "$status" ]; then echo "$head"; fi > /root/.m2/sdk-commit.txt', dockerfile)
                self.assertNotIn("--untracked-files=no", dockerfile)

    def test_checkout_helper_checks_out_the_requested_sha(self):
        """Run sdk-checkout.sh's fetch_and_checkout_revision against a local remote whose tip
        is newer than the pin: the checked-out HEAD must be the pin, not the tip."""
        import subprocess
        import tempfile

        def git(*args, cwd):
            return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                                  text=True).stdout.strip()

        with tempfile.TemporaryDirectory() as tmp:
            remote = os.path.join(tmp, "remote")
            os.mkdir(remote)
            git("init", "-q", cwd=remote)
            git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=remote)
            for n in ("pin", "tip"):
                with open(os.path.join(remote, "f"), "w") as f:
                    f.write(n)
                git("add", "f", cwd=remote)
                git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", n, cwd=remote)
            tip = git("rev-parse", "HEAD", cwd=remote)
            pin = git("rev-parse", "HEAD~1", cwd=remote)
            checkout = os.path.join(tmp, "checkout")
            os.mkdir(checkout)
            git("init", "-q", cwd=checkout)
            helper = os.path.join(REPO_ROOT, "sdk-checkout.sh")
            subprocess.run(["bash", "-c", f'source "{helper}"; fetch_and_checkout_revision "$1" "$2" "$3"',
                            "_", checkout, "file://" + remote, pin], check=True, capture_output=True)
            self.assertNotEqual(pin, tip)
            self.assertEqual(git("rev-parse", "HEAD", cwd=checkout), pin)

    def test_ci_and_local_setup_call_the_checkout_helper(self):
        workflow = self.read(".github", "workflows", "cross-sdk.yml")
        self.assertIn('revision="${SDK_REVISIONS[$repo]:-}"', workflow)
        self.assertIn('fetch_and_checkout_revision "$repo" "https://github.com/absmartly/$source_repo.git" "$revision"',
                      workflow)
        setup = self.read("setup-sdks.sh")
        self.assertIn('revision="${SDK_REVISIONS[$repo]:-}"', setup)
        self.assertIn('checkout_revision "$repo" "$revision"', setup)


if __name__ == "__main__":
    unittest.main()
