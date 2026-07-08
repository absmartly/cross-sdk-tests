#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Tuple

import requests


# The one scenario that exercises the literal createContextWith API (pre-fetched
# data construction). Coupled by exact name: run_scenario routes only this
# scenario through the createContextWith path and every other createContext(With)
# through live fetch. If this scenario is renamed in test_scenarios_complete.json
# without updating this constant, that coverage silently reroutes to live-fetch,
# so main() prints a loud warning at startup when the name is absent.
CREATE_WITH_SCENARIO = "01 - Context Creation - Ready with Data"


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"


class TestOrchestrator:
    def __init__(
        self,
        sdks: Dict[str, str],
        verbose: bool = False,
        allow_wrapper_skip: bool = True,
        loose_error_match: bool = False,
    ):
        self.sdks = sdks
        self.results: List[Dict[str, Any]] = []
        self.capabilities: Dict[str, Dict[str, Any]] = {}
        # SDKs whose /capabilities endpoint errored (unreachable or non-200).
        # Maps sdk_name -> reason. These are distinct from an SDK that answered
        # /capabilities but simply lacks a capability (a legitimate skip).
        self.capabilities_fetch_failed: Dict[str, str] = {}
        # Per-scenario actual outcome ("pass"/"fail"/"skip"/"error") per SDK,
        # used for the cross-SDK consistency pass after all SDKs have run.
        self.scenario_outcomes: Dict[str, Dict[str, str]] = {}
        self.consistency_failures: Dict[str, List[str]] = {}
        self.consistency_report: List[Dict[str, Any]] = []
        self.verbose = verbose
        self.allow_wrapper_skip = allow_wrapper_skip
        self.loose_error_match = loose_error_match

    def wait_for_services(self) -> Tuple[Dict[str, str], List[str]]:
        print("Waiting for SDK services to be ready...")
        working_sdks: Dict[str, str] = {}
        failed_sdks: List[str] = []

        for sdk_name, base_url in self.sdks.items():
            max_retries = 60
            service_ready = False

            for i in range(max_retries):
                try:
                    response = requests.get(f"{base_url}/health", timeout=1)
                    if response.status_code == 200:
                        print(f"  {Colors.GREEN}✓{Colors.RESET} {sdk_name} ready")
                        working_sdks[sdk_name] = base_url
                        service_ready = True
                        self.fetch_capabilities(sdk_name, base_url)
                        break

                    # Non-200 health response: the service is up but not ready
                    # yet, so keep retrying. Sleep and report just like the
                    # exception branch below - otherwise a fast 500 would burn
                    # all 60 retries in milliseconds, silently.
                    if i == max_retries - 1:
                        print(
                            f"  {Colors.RED}✗{Colors.RESET} {sdk_name} failed to start "
                            f"(last /health status {response.status_code})"
                        )
                        failed_sdks.append(sdk_name)
                    else:
                        time.sleep(1)
                except Exception:
                    if i == max_retries - 1:
                        print(f"  {Colors.RED}✗{Colors.RESET} {sdk_name} failed to start")
                        failed_sdks.append(sdk_name)
                    else:
                        time.sleep(1)

            if not service_ready and sdk_name not in failed_sdks:
                failed_sdks.append(sdk_name)

        print()
        if failed_sdks:
            print(f"⚠️  {len(failed_sdks)} SDK(s) failed to start: {', '.join(failed_sdks)}")
            print(f"✓  Continuing with {len(working_sdks)} working SDK(s)\n")

        return working_sdks, failed_sdks

    def fetch_capabilities(self, sdk_name: str, base_url: str) -> None:
        try:
            caps_response = requests.get(f"{base_url}/capabilities", timeout=1)
            if caps_response.status_code == 200:
                self.capabilities[sdk_name] = caps_response.json()
                return
            reason = f"HTTP {caps_response.status_code}"
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__

        # The endpoint itself errored (unreachable or non-200) rather than
        # reporting a missing capability. Default caps so `requires:` scenarios
        # still skip instead of crashing, but record the failure loudly: the
        # report must not silently attribute those skips to a genuinely
        # unsupported feature, and this SDK's run must be marked failed.
        self.capabilities[sdk_name] = {"attrsSeq": False}
        self.capabilities_fetch_failed[sdk_name] = reason
        print(
            f"  {Colors.RED}⚠ CAPABILITIES FETCH FAILED{Colors.RESET} {sdk_name}: {reason} - "
            f"all `requires:`-gated scenarios will be skipped and this SDK's run marked FAILED"
        )

    def run_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        if self.verbose:
            print(f"\n{'=' * 60}")
            print(f"=== {scenario['name']}")
            print(f"{'=' * 60}")
            if scenario.get("description"):
                print(f"{scenario['description']}\n")

        scenario_results: Dict[str, Any] = {
            "name": scenario["name"],
            "description": scenario.get("description", ""),
            "sdks": {},
        }

        for sdk_name, base_url in self.sdks.items():
            try:
                result = self.run_and_validate_sdk(sdk_name, base_url, scenario)
                scenario_results["sdks"][sdk_name] = result

                if result.get("skipped"):
                    self.record_outcome(scenario["name"], sdk_name, "skip")
                    reason = result.get("reason", "Not supported")
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.YELLOW}⊘ SKIP{Colors.RESET} ({reason})")
                    else:
                        print(f"{Colors.YELLOW}SKIP{Colors.RESET}  {scenario['name']}: {reason}")
                elif result["passed"]:
                    self.record_outcome(scenario["name"], sdk_name, "pass")
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.GREEN}✓ PASS{Colors.RESET}")
                    else:
                        print(f"{Colors.GREEN}PASS{Colors.RESET}  {scenario['name']}")
                else:
                    self.record_outcome(scenario["name"], sdk_name, "fail")
                    failure_count = len(result["failures"])
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.RED}✗ FAIL{Colors.RESET} ({failure_count} failures)")
                        for failure in result["failures"][:3]:
                            print(f"      {failure}")
                    else:
                        first_failure = result["failures"][0] if result["failures"] else {}
                        error_msg = first_failure.get("error", first_failure.get("actual", ""))
                        print(f"{Colors.RED}FAIL{Colors.RESET}  {scenario['name']}: {error_msg}")
            except Exception as exc:
                self.record_outcome(scenario["name"], sdk_name, "error")
                if self.verbose:
                    print(f"  {sdk_name:20} {Colors.RED}✗ ERROR{Colors.RESET}: {exc}")
                else:
                    print(f"{Colors.RED}FAIL{Colors.RESET}  {scenario['name']}: {exc}")
                scenario_results["sdks"][sdk_name] = {"passed": False, "error": str(exc)}

        self.results.append(scenario_results)
        return scenario_results

    def record_outcome(self, scenario_name: str, sdk_name: str, outcome: str) -> None:
        self.scenario_outcomes.setdefault(scenario_name, {})[sdk_name] = outcome

    def should_skip_wrapper_test(self, sdk_name: str, scenario: Dict[str, Any]) -> Tuple[bool, Any]:
        if not self.allow_wrapper_skip:
            return False, None

        sdk_caps = self.capabilities.get(sdk_name, {})
        if not sdk_caps.get("isWrapper"):
            return False, None

        underlying_sdk = sdk_caps.get("wrapsSDK")
        if not underlying_sdk or underlying_sdk not in self.sdks:
            return False, None

        pass_through_ops = set(sdk_caps.get("passThroughOperations", []))
        if not pass_through_ops:
            return False, None

        scenario_actions = set()
        for step in scenario.get("steps", []):
            action = step.get("action")
            if action and action not in ("createContext", "createContextWith", "createContextFailed"):
                scenario_actions.add(action)

        if not scenario_actions:
            return False, None

        if scenario_actions.issubset(pass_through_ops):
            return True, f"Pass-through to {underlying_sdk} SDK"

        return False, None

    def run_and_validate_sdk(self, sdk_name: str, base_url: str, scenario: Dict[str, Any]) -> Dict[str, Any]:
        should_skip, skip_reason = self.should_skip_wrapper_test(sdk_name, scenario)
        if should_skip:
            return {"passed": True, "skipped": True, "reason": skip_reason}

        if "requires" in scenario:
            sdk_caps = self.capabilities.get(sdk_name, {})
            missing_caps = [cap for cap in scenario["requires"] if not sdk_caps.get(cap, False)]
            if missing_caps:
                if sdk_name in self.capabilities_fetch_failed:
                    reason = (
                        f"capabilities fetch failed ({self.capabilities_fetch_failed[sdk_name]}); "
                        f"cannot confirm support for: {', '.join(missing_caps)}"
                    )
                else:
                    reason = f"SDK does not support: {', '.join(missing_caps)}"
                return {
                    "passed": True,
                    "skipped": True,
                    "reason": reason,
                }

        steps = scenario.get("steps", [])
        if not steps:
            return {
                "passed": False,
                "failures": [
                    {
                        "step": -1,
                        "action": "scenario",
                        "error": "Scenario has no executable steps",
                    }
                ],
            }

        context_id = None
        payload_id = None
        failures: List[Dict[str, Any]] = []

        for step_index, step in enumerate(steps):
            action = step.get("action")
            params = step.get("params", {})
            expected = step.get("expect", {})

            if not action:
                failures.append(
                    {
                        "step": step_index,
                        "action": "unknown",
                        "error": "Step is missing required field: action",
                    }
                )
                continue

            if "expect" not in step:
                failures.append(
                    {
                        "step": step_index,
                        "action": action,
                        "error": "Step is missing required field: expect",
                    }
                )
                continue

            try:
                # Default: drive context creation through the live fetch path so the
                # SDK's real ContextDataProvider/Client fetches and deserializes the
                # payload from the wrapper endpoint (exactly as it would from the
                # collector). Exactly one scenario (CREATE_WITH_SCENARIO) keeps
                # the literal createContextWith API to cover pre-fetched-data
                # construction; its payload is passed as-is with no reshaping.
                use_create_with = (
                    action == "createContextWith"
                    and scenario.get("name") == CREATE_WITH_SCENARIO
                )

                if use_create_with:
                    response = requests.post(
                        f"{base_url}/context",
                        json={
                            "data": scenario["contextData"],
                            "units": params["units"],
                            "options": params.get("options", {}),
                        },
                        timeout=5,
                    )
                    response.raise_for_status()
                    data = response.json()
                    context_id = data["result"]["contextId"]

                elif action in ("createContext", "createContextWith"):
                    import uuid

                    payload_id = f"payload-{uuid.uuid4()}"
                    payload_response = requests.put(
                        f"{base_url}/context_payload/{payload_id}",
                        json={"data": scenario["contextData"]},
                        timeout=5,
                    )
                    payload_response.raise_for_status()

                    endpoint = f"{base_url}/context_payload/{payload_id}"
                    response = requests.post(
                        f"{base_url}/context",
                        json={
                            "endpoint": endpoint,
                            "units": params["units"],
                            "options": params.get("options", {}),
                        },
                        timeout=5,
                    )
                    response.raise_for_status()
                    data = response.json()
                    context_id = data["result"]["contextId"]

                elif action == "createContextFailed":
                    response = requests.post(
                        f"{base_url}/context",
                        json={
                            "failLoad": True,
                            "units": params["units"],
                            "options": params.get("options", {}),
                        },
                        timeout=5,
                    )
                    response.raise_for_status()
                    data = response.json()
                    context_id = data["result"]["contextId"]

                elif action == "refresh":
                    if payload_id and "newData" in params:
                        requests.put(
                            f"{base_url}/context_payload/{payload_id}",
                            json={"data": params["newData"]},
                            timeout=5,
                        ).raise_for_status()

                    response = requests.post(
                        f"{base_url}/context/{context_id}/refresh",
                        json={"newData": params.get("newData")} if "newData" in params else {},
                        timeout=5,
                    )
                    response.raise_for_status()
                    data = response.json()

                elif action == "waitForReady":
                    max_wait = params.get("timeout", 5000) / 1000.0
                    poll_interval = 0.1
                    elapsed = 0.0
                    ready = False

                    while elapsed < max_wait and not ready:
                        response = requests.get(f"{base_url}/context/{context_id}/isReady", timeout=5)
                        response.raise_for_status()
                        data = response.json()
                        ready = data.get("result", False)
                        if not ready:
                            time.sleep(poll_interval)
                            elapsed += poll_interval

                    data = {"result": ready, "events": []}

                elif action in ["pending", "isFinalized", "isReady", "isFailed", "experiments"]:
                    response = requests.get(f"{base_url}/context/{context_id}/{action}", timeout=5)
                    response.raise_for_status()
                    data = response.json()

                elif action == "diagnostic":
                    response = requests.post(f"{base_url}/diagnostic", json=params or {}, timeout=5)
                    response.raise_for_status()
                    data = response.json()

                else:
                    if params:
                        response = requests.post(
                            f"{base_url}/context/{context_id}/{action}", json=params, timeout=5
                        )
                    else:
                        response = requests.post(f"{base_url}/context/{context_id}/{action}", timeout=5)
                    response.raise_for_status()
                    data = response.json()

                step_failures = self.validate_step(step_index, action, data, expected)
                if step_failures:
                    failures.extend(step_failures)

            except requests.HTTPError as exc:
                error_msg = None
                if exc.response is not None:
                    try:
                        error_body = exc.response.json()
                        error_msg = error_body.get("error")
                    except Exception:
                        error_msg = exc.response.text
                if not error_msg:
                    error_msg = str(exc)

                if "errorContains" in expected:
                    if expected["errorContains"].lower() not in error_msg.lower():
                        failures.append(
                            {
                                "step": step_index,
                                "action": action,
                                "field": "errorContains",
                                "expected": expected["errorContains"],
                                "actual": error_msg,
                            }
                        )
                elif "error" in expected:
                    if not self.error_matches(error_msg, expected["error"]):
                        failures.append(
                            {
                                "step": step_index,
                                "action": action,
                                "field": "error",
                                "expected": expected["error"],
                                "actual": error_msg,
                            }
                        )
                else:
                    failures.append(
                        {
                            "step": step_index,
                            "action": action,
                            "error": f"Request failed: {error_msg}",
                        }
                    )

            except requests.RequestException as exc:
                failures.append(
                    {
                        "step": step_index,
                        "action": action,
                        "error": f"Request failed: {str(exc)}",
                    }
                )
            except Exception as exc:
                failures.append({"step": step_index, "action": action, "error": str(exc)})

        if context_id:
            try:
                requests.delete(f"{base_url}/context/{context_id}", timeout=5)
            except Exception:
                pass

        return {"passed": len(failures) == 0, "failures": failures}

    def validate_step(
        self,
        step_index: int,
        action: str,
        actual: Dict[str, Any],
        expected: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        failures: List[Dict[str, Any]] = []

        # validate_step only runs on the success path (raise_for_status did not
        # raise), so if the scenario expected an error, the call succeeding is
        # itself a failure. Without this, error scenarios pass vacuously whenever
        # the SDK returns HTTP 200 instead of the expected failure.
        if "errorContains" in expected or "error" in expected:
            expected_error = expected.get("errorContains", expected.get("error"))
            http_status = actual.get("status", 200) if isinstance(actual, dict) else 200
            failures.append(
                {
                    "step": step_index,
                    "action": action,
                    "field": "errorContains" if "errorContains" in expected else "error",
                    "expected": expected_error,
                    "actual": f"expected error but call succeeded (got HTTP {http_status})",
                }
            )
            return failures

        if "result" in expected:
            actual_result = actual.get("result")
            expected_result = expected["result"]
            if not self.values_match(actual_result, expected_result):
                failures.append(
                    {
                        "step": step_index,
                        "action": action,
                        "field": "result",
                        "expected": expected_result,
                        "actual": actual_result,
                    }
                )

        if "events" in expected:
            actual_events = actual.get("events", [])
            expected_events = expected["events"]

            if len(actual_events) != len(expected_events):
                failures.append(
                    {
                        "step": step_index,
                        "action": action,
                        "field": "events.length",
                        "expected": len(expected_events),
                        "actual": len(actual_events),
                    }
                )
            else:
                for i, (actual_event, expected_event) in enumerate(zip(actual_events, expected_events)):
                    if actual_event.get("type") != expected_event.get("type"):
                        failures.append(
                            {
                                "step": step_index,
                                "action": action,
                                "field": f"events[{i}].type",
                                "expected": expected_event.get("type"),
                                "actual": actual_event.get("type"),
                            }
                        )

                    if "data" in expected_event:
                        actual_data = actual_event.get("data", {})
                        expected_data = expected_event["data"]

                        for key, expected_value in expected_data.items():
                            actual_value = actual_data.get(key)
                            if not self.values_match(actual_value, expected_value):
                                failures.append(
                                    {
                                        "step": step_index,
                                        "action": action,
                                        "field": f"events[{i}].data.{key}",
                                        "expected": expected_value,
                                        "actual": actual_value,
                                    }
                                )

        return failures

    def values_match(self, actual: Any, expected: Any) -> bool:
        if isinstance(expected, dict) and isinstance(actual, dict):
            for key, expected_value in expected.items():
                if key not in actual:
                    return False
                if not self.values_match(actual[key], expected_value):
                    return False
            return True

        if isinstance(expected, list) and isinstance(actual, list):
            if len(expected) != len(actual):
                return False

            if all(isinstance(x, (str, int, float, bool, type(None))) for x in expected):
                # Order-insensitive comparison for scalar lists. A plain
                # sorted() raises TypeError on mixed types (e.g. [None, 'a']),
                # so use a total-ordering key that never compares values of
                # different types directly.
                def sort_key(x: Any) -> Tuple[bool, str, str]:
                    return (x is None, str(type(x)), str(x))

                return sorted(actual, key=sort_key) == sorted(expected, key=sort_key)

            return all(self.values_match(a, e) for a, e in zip(actual, expected))

        return actual == expected

    def error_matches(self, actual_error: str, expected_error: str) -> bool:
        import re

        def normalize(msg: str) -> str:
            msg = msg.lower().strip()
            msg = re.sub(r"[.\n\r]+$", "", msg)
            msg = re.sub(r"\s+", " ", msg)
            return msg

        actual_norm = normalize(actual_error or "")
        expected_norm = normalize(expected_error or "")

        if actual_norm == expected_norm:
            return True

        # Allow equivalent error wording where one message contains the other.
        if expected_norm and (expected_norm in actual_norm or actual_norm in expected_norm):
            return True

        expected_words = set(re.findall(r"'[^']+'|\w+", expected_norm))
        actual_words = set(re.findall(r"'[^']+'|\w+", actual_norm))
        filler = {"must", "be", "not", "the", "a", "an", "is", "of"}
        expected_key_words = expected_words - filler
        # Subset rule: the expected keywords all appear in the actual message.
        # A single shared *common* token is too weak (it matches unrelated
        # errors), so require at least 2 matching keywords. When the expected
        # message has fewer than 2 keywords, still accept it only if that lone
        # keyword is a quoted identifier (e.g. 'unitType') - a strong, specific
        # signal - rather than a bare common word.
        if expected_key_words and expected_key_words.issubset(actual_words):
            if len(expected_key_words) >= 2:
                return True
            lone_keyword = next(iter(expected_key_words))
            if lone_keyword.startswith("'") and lone_keyword.endswith("'"):
                return True

        if not self.loose_error_match:
            return False

        if len(expected_key_words) == 0:
            return True

        matches = expected_key_words & actual_words
        match_ratio = len(matches) / len(expected_key_words)
        return match_ratio >= 0.9

    def check_cross_sdk_consistency(self) -> None:
        """Compare each scenario's actual outcome across the SDKs that ran it.

        Baked expectations catch an SDK that disagrees with the spec, but not
        two SDKs that agree with each other while both drifting from a third.
        For every scenario executed (pass/fail, not skipped/errored) by 2+ SDKs,
        flag any SDK whose outcome differs from the majority. On a tie there is
        no majority, so every participating SDK is reported as disagreeing.
        """
        if len(self.sdks) < 2:
            return

        from collections import Counter

        for scenario_name, outcomes in self.scenario_outcomes.items():
            executed = {sdk: o for sdk, o in outcomes.items() if o in ("pass", "fail")}
            if len(executed) < 2:
                continue
            if len(set(executed.values())) <= 1:
                continue  # all participating SDKs agree

            counts = Counter(executed.values())
            max_count = counts.most_common(1)[0][1]
            majority = [outcome for outcome, c in counts.items() if c == max_count]
            if len(majority) == 1:
                majority_outcome = majority[0]
                disagreeing = [sdk for sdk, o in executed.items() if o != majority_outcome]
            else:
                majority_outcome = None  # tie: no majority, everyone disagrees
                disagreeing = list(executed.keys())

            self.consistency_report.append(
                {
                    "scenario": scenario_name,
                    "outcomes": dict(executed),
                    "majority": majority_outcome,
                    "disagreeing": disagreeing,
                }
            )
            for sdk in disagreeing:
                self.consistency_failures.setdefault(sdk, []).append(scenario_name)

    def generate_report(self, output_file: str, failed_sdks: List[str] = None) -> int:
        if failed_sdks is None:
            failed_sdks = []

        self.check_cross_sdk_consistency()

        total_tests = len(self.results)
        sdk_stats: Dict[str, Dict[str, Any]] = {}

        for sdk_name in self.sdks.keys():
            passed = 0
            failed = 0
            errors = 0
            skipped = 0

            for scenario_result in self.results:
                sdk_result = scenario_result["sdks"].get(sdk_name, {})
                if sdk_result.get("skipped"):
                    skipped += 1
                elif "error" in sdk_result:
                    errors += 1
                elif sdk_result.get("passed"):
                    passed += 1
                else:
                    failed += 1

            tested = total_tests - skipped
            consistency_failed = self.consistency_failures.get(sdk_name, [])
            capabilities_failed = sdk_name in self.capabilities_fetch_failed
            # An SDK that ran zero scenarios (everything skipped) while scenarios
            # exist has proven nothing - treat it as a failing "NO TESTS RUN"
            # state rather than a vacuous 100%.
            no_tests_run = tested == 0 and total_tests > 0

            sdk_stats[sdk_name] = {
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "skipped": skipped,
                "tested": tested,
                "total": total_tests,
                "pass_rate": (passed / tested * 100) if tested > 0 else 0.0,
                "consistency_failed": consistency_failed,
                "capabilities_failed": capabilities_failed,
                "capabilities_error": self.capabilities_fetch_failed.get(sdk_name),
                "no_tests_run": no_tests_run,
            }

        for sdk_name in failed_sdks:
            # A service that never started is a failed run, not a 0/0 pass.
            # Include tested/skipped so downstream pass checks (which compare
            # passed == tested) don't KeyError and don't read as vacuously green.
            sdk_stats[sdk_name] = {
                "passed": 0,
                "failed": 0,
                "errors": total_tests,
                "skipped": 0,
                "tested": total_tests,
                "total": total_tests,
                "pass_rate": 0.0,
                "consistency_failed": [],
                "capabilities_failed": False,
                "capabilities_error": None,
                "no_tests_run": False,
                "service_failed": True,
            }

        def sdk_run_failed(stats: Dict[str, Any]) -> bool:
            return bool(
                stats.get("service_failed")
                or stats.get("capabilities_failed")
                or stats.get("no_tests_run")
                or stats.get("consistency_failed")
                or stats["failed"] > 0
                or stats["errors"] > 0
            )

        report = {"total_scenarios": total_tests, "sdk_stats": sdk_stats, "results": self.results}
        if self.consistency_report:
            report["cross_sdk_consistency"] = self.consistency_report
        with open(output_file, "w") as out:
            json.dump(report, out, indent=2)

        print(f"\n{'=' * 60}")
        print(f"{'=' * 60}")
        print(f"{'TEST SUMMARY':^60}")
        print(f"{'=' * 60}")
        print(f"\nTotal Scenarios: {total_tests}\n")
        print("SDK Results:")
        print(f"{'-' * 60}")

        for sdk_name, stats in sdk_stats.items():
            if stats.get("service_failed"):
                status = f"{Colors.YELLOW}⚠ DOWN{Colors.RESET}"
                print(f"  {sdk_name:20} {status:8} (service failed to start)")
                continue

            if stats.get("capabilities_failed"):
                status = f"{Colors.RED}✗ CAPS FAIL{Colors.RESET}"
                print(
                    f"  {sdk_name:20} {status:8} "
                    f"(capabilities fetch failed: {stats['capabilities_error']}; "
                    f"{stats['skipped']} scenarios skipped as a result)"
                )
                continue

            if stats.get("no_tests_run"):
                status = f"{Colors.RED}✗ NO TESTS RUN{Colors.RESET}"
                print(f"  {sdk_name:20} {status:8} (0/{total_tests} tested, all {stats['skipped']} skipped)")
                continue

            if sdk_run_failed(stats):
                status = f"{Colors.RED}✗ FAIL{Colors.RESET}"
            else:
                status = f"{Colors.GREEN}✓ PASS{Colors.RESET}"

            skipped_info = f", {stats['skipped']} skipped" if stats["skipped"] > 0 else ""
            consistency_info = (
                f", {len(stats['consistency_failed'])} cross-SDK disagreement(s)"
                if stats["consistency_failed"]
                else ""
            )
            print(
                f"  {sdk_name:20} {status:8} "
                f"({stats['passed']}/{stats['tested']} tested{skipped_info}, "
                f"{stats['pass_rate']:.1f}%{consistency_info})"
            )

        print(f"{'=' * 60}")

        # Per-SDK skip counts with reasons, so the report distinguishes genuine
        # unsupported-capability skips from wrapper pass-through skips (and from
        # skips caused by a capabilities fetch failure, surfaced above).
        skip_reasons_by_sdk: Dict[str, Dict[str, int]] = {}
        skipped_scenarios: Dict[str, List[str]] = {}
        for scenario_result in self.results:
            scenario_name = scenario_result["name"]
            for sdk_name, sdk_result in scenario_result["sdks"].items():
                if sdk_result.get("skipped"):
                    skipped_scenarios.setdefault(scenario_name, []).append(sdk_name)
                    reason = sdk_result.get("reason", "Not supported")
                    skip_reasons_by_sdk.setdefault(sdk_name, {})
                    skip_reasons_by_sdk[sdk_name][reason] = (
                        skip_reasons_by_sdk[sdk_name].get(reason, 0) + 1
                    )

        if skip_reasons_by_sdk:
            print("\nSkip Counts by SDK:")
            print(f"{'-' * 60}")
            for sdk_name in sorted(skip_reasons_by_sdk):
                reasons = skip_reasons_by_sdk[sdk_name]
                total_skipped = sum(reasons.values())
                print(f"  {sdk_name}: {total_skipped} skipped")
                for reason, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
                    print(f"    - {count}x {reason}")

        if self.consistency_report:
            print("\nCross-SDK consistency:")
            print(f"{'-' * 60}")
            for entry in self.consistency_report:
                print(f"  {entry['scenario']}")
                outcome_str = ", ".join(
                    f"{sdk}={outcome}" for sdk, outcome in sorted(entry["outcomes"].items())
                )
                print(f"    outcomes: {outcome_str}")
                if entry["majority"] is None:
                    print(f"    {Colors.RED}no majority (tie){Colors.RESET} - all disagreeing")
                else:
                    print(
                        f"    majority={entry['majority']}, "
                        f"{Colors.RED}disagreeing: {', '.join(entry['disagreeing'])}{Colors.RESET}"
                    )
        elif len(self.sdks) > 1:
            print(f"\nCross-SDK consistency: {Colors.GREEN}all SDKs agree{Colors.RESET}")

        if skipped_scenarios:
            print("\nSkipped Scenarios:")
            print(f"{'-' * 60}")
            for scenario_name, sdks in skipped_scenarios.items():
                print(f"  {scenario_name}")
                print(f"    Skipped on: {', '.join(sdks)}")

        print(f"\nDetailed report: {output_file}\n")

        all_passed = not any(sdk_run_failed(stats) for stats in sdk_stats.values())
        return 0 if all_passed else 1


def discover_sdks() -> Dict[str, str]:
    sdk_services_env = os.getenv("SDK_SERVICES", "")
    if not sdk_services_env:
        print("Error: SDK_SERVICES environment variable is not set.")
        print("Set it to a comma-separated list of SDK names (e.g., javascript,python,ruby)")
        sys.exit(1)

    sdk_names = [s.strip() for s in sdk_services_env.split(",") if s.strip()]
    return {name: f"http://{name}-sdk:3000" for name in sdk_names}


def validate_and_filter_scenarios(all_scenarios: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    valid: List[Dict[str, Any]] = []
    excluded_empty: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []

    for idx, scenario in enumerate(all_scenarios):
        name = scenario.get("name", f"scenario-{idx + 1}")
        steps = scenario.get("steps")

        if not isinstance(steps, list):
            invalid.append({"index": idx + 1, "name": name, "reason": "steps must be an array"})
            continue

        if len(steps) == 0:
            excluded_empty.append({"index": idx + 1, "name": name, "reason": "no executable steps"})
            continue

        scenario_invalid = False
        normalized_steps = []
        for step_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                invalid.append(
                    {
                        "index": idx + 1,
                        "name": name,
                        "reason": f"step {step_idx} is not an object",
                    }
                )
                scenario_invalid = True
                break

            if "action" not in step:
                invalid.append(
                    {
                        "index": idx + 1,
                        "name": name,
                        "reason": f"step {step_idx} missing 'action'",
                    }
                )
                scenario_invalid = True
                break

            if "expect" not in step:
                invalid.append(
                    {
                        "index": idx + 1,
                        "name": name,
                        "reason": f"step {step_idx} missing 'expect'",
                    }
                )
                scenario_invalid = True
                break

            normalized_step = dict(step)
            normalized_step.setdefault("params", {})
            normalized_steps.append(normalized_step)

        if scenario_invalid:
            continue

        normalized_scenario = dict(scenario)
        normalized_scenario["steps"] = normalized_steps
        valid.append(normalized_scenario)

    return valid, excluded_empty, invalid


def main() -> None:
    all_sdks = discover_sdks()

    parser = argparse.ArgumentParser(description="Run cross-SDK tests")
    parser.add_argument("--sdk", type=str, help="Comma-separated list of SDKs to test (e.g., rust,go,javascript)")
    parser.add_argument(
        "--no-wrapper-skip",
        action="store_true",
        help="Disable skipping wrapper pass-through scenarios (by default, pass-through scenarios are skipped)",
    )
    parser.add_argument(
        "--loose-error-match",
        action="store_true",
        help="Use relaxed error message matching instead of strict normalized equality",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output with full test details")
    args = parser.parse_args()

    if args.sdk:
        sdk_names = [s.strip() for s in args.sdk.split(",")]
        sdks = {name: all_sdks[name] for name in sdk_names if name in all_sdks}
        invalid_sdks = [name for name in sdk_names if name not in all_sdks]
        if invalid_sdks:
            print(f"Warning: Unknown SDK(s): {', '.join(invalid_sdks)}")
        if not sdks:
            print(f"No valid SDKs specified. Available: {', '.join(all_sdks.keys())}")
            sys.exit(1)
    else:
        sdks = all_sdks

    scenarios_path = os.getenv("TEST_SCENARIOS_PATH", "/test_scenarios_complete.json")
    if not os.path.exists(scenarios_path):
        scenarios_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_scenarios_complete.json")

    with open(scenarios_path) as src:
        all_scenarios = json.load(src)

    scenarios, excluded_empty, invalid = validate_and_filter_scenarios(all_scenarios)

    # CREATE_WITH_SCENARIO is coupled to run_scenario by exact name. If it was
    # renamed in the scenario file, the createContextWith coverage silently
    # reroutes to live-fetch; warn loudly so the rename is caught.
    if not any(s.get("name") == CREATE_WITH_SCENARIO for s in all_scenarios):
        print(
            f"{Colors.YELLOW}WARNING{Colors.RESET}: scenario "
            f"{CREATE_WITH_SCENARIO!r} not found — the createContextWith "
            f"(pre-fetched data) path is now UNCOVERED; every context is built "
            f"via live-fetch. Update CREATE_WITH_SCENARIO in test_runner.py if "
            f"the scenario was renamed."
        )

    print(f"Loaded {len(all_scenarios)} total scenarios")
    print(f"Running {len(scenarios)} executable scenarios")
    if excluded_empty:
        print(f"Excluded {len(excluded_empty)} empty scenarios (no steps)")
    if invalid:
        print(f"Found {len(invalid)} invalid scenarios:")
        for entry in invalid[:10]:
            print(f"  - [{entry['index']}] {entry['name']}: {entry['reason']}")
        if len(invalid) > 10:
            print(f"  ... and {len(invalid) - 10} more")
        sys.exit(1)
    print()

    orchestrator = TestOrchestrator(
        sdks,
        verbose=args.verbose,
        allow_wrapper_skip=not args.no_wrapper_skip,
        loose_error_match=args.loose_error_match,
    )

    working_sdks, failed_sdks = orchestrator.wait_for_services()
    if not working_sdks:
        print("No SDK services available - all failed to start")
        sys.exit(1)

    orchestrator.sdks = working_sdks
    for scenario in scenarios:
        orchestrator.run_scenario(scenario)

    exit_code = orchestrator.generate_report("/results/report.json", failed_sdks)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
