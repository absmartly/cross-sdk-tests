"""Docker-free unit tests for the cross-SDK harness (orchestrator/*.py).

Run with:  python3 -m unittest discover -s tests -v
(only the standard library plus `requests`, which test_runner imports).
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))

import results_aggregator  # noqa: E402
import test_runner  # noqa: E402
from test_runner import MISSING, TestOrchestrator, values_match, result_matches  # noqa: E402

SCENARIOS_PATH = os.path.join(REPO_ROOT, "test_scenarios_complete.json")


def make_policy(sdks=None):
    return {
        "capabilities": {"diagnostics": "d", "holdouts": "h"},
        "sdks": sdks
        if sdks is not None
        else {
            "alpha": {"required": ["diagnostics", "holdouts"], "unsupported": {}},
            "beta": {"required": ["diagnostics"], "unsupported": {"holdouts": "beta SDK lacks holdouts"}},
        },
    }


def make_orchestrator(sdks=None, policy=None, **kwargs):
    sdks = sdks if sdks is not None else {"alpha": "http://alpha", "beta": "http://beta"}
    return TestOrchestrator(sdks, capability_policy=policy or make_policy(), **kwargs)


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class ValuesMatchTests(unittest.TestCase):
    def test_bool_is_not_a_number(self):
        self.assertFalse(values_match(1, True))
        self.assertFalse(values_match(True, 1))
        self.assertFalse(values_match(0, False))
        self.assertFalse(values_match([1, 0], [True, False]))
        self.assertFalse(values_match([1, 0], [True, False], unordered=True))

    def test_int_equals_float(self):
        self.assertTrue(values_match(1.0, 1))
        self.assertTrue(values_match(1, 1.0))
        self.assertTrue(values_match([1.0, 2], [2, 1], unordered=True))

    def test_null_is_not_string_or_zero(self):
        self.assertFalse(values_match("null", None))
        self.assertFalse(values_match(0, None))
        self.assertFalse(values_match(None, 0))
        self.assertFalse(values_match("", None))
        self.assertTrue(values_match(None, None))

    def test_string_is_not_number(self):
        self.assertFalse(values_match("1", 1))
        self.assertFalse(values_match(1, "1"))

    def test_missing_never_matches(self):
        self.assertFalse(values_match(MISSING, None))
        self.assertFalse(values_match(MISSING, []))
        self.assertFalse(values_match({}, {"a": None}))
        self.assertTrue(values_match({"a": None}, {"a": None}))

    def test_object_subset_semantics_preserved(self):
        actual = {"contextId": "ctx-1", "ready": True, "failed": False, "extra": 1}
        self.assertTrue(values_match(actual, {"ready": True, "failed": False}))
        self.assertFalse(values_match(actual, {"ready": False}))

    def test_type_mismatch_container_vs_scalar(self):
        self.assertFalse(values_match("abc", ["abc"]))
        self.assertFalse(values_match(["a"], "a"))
        self.assertFalse(values_match([], {}))
        self.assertFalse(values_match({}, []))
        self.assertFalse(values_match({"a": 1}, 1))

    def test_unordered_preserves_duplicates(self):
        self.assertFalse(values_match(["a", "a", "b"], ["a", "b", "b"], unordered=True))
        self.assertTrue(values_match(["b", "a", "a"], ["a", "a", "b"], unordered=True))

    def test_lists_ordered_by_default(self):
        self.assertFalse(values_match(["b", "a"], ["a", "b"]))
        self.assertTrue(values_match(["a", "b"], ["a", "b"]))

    def test_mixed_scalar_types_unordered_do_not_crash(self):
        self.assertTrue(values_match([None, "a", 1], ["a", 1, None], unordered=True))
        self.assertFalse(values_match([None, "a"], ["a", "None"], unordered=True))


class ResultMatchesTests(unittest.TestCase):
    def test_unordered_allowlist(self):
        for action in ("variableKeys", "customFieldKeys", "globalCustomFieldKeys", "experiments"):
            self.assertTrue(result_matches(action, ["b", "a"], ["a", "b"]), action)

    def test_other_actions_are_ordered(self):
        self.assertFalse(result_matches("customFieldValue", ["world", "hello"], ["hello", "world"]))
        self.assertFalse(result_matches("diagnostic", [116, 101], [101, 116]))

    def test_ordered_result_overrides_allowlist(self):
        self.assertFalse(result_matches("variableKeys", ["b", "a"], ["a", "b"], ordered=True))

    def test_variable_keys_map_values_unordered(self):
        expected = {"button.color": ["x", "y"], "banner": ["z"]}
        self.assertTrue(result_matches("variableKeysMap", {"button.color": ["y", "x"], "banner": ["z"]}, expected))
        self.assertFalse(result_matches("variableKeysMap", {"button.color": ["x"], "banner": ["z"]}, expected))
        self.assertFalse(result_matches("variableKeysMap", {"banner": ["z"]}, expected))
        self.assertFalse(result_matches("variableKeysMap", None, expected))

    def test_absent_result_does_not_match_null(self):
        self.assertFalse(result_matches("track", MISSING, None))

    def test_data_results_require_exact_key_set(self):
        self.assertFalse(result_matches("getUnits", {"session_id": "a", "extra": 1}, {"session_id": "a"}))
        self.assertFalse(result_matches("getAttributes", {"country": "US", "x": None}, {"country": "US"}))
        self.assertFalse(result_matches("customFieldValue", {"123": 1, "456": 0, "7": 2}, {"123": 1, "456": 0}))
        self.assertFalse(result_matches("customFieldValue", [{"a": 1, "b": 2}], [{"a": 1}]))
        self.assertFalse(
            result_matches("variableKeysMap", {"button.color": ["x"], "banner": ["z"]}, {"button.color": ["x"]})
        )
        self.assertTrue(result_matches("getUnits", {"session_id": "a"}, {"session_id": "a"}))

    def test_status_results_stay_partial(self):
        actual = {"contextId": "ctx-1", "ready": True, "failed": False, "finalized": False}
        for action in ("createContext", "createContextWith", "createContextFailed"):
            self.assertTrue(result_matches(action, actual, {"ready": True, "failed": False}), action)
        self.assertTrue(result_matches("readyError", {"isError": True, "message": "boom"}, {"isError": True}))


class ValidateStepTests(unittest.TestCase):
    def setUp(self):
        self.orch = make_orchestrator()

    def fields(self, failures):
        return [f.get("field", f.get("error")) for f in failures]

    def test_absent_result_fails_expected_null(self):
        failures = self.orch.validate_step(0, "track", {"events": []}, {"result": None, "events": []})
        self.assertEqual(self.fields(failures), ["result"])
        self.assertEqual(failures[0]["actual"], "<missing>")

    def test_absent_events_fails_expected_empty(self):
        failures = self.orch.validate_step(0, "track", {"result": None}, {"result": None, "events": []})
        self.assertEqual(self.fields(failures), ["events"])

    def test_unexpected_events_fail_when_step_omits_events(self):
        actual = {"result": None, "events": [{"type": "goal", "data": {}}]}
        failures = self.orch.validate_step(0, "track", actual, {"result": None})
        self.assertEqual(self.fields(failures), ["events"])
        self.assertIn("goal", failures[0]["actual"])

    def test_goal_properties_null_matches_omitted_member(self):
        actual = {"result": None, "events": [{"type": "goal", "data": {"name": "g"}}]}
        expected = {"result": None, "events": [{"type": "goal", "data": {"name": "g", "properties": None}}]}
        self.assertEqual(self.orch.validate_step(0, "track", actual, expected), [])

    def test_goal_properties_omitted_still_fails_for_non_null(self):
        actual = {"result": None, "events": [{"type": "goal", "data": {"name": "g"}}]}
        expected = {"result": None, "events": [{"type": "goal", "data": {"name": "g", "properties": {"a": 1}}}]}
        self.assertEqual(self.fields(self.orch.validate_step(0, "track", actual, expected)), ["events[0].data.properties"])

    def test_null_exemption_is_limited_to_goal_properties(self):
        actual = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1}}]}
        expected = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1, "properties": None}}]}
        self.assertEqual(self.fields(self.orch.validate_step(0, "treatment", actual, expected)), ["events[0].data.properties"])

    def test_no_events_ok_when_step_omits_events(self):
        self.assertEqual(self.orch.validate_step(0, "createContext", {"result": {"ready": True}, "events": []}, {"result": {"ready": True}}), [])
        self.assertEqual(self.fields(self.orch.validate_step(0, "createContext", {"result": {"ready": True}}, {"result": {"ready": True}})), ["events"])

    def test_event_count_mismatch(self):
        actual = {"result": 1, "events": [{"type": "exposure"}, {"type": "exposure"}]}
        failures = self.orch.validate_step(0, "treatment", actual, {"result": 1, "events": [{"type": "exposure"}]})
        self.assertEqual(self.fields(failures), ["events.length"])
        self.assertEqual(failures[0]["actual_types"], ["exposure", "exposure"])

    def test_event_data_subset_with_dynamic_fields(self):
        actual = {
            "result": None,
            "events": [
                {
                    "type": "publish",
                    "timestamp": 123,
                    "data": {"hashed": True, "publishedAt": 999, "units": [{"type": "session_id", "uid": "abc"}]},
                }
            ],
        }
        expected = {"result": None, "events": [{"type": "publish", "data": {"hashed": True, "units": [{"type": "session_id"}]}}]}
        self.assertEqual(self.orch.validate_step(0, "publish", actual, expected), [])

    def test_event_data_key_absent_vs_null(self):
        actual = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1}}]}
        expected = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1, "custom": None}}]}
        failures = self.orch.validate_step(0, "treatment", actual, expected)
        self.assertEqual(self.fields(failures), ["events[0].data.custom"])

    def test_event_data_bool_vs_int(self):
        actual = {"result": 1, "events": [{"type": "exposure", "data": {"assigned": 1}}]}
        expected = {"result": 1, "events": [{"type": "exposure", "data": {"assigned": True}}]}
        self.assertEqual(self.fields(self.orch.validate_step(0, "treatment", actual, expected)), ["events[0].data.assigned"])

    def test_event_data_non_object(self):
        actual = {"result": 1, "events": [{"type": "exposure", "data": None}]}
        expected = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1}}]}
        self.assertEqual(self.fields(self.orch.validate_step(0, "treatment", actual, expected)), ["events[0].data"])

    def test_non_object_event(self):
        actual = {"result": 1, "events": ["exposure"]}
        expected = {"result": 1, "events": [{"type": "exposure"}]}
        self.assertEqual(self.fields(self.orch.validate_step(0, "treatment", actual, expected)), ["events[0]"])

    def test_events_not_a_list(self):
        actual = {"result": 1, "events": {"type": "exposure"}}
        failures = self.orch.validate_step(0, "treatment", actual, {"result": 1, "events": [{"type": "exposure"}]})
        self.assertEqual(self.fields(failures), ["events"])

    def test_body_not_object(self):
        failures = self.orch.validate_step(0, "treatment", [1], {"result": 1})
        self.assertIn("JSON object", failures[0]["error"])

    def test_expected_error_but_success(self):
        failures = self.orch.validate_step(0, "setUnit", {"result": None, "events": []}, {"error": "UID must not be blank"})
        self.assertEqual(self.fields(failures), ["error"])

    def test_numeric_float_result_equal_int(self):
        self.assertEqual(self.orch.validate_step(0, "getUnit", {"result": 99.0, "events": []}, {"result": 99, "events": []}), [])

    def test_method_values_match_backwards_compatible(self):
        # Historical signature: ordered=False compares scalar lists as multisets.
        self.assertTrue(self.orch.values_match(["b", "a"], ["a", "b"]))
        self.assertFalse(self.orch.values_match(["b", "a"], ["a", "b"], ordered=True))

    def test_report_is_json_serializable_with_missing(self):
        failures = self.orch.validate_step(0, "track", {}, {"result": None, "events": []})
        json.dumps(failures)


class ConstructorStateTests(unittest.TestCase):
    """run_scenario reads attributes the constructor must always set."""

    def test_verbose_flag_is_stored(self):
        self.assertTrue(make_orchestrator(verbose=True).verbose)
        self.assertFalse(make_orchestrator().verbose)

    def test_run_scenario_reaches_the_sdk_loop(self):
        orch = make_orchestrator(sdks={})
        with quiet():
            result = orch.run_scenario({"name": "x", "steps": []})
        self.assertIn("sdks", result)


class ErrorPathEventTests(unittest.TestCase):
    """An expected error must not hide events emitted by the failed call."""

    def run_error_step(self, body, expect=None):
        import requests

        resp = requests.Response()
        resp.status_code = 400
        resp._content = json.dumps(body).encode()
        resp.url = "http://alpha/context/x/treatment"
        orch = make_orchestrator(policy=make_policy({"alpha": {"required": ["diagnostics", "holdouts"], "unsupported": {}}}))
        scenario = {
            "name": "err",
            "steps": [
                {
                    "action": "treatment",
                    "params": {"experimentName": "e"},
                    "expect": expect or {"error": "not yet ready", "events": []},
                }
            ],
        }
        with mock.patch.object(test_runner.requests, "post", return_value=resp), mock.patch.object(
            test_runner.requests, "delete"
        ):
            return orch.run_and_validate_sdk("alpha", "http://alpha", scenario)

    def test_events_on_error_response_fail(self):
        result = self.run_error_step(
            {"error": "ABsmartly Context is not yet ready.", "events": [{"type": "exposure", "data": {}}]}
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["field"], "events.length")

    def test_error_response_without_events_fails(self):
        result = self.run_error_step({"error": "ABsmartly Context is not yet ready."})
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["failures"][0]["field"], "events")
        self.assertIn("<missing>", result["failures"][0]["actual"])

    def test_error_response_with_empty_events_passes(self):
        result = self.run_error_step({"error": "ABsmartly Context is not yet ready.", "events": []})
        self.assertTrue(result["passed"], result)

    def test_error_response_events_must_match_listed_events(self):
        expect = {"error": "Publish failed", "events": [{"type": "error"}]}
        ok = self.run_error_step({"error": "Publish failed", "events": [{"type": "error", "data": "x"}]}, expect)
        self.assertTrue(ok["passed"], ok)
        missing = self.run_error_step({"error": "Publish failed", "events": []}, expect)
        self.assertEqual(missing["failures"][0]["field"], "events.length")
        wrong = self.run_error_step({"error": "Publish failed", "events": [{"type": "publish"}]}, expect)
        self.assertEqual(wrong["failures"][0]["field"], "events[0].type")

    def test_non_object_error_body_fails(self):
        result = self.run_error_step(["oops"])
        self.assertFalse(result["passed"])


class ErrorMatchTests(unittest.TestCase):
    def test_existing_rules_unchanged(self):
        orch = make_orchestrator()
        self.assertTrue(orch.error_matches("Context finalized", "Context finalized"))
        self.assertTrue(orch.error_matches("ABSmartly Context is finalized.", "Context finalized"))
        self.assertTrue(orch.error_matches("Unit 'session_id' UID already set.", "Unit 'session_id' UID already set."))
        self.assertFalse(orch.error_matches("finalized", "Context finalized"))
        self.assertFalse(orch.error_matches("something else", "Context finalized"))

    def test_negation_is_significant(self):
        orch = make_orchestrator()
        self.assertFalse(orch.error_matches("context is not finalized", "Context finalized"))
        self.assertFalse(orch.error_matches("Context is finalized", "Context not finalized"))
        self.assertFalse(orch.error_matches("Context can't be finalized", "Context finalized"))
        self.assertFalse(orch.error_matches("UID must be blank", "UID must not be blank"))
        self.assertTrue(orch.error_matches("Unit 'x' UID must not be blank.", "UID must not be blank"))
        self.assertTrue(orch.error_matches("ABsmartly Context is not yet ready.", "not yet ready"))
        loose = make_orchestrator(loose_error_match=True)
        self.assertFalse(loose.error_matches("context is not finalized", "Context finalized"))


class CapabilityPolicyTests(unittest.TestCase):
    def test_checked_in_policy_is_valid(self):
        policy = test_runner.load_capability_policy()
        self.assertEqual(test_runner.validate_capability_policy(policy), [])

    def test_checked_in_policy_covers_every_compose_sdk(self):
        policy = test_runner.load_capability_policy()
        with open(os.path.join(REPO_ROOT, "docker-compose.yml")) as src:
            services = re.findall(r"^  ([a-z0-9]+)-sdk:\s*$", src.read(), re.MULTILINE)
        self.assertTrue(services)
        self.assertEqual(sorted(set(services) - set(policy["sdks"])), [])

    def test_checked_in_policy_covers_every_scenario_requirement(self):
        policy = test_runner.load_capability_policy()
        with open(SCENARIOS_PATH) as src:
            scenarios = json.load(src)
        used = {cap for s in scenarios for cap in s.get("requires", [])}
        self.assertEqual(sorted(used - set(policy["capabilities"])), [])

    def test_holdouts_required_only_where_evidenced(self):
        policy = test_runner.load_capability_policy()
        holders = sorted(
            sdk for sdk, entry in policy["sdks"].items() if {"holdouts", "holdout_arms"} & set(entry["required"])
        )
        self.assertEqual(holders, ["angular", "java", "javascript", "react", "vue2", "vue3"])
        for sdk, entry in policy["sdks"].items():
            for cap, reason in entry["unsupported"].items():
                self.assertTrue(reason.strip(), f"{sdk}.{cap} needs a reason")

    def test_invalid_policies_rejected(self):
        self.assertTrue(test_runner.validate_capability_policy([]))
        bad = make_policy({"alpha": {"required": ["diagnostics"], "unsupported": {}}})
        self.assertTrue(any("neither required nor" in e for e in test_runner.validate_capability_policy(bad)))
        bad = make_policy({"alpha": {"required": ["diagnostics", "holdouts"], "unsupported": {"holdouts": "x"}}})
        self.assertTrue(any("both required and unsupported" in e for e in test_runner.validate_capability_policy(bad)))
        bad = make_policy({"alpha": {"required": ["diagnostics"], "unsupported": {"holdouts": " "}}})
        self.assertTrue(any("one-line reason" in e for e in test_runner.validate_capability_policy(bad)))
        bad = make_policy({"alpha": {"required": ["diagnostics", "holdouts", "nope"], "unsupported": {}}})
        self.assertTrue(any("not declared" in e for e in test_runner.validate_capability_policy(bad)))
        with self.assertRaises(test_runner.ConfigError):
            make_orchestrator(policy=make_policy({"alpha": {"required": [], "unsupported": {}}}))

    def test_load_policy_errors_are_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.json")
            with self.assertRaisesRegex(test_runner.ConfigError, "not found"):
                test_runner.load_capability_policy(missing)
            broken = os.path.join(tmp, "broken.json")
            with open(broken, "w") as out:
                out.write("{")
            with self.assertRaisesRegex(test_runner.ConfigError, "not valid JSON"):
                test_runner.load_capability_policy(broken)


class CapabilitiesResponseTests(unittest.TestCase):
    known = {"diagnostics", "holdouts"}

    def test_valid_with_unrelated_extra_keys(self):
        payload = {"diagnostics": True, "holdouts": False, "attrsSeq": "whatever", "angularServiceAvailable": 1}
        self.assertIsNone(test_runner.validate_capabilities_response(payload, self.known))

    def test_non_object(self):
        self.assertIn("JSON object", test_runner.validate_capabilities_response([], self.known))

    def test_non_boolean_flag(self):
        self.assertIn("boolean", test_runner.validate_capabilities_response({"holdouts": "true"}, self.known))
        self.assertIn("boolean", test_runner.validate_capabilities_response({"diagnostics": 1}, self.known))

    def test_metadata_types(self):
        self.assertIsNotNone(test_runner.validate_capabilities_response({"isWrapper": "yes"}, self.known))
        self.assertIsNotNone(test_runner.validate_capabilities_response({"passThroughOperations": "peek"}, self.known))
        self.assertIsNotNone(test_runner.validate_capabilities_response({"passThroughOperations": [1]}, self.known))

    def test_fetch_marks_malformed_body_as_caps_failure(self):
        orch = make_orchestrator()
        response = mock.Mock(status_code=200)
        response.json.return_value = {"diagnostics": "yes"}
        with mock.patch.object(test_runner.requests, "get", return_value=response), quiet():
            orch.fetch_capabilities("alpha", "http://alpha")
        self.assertIn("alpha", orch.capabilities_fetch_failed)
        self.assertIn("boolean", orch.capabilities_fetch_failed["alpha"])

    def test_fetch_records_valid_body(self):
        orch = make_orchestrator()
        response = mock.Mock(status_code=200)
        response.json.return_value = {"diagnostics": True, "holdouts": True}
        with mock.patch.object(test_runner.requests, "get", return_value=response), quiet():
            orch.fetch_capabilities("alpha", "http://alpha")
        self.assertNotIn("alpha", orch.capabilities_fetch_failed)
        self.assertEqual(orch.capability_policy_violations, {})


class CapabilityGateTests(unittest.TestCase):
    holdout_scenario = {"name": "h", "requires": ["holdouts"], "steps": [{"action": "treatment", "expect": {}}]}

    def test_required_capability_false_is_violation_and_scenario_runs(self):
        orch = make_orchestrator()
        with quiet():
            orch.set_capabilities("alpha", {"diagnostics": True, "holdouts": False})
        self.assertEqual(len(orch.capability_policy_violations["alpha"]), 1)
        self.assertIn("holdouts", orch.capability_policy_violations["alpha"][0])
        self.assertIsNone(orch.capability_gate("alpha", self.holdout_scenario))

    def test_required_capability_missing_is_violation(self):
        orch = make_orchestrator()
        with quiet():
            orch.set_capabilities("alpha", {"diagnostics": True})
        self.assertIn("missing", orch.capability_policy_violations["alpha"][0])

    def test_reviewed_exception_skips_with_reason(self):
        orch = make_orchestrator()
        with quiet():
            orch.set_capabilities("beta", {"diagnostics": True, "holdouts": False})
        self.assertNotIn("beta", orch.capability_policy_violations)
        reason = orch.capability_gate("beta", self.holdout_scenario)
        self.assertIn("reviewed exception", reason)
        self.assertIn("beta SDK lacks holdouts", reason)

    def test_exception_advertised_true_runs_and_flags_policy(self):
        orch = make_orchestrator()
        with quiet():
            orch.set_capabilities("beta", {"diagnostics": True, "holdouts": True})
        self.assertIn("move it to 'required'", orch.capability_policy_violations["beta"][0])
        self.assertIsNone(orch.capability_gate("beta", self.holdout_scenario))

    def test_unknown_sdk_fails_closed(self):
        orch = make_orchestrator(sdks={"gamma": "http://gamma"})
        with quiet():
            orch.set_capabilities("gamma", {"diagnostics": True})
        self.assertIn("no entry", orch.capability_policy_violations["gamma"][0])
        self.assertIn("no capability policy entry", orch.capability_gate("gamma", self.holdout_scenario))

    def test_caps_fetch_failure_skips_gated(self):
        orch = make_orchestrator()
        orch.capabilities_fetch_failed["alpha"] = "HTTP 500"
        self.assertIn("capabilities fetch failed", orch.capability_gate("alpha", self.holdout_scenario))

    def test_ungated_scenario_runs(self):
        orch = make_orchestrator()
        self.assertIsNone(orch.capability_gate("beta", {"name": "x", "steps": []}))


class WrapperSkipTests(unittest.TestCase):
    caps = {
        "diagnostics": True,
        "holdouts": False,
        "isWrapper": True,
        "wrapsSDK": "alpha",
        "passThroughOperations": ["treatment", "holdoutThing"],
    }
    scenario = {"name": "s", "steps": [{"action": "createContext"}, {"action": "treatment"}]}

    def test_default_is_strict(self):
        orch = make_orchestrator()
        self.assertFalse(orch.allow_wrapper_skip)
        orch.capabilities["beta"] = self.caps
        self.assertEqual(orch.should_skip_wrapper_test("beta", self.scenario), (False, None))

    def test_opt_in_skips_pass_through(self):
        orch = make_orchestrator(allow_wrapper_skip=True)
        orch.capabilities["beta"] = self.caps
        skip, reason = orch.should_skip_wrapper_test("beta", self.scenario)
        self.assertTrue(skip)
        self.assertIn("Pass-through", reason)

    def test_capability_gate_checked_before_pass_through(self):
        orch = make_orchestrator(allow_wrapper_skip=True)
        orch.capabilities["beta"] = self.caps
        scenario = {"name": "s", "requires": ["holdouts"], "steps": [{"action": "treatment", "expect": {}}]}
        result = orch.run_and_validate_sdk("beta", "http://beta", scenario)
        self.assertTrue(result["skipped"])
        self.assertIn("reviewed exception", result["reason"])
        self.assertNotIn("passthrough", result)

    def test_cli_flags(self):
        parser = test_runner.build_arg_parser()
        self.assertFalse(parser.parse_args([]).wrapper_skip)
        self.assertFalse(parser.parse_args(["--no-wrapper-skip"]).wrapper_skip)
        self.assertTrue(parser.parse_args(["--wrapper-skip"]).wrapper_skip)
        with quiet(), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--wrapper-skip", "--no-wrapper-skip"])

    def test_run_tests_sh_does_not_force_wrapper_skip(self):
        for script_name in ("run-tests.sh", "verify_sdk.sh"):
            with open(os.path.join(REPO_ROOT, script_name)) as src:
                script = src.read()
            self.assertNotIn("allow_wrapper_skip=True", script, script_name)
            self.assertNotIn("'steps' in s", script, script_name)
        with open(os.path.join(REPO_ROOT, "run-tests.sh")) as src:
            self.assertIn("orchestrator/test_runner.py", src.read())


class ScenarioLoaderTests(unittest.TestCase):
    def test_repo_scenarios_valid(self):
        policy = test_runner.load_capability_policy()
        valid, empty, invalid = test_runner.load_scenarios(SCENARIOS_PATH, set(policy["capabilities"]))
        self.assertEqual(invalid, [])
        self.assertEqual(empty, [])
        self.assertGreater(len(valid), 200)
        self.assertTrue(any(s["name"] == test_runner.CREATE_WITH_SCENARIO for s in valid))

    def test_unknown_requires_is_invalid(self):
        scenarios = [{"name": "x", "requires": ["telepathy"], "steps": [{"action": "a", "expect": {}}]}]
        _, _, invalid = test_runner.validate_and_filter_scenarios(scenarios, {"holdouts"})
        self.assertEqual(len(invalid), 1)
        self.assertIn("telepathy", invalid[0]["reason"])

    def test_malformed_requires_is_invalid(self):
        for requires in ("holdouts", [], [1]):
            scenarios = [{"name": "x", "requires": requires, "steps": [{"action": "a", "expect": {}}]}]
            _, _, invalid = test_runner.validate_and_filter_scenarios(scenarios, {"holdouts"})
            self.assertEqual(len(invalid), 1, requires)

    def test_step_shape_errors(self):
        cases = [
            ({"name": "x", "steps": "nope"}, "steps must be an array"),
            ({"name": "x", "steps": [1]}, "not an object"),
            ({"name": "x", "steps": [{"expect": {}}]}, "missing 'action'"),
            ({"name": "x", "steps": [{"action": "a"}]}, "missing 'expect'"),
            ({"name": "x", "steps": [{"action": "a", "expect": []}]}, "'expect' must be an object"),
            ({"name": "x", "steps": [{"action": "a", "expect": {"events": {}}}]}, "'expect.events' must be an array"),
            ({"steps": [{"action": "a", "expect": {}}]}, "missing 'name'"),
        ]
        for scenario, reason in cases:
            _, _, invalid = test_runner.validate_and_filter_scenarios([scenario])
            self.assertEqual(len(invalid), 1, scenario)
            self.assertIn(reason, invalid[0]["reason"])

    def test_duplicate_names_invalid(self):
        s = {"name": "x", "steps": [{"action": "a", "expect": {}}]}
        _, _, invalid = test_runner.validate_and_filter_scenarios([s, dict(s)])
        self.assertEqual(invalid[0]["reason"], "duplicate scenario name")

    def test_empty_steps_excluded_and_params_defaulted(self):
        valid, empty, _ = test_runner.validate_and_filter_scenarios(
            [{"name": "e", "steps": []}, {"name": "v", "steps": [{"action": "a", "expect": {}}]}]
        )
        self.assertEqual([e["name"] for e in empty], ["e"])
        self.assertEqual(valid[0]["steps"][0]["params"], {})

    def test_load_errors_are_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(test_runner.ConfigError, "not found"):
                test_runner.load_scenarios(os.path.join(tmp, "x.json"))
            path = os.path.join(tmp, "bad.json")
            with open(path, "w") as out:
                out.write("[")
            with self.assertRaisesRegex(test_runner.ConfigError, "not valid JSON"):
                test_runner.load_scenarios(path)

    def test_sdk_urls_override(self):
        with mock.patch.dict(os.environ, {"SDK_URLS_OVERRIDE": "go=http://localhost:1,python=http://localhost:2/"}):
            self.assertEqual(
                test_runner.discover_sdks(), {"go": "http://localhost:1", "python": "http://localhost:2"}
            )


class ConsistencyTests(unittest.TestCase):
    def run_obs(self, orch, scenario, sdk, step, action, actual, expected):
        orch.record_outcome(scenario, sdk, "pass")
        orch.step_observations.setdefault(scenario, {}).setdefault(sdk, {})[step] = (
            action,
            test_runner.observation_for_step(action, actual, expected),
        )

    def test_volatile_fields_ignored(self):
        orch = make_orchestrator(sdks={"a": "", "b": ""})
        expected = {"result": {"ready": True}, "events": [{"type": "ready"}]}
        self.run_obs(orch, "s", "a", 0, "createContext",
                     {"result": {"contextId": "ctx-1", "ready": True}, "events": [{"type": "ready", "timestamp": 1, "data": {"experiments": [{"id": 1, "name": "e", "seedHi": 1}]}}]}, expected)
        self.run_obs(orch, "s", "b", 0, "createContext",
                     {"result": {"contextId": "ctx-2", "ready": True}, "events": [{"type": "ready", "timestamp": 2, "data": {"experiments": [{"id": 1, "name": "e"}]}}]}, expected)
        expected_pub = {"result": None, "events": [{"type": "publish"}]}
        pub_a = {"publishedAt": 1, "sdkVersion": "1.0", "hashed": True, "units": [{"type": "u", "uid": "h"}],
                 "exposures": [{"id": 1, "name": "e", "exposedAt": 3}],
                 "attributes": [{"name": "sdk_name", "value": "a", "setAt": 1}, {"name": "x", "value": 1, "setAt": 1}]}
        pub_b = {"publishedAt": 2, "sdkVersion": "2.0", "hashed": True, "units": [{"type": "u", "uid": "h"}],
                 "exposures": [{"id": 1, "name": "e", "exposedAt": 4}], "goals": [],
                 "attributes": [{"name": "x", "value": 1, "setAt": 9}, {"name": "sdk_name", "value": "b"}]}
        self.run_obs(orch, "s", "a", 1, "publish", {"result": None, "events": [{"type": "publish", "data": pub_a}]}, expected_pub)
        self.run_obs(orch, "s", "b", 1, "publish", {"result": None, "events": [{"type": "publish", "data": pub_b}]}, expected_pub)
        orch.check_cross_sdk_consistency()
        self.assertEqual(orch.consistency_report, [])
        self.assertEqual(orch.consistency_comparisons, 3)

    def test_payload_drift_detected(self):
        def obs(action, data, etype):
            return test_runner.observation_for_step(action, {"result": None, "events": [{"type": etype, "data": data}]}, {})

        base_pub = {"hashed": True, "units": [{"type": "u", "uid": "h"}], "goals": [{"name": "g", "properties": {"a": 1}}]}
        self.assertNotEqual(obs("publish", base_pub, "publish"), obs("publish", dict(base_pub, units=[{"type": "u", "uid": "H"}]), "publish"))
        self.assertNotEqual(obs("publish", base_pub, "publish"), obs("publish", dict(base_pub, goals=[{"name": "g", "properties": None}]), "publish"))
        self.assertNotEqual(obs("publish", base_pub, "publish"), obs("publish", dict(base_pub, attributes=[{"name": "x", "value": 1}]), "publish"))
        ready = {"experiments": [{"id": 1, "name": "e"}]}
        self.assertNotEqual(obs("refresh", ready, "refresh"), obs("refresh", {"experiments": []}, "refresh"))
        self.assertNotEqual(obs("createContext", ready, "ready"), obs("createContext", {"experiments": [{"id": 2, "name": "e"}]}, "ready"))

    def test_error_response_events_observed(self):
        orch = make_orchestrator(sdks={"a": "", "b": "", "c": ""})
        import requests

        def resp(body):
            r = requests.Response()
            r.status_code = 500
            r._content = json.dumps(body).encode()
            return r

        scenario = {"name": "s", "steps": [{"action": "publish", "params": {}, "expect": {"error": "Publish failed", "events": [{"type": "error"}]}}]}
        for sdk, events in (("a", [{"type": "error"}]), ("b", [{"type": "error"}]), ("c", [{"type": "publish"}])):
            with mock.patch.object(test_runner.requests, "post", return_value=resp({"error": "Publish failed", "events": events})), mock.patch.object(test_runner.requests, "delete"):
                outcome = orch.run_and_validate_sdk(sdk, "http://x", scenario)
            orch.record_outcome("s", sdk, "pass" if outcome["passed"] else "fail")
        orch.check_cross_sdk_consistency()
        self.assertIn("s [step 0 publish]", orch.consistency_failures.get("c", []))

    def test_compare_reports_across_lanes(self):
        def lane(sdk, variant):
            orch = make_orchestrator(sdks={sdk: ""})
            self.run_obs(orch, "s", sdk, 0, "treatment", {"result": variant, "events": []}, {"result": 1})
            path = os.path.join(self.tmp, f"{sdk}.json")
            with quiet():
                orch.generate_report(path)
            return path

        self.tmp = tempfile.mkdtemp()
        paths = [lane("a", 1), lane("b", 1), lane("c", 0)]
        with quiet():
            self.assertEqual(test_runner.compare_reports(paths[:2]), 0)
            self.assertEqual(test_runner.compare_reports(paths, os.path.join(self.tmp, "out.json")), 1)
            self.assertEqual(test_runner.compare_reports(paths[:1]), 1)
            self.assertEqual(test_runner.compare_reports([paths[0], paths[0]]), 1)
        with open(os.path.join(self.tmp, "out.json")) as src:
            self.assertEqual(json.load(src)["consistency_failures"], {"c": ["s [step 0 treatment]"]})

    def test_workflow_compares_every_lane(self):
        with open(os.path.join(REPO_ROOT, ".github", "workflows", "cross-sdk.yml")) as src:
            text = src.read()
        self.assertIn("--compare-reports", text)
        self.assertIn("pattern: report-*", text)

    def test_data_results_compared_whole(self):
        a = test_runner.observation_for_step("getUnits", {"result": {"u": 1}, "events": []}, {"result": {"u": 1}})
        b = test_runner.observation_for_step("getUnits", {"result": {"u": 1, "v": 2}, "events": []}, {"result": {"u": 1}})
        self.assertNotEqual(a, b)

    def test_unasserted_exposure_drift_detected(self):
        orch = make_orchestrator(sdks={"a": "", "b": "", "c": ""})
        expected = {"result": 1, "events": [{"type": "exposure"}]}
        good = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1, "name": "e", "variant": 1, "assigned": True, "exposedAt": 5}}]}
        drift = {"result": 1, "events": [{"type": "exposure", "data": {"id": 1, "name": "e", "variant": 1, "assigned": False}}]}
        self.run_obs(orch, "s", "a", 0, "treatment", good, expected)
        self.run_obs(orch, "s", "b", 0, "treatment", json.loads(json.dumps(good)), expected)
        self.run_obs(orch, "s", "c", 0, "treatment", drift, expected)
        orch.check_cross_sdk_consistency()
        self.assertEqual(orch.consistency_failures, {"c": ["s [step 0 treatment]"]})

    def test_unordered_and_numeric_normalization(self):
        orch = make_orchestrator(sdks={"a": "", "b": ""})
        exp = {"result": ["x", "y"], "events": []}
        self.run_obs(orch, "s", "a", 0, "variableKeys", {"result": ["y", "x"], "events": []}, exp)
        self.run_obs(orch, "s", "b", 0, "variableKeys", {"result": ["x", "y"], "events": []}, exp)
        exp2 = {"result": 99, "events": []}
        self.run_obs(orch, "s", "a", 1, "getUnit", {"result": 99.0, "events": []}, exp2)
        self.run_obs(orch, "s", "b", 1, "getUnit", {"result": 99, "events": []}, exp2)
        orch.check_cross_sdk_consistency()
        self.assertEqual(orch.consistency_report, [])

    def test_bool_vs_int_observation_differs(self):
        a = test_runner.observation_for_step("customFieldValue", {"result": True, "events": []}, {"result": True})
        b = test_runner.observation_for_step("customFieldValue", {"result": 1, "events": []}, {"result": True})
        self.assertNotEqual(a, b)

    def test_outcome_disagreement_and_tie(self):
        orch = make_orchestrator(sdks={"a": "", "b": ""})
        orch.record_outcome("s", "a", "pass")
        orch.record_outcome("s", "b", "fail")
        orch.check_cross_sdk_consistency()
        self.assertEqual(sorted(orch.consistency_failures), ["a", "b"])
        self.assertIsNone(orch.consistency_report[0]["majority"])

    def test_zero_comparisons_fails_run(self):
        orch = make_orchestrator()
        orch.results = [{"name": "s", "sdks": {"alpha": {"passed": True, "failures": []}, "beta": {"passed": True, "skipped": True, "reason": "r"}}}]
        orch.record_outcome("s", "alpha", "pass")
        orch.record_outcome("s", "beta", "skip")
        with tempfile.TemporaryDirectory() as tmp, quiet() as out:
            code = orch.generate_report(os.path.join(tmp, "report.json"))
            with open(os.path.join(tmp, "report.json")) as src:
                report = json.load(src)
        self.assertEqual(code, 1)
        self.assertTrue(report["sdk_stats"]["alpha"]["consistency_unchecked"])
        self.assertEqual(report["consistency_comparisons"], 0)
        self.assertNotIn("all SDKs agree", out.getvalue())
        self.assertIn("NOT CHECKED", out.getvalue())

    def test_single_sdk_not_unchecked(self):
        orch = make_orchestrator(sdks={"alpha": ""})
        orch.results = [{"name": "s", "sdks": {"alpha": {"passed": True, "failures": []}}}]
        with tempfile.TemporaryDirectory() as tmp, quiet():
            code = orch.generate_report(os.path.join(tmp, "r", "report.json"))
        self.assertEqual(code, 0)


class ReportFlagTests(unittest.TestCase):
    def report_for(self, orch):
        with tempfile.TemporaryDirectory() as tmp, quiet():
            path = os.path.join(tmp, "report.json")
            code = orch.generate_report(path)
            with open(path) as src:
                return code, json.load(src)

    def test_policy_violation_fails_run(self):
        orch = make_orchestrator(sdks={"alpha": ""})
        orch.results = [{"name": "s", "sdks": {"alpha": {"passed": True, "failures": []}}}]
        orch.capability_policy_violations["alpha"] = ["required capability 'holdouts' is not advertised"]
        code, report = self.report_for(orch)
        self.assertEqual(code, 1)
        self.assertEqual(len(report["sdk_stats"]["alpha"]["capability_policy_violations"]), 1)
        self.assertFalse(report["wrapper_skip_enabled"])

    def test_clean_run_passes(self):
        orch = make_orchestrator(sdks={"alpha": ""})
        orch.results = [{"name": "s", "sdks": {"alpha": {"passed": True, "failures": []}}}]
        code, report = self.report_for(orch)
        self.assertEqual(code, 0)
        self.assertEqual(report["sdk_stats"]["alpha"]["capability_policy_violations"], [])


class AggregatorTests(unittest.TestCase):
    def load(self, stats):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            with open(path, "w") as out:
                json.dump({"sdk_stats": {"x": stats}}, out)
            return results_aggregator.load_cross_sdk_report(path)["x"]

    base = {"passed": 10, "failed": 0, "errors": 0, "skipped": 0, "tested": 10}

    def assert_forced(self, flag, value, status):
        row = self.load(dict(self.base, **{flag: value}))
        self.assertEqual(row["forced_status"], status)
        with quiet():
            any_failure = results_aggregator.print_results_table({}, {"x": row}, ["x"], False, True)
        self.assertTrue(any_failure, flag)

    def test_capability_policy_violation_flag(self):
        self.assert_forced("capability_policy_violations", ["v"], "CAPS POLICY")

    def test_consistency_unchecked_flag(self):
        self.assert_forced("consistency_unchecked", True, "UNCHECKED")

    def test_existing_flags(self):
        self.assert_forced("service_failed", True, "DOWN")
        self.assert_forced("capabilities_failed", True, "CAPS FAIL")
        self.assert_forced("no_tests_run", True, "NO TESTS RUN")
        self.assert_forced("consistency_failed", ["s"], "INCONSISTENT")

    def test_clean_row_passes(self):
        row = self.load(dict(self.base, capability_policy_violations=[], consistency_unchecked=False))
        self.assertIsNone(row["forced_status"])
        with quiet():
            self.assertFalse(results_aggregator.print_results_table({}, {"x": row}, ["x"], False, True))

    def test_no_typescript_parser(self):
        self.assertNotIn("typescript", results_aggregator.UNIT_TEST_PARSERS)


class StaticRepoChecks(unittest.TestCase):
    """Cheap guards that keep docs and CI in sync with the canonical oracle."""

    def scenario_count(self):
        with open(SCENARIOS_PATH) as src:
            return len(json.load(src))

    def test_readme_scenario_count(self):
        with open(os.path.join(REPO_ROOT, "README.md")) as src:
            counts = {int(n) for n in re.findall(r"\*\*(\d+) scenarios\*\*", src.read())}
        self.assertEqual(counts, {self.scenario_count()})

    def test_workflow_header_scenario_count(self):
        with open(os.path.join(REPO_ROOT, ".github", "workflows", "cross-sdk.yml")) as src:
            counts = {int(n) for n in re.findall(r"(\d+)-scenario", src.read())}
        self.assertEqual(counts, {self.scenario_count()})

    def test_scenario_names_are_numbered_and_unique(self):
        with open(SCENARIOS_PATH) as src:
            names = [s["name"] for s in json.load(src)]
        self.assertEqual(len(names), len(set(names)))

    def test_http_client_scenarios_do_not_claim_fault_injection(self):
        # No fault is injected by the harness; names must not claim
        # retry/timeout/abort coverage that is not exercised.
        with open(SCENARIOS_PATH) as src:
            names = [s["name"] for s in json.load(src)]
        for name in names:
            self.assertNotRegex(name, r"(?i)\bretry\b|\btimeout after\b|\babort signal\b", name)

    def test_audience_scenarios_assert_audience_mismatch(self):
        # Every treatment on an experiment with an audience must pin the
        # exposure's audienceMismatch, or a matcher that always returns null
        # (or always true/false) passes unnoticed.
        with open(SCENARIOS_PATH) as src:
            scenarios = json.load(src)
        for s in scenarios:
            with_audience = {
                e["name"] for e in s.get("contextData", {}).get("experiments", []) if e.get("audience") not in (None, "")
            }
            for st in s["steps"]:
                if st["action"] != "treatment" or st["params"].get("experimentName") not in with_audience:
                    continue
                for e in st["expect"].get("events", []):
                    if e.get("type") == "exposure":
                        self.assertIn("audienceMismatch", e.get("data", {}), s["name"])

    def test_generator_is_marked_non_canonical(self):
        with open(os.path.join(REPO_ROOT, "generate_scenarios.py")) as src:
            head = src.read(2000)
        self.assertIn("CANONICAL", head)
        self.assertIn("test_scenarios_complete.json", head)


if __name__ == "__main__":
    unittest.main()
