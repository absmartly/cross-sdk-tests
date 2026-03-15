#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Tuple

import requests


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
        self.verbose = verbose
        self.allow_wrapper_skip = allow_wrapper_skip
        self.loose_error_match = loose_error_match

    def wait_for_services(self) -> Tuple[Dict[str, str], List[str]]:
        print("Waiting for SDK services to be ready...")
        working_sdks: Dict[str, str] = {}
        failed_sdks: List[str] = []

        for sdk_name, base_url in self.sdks.items():
            max_retries = 30
            service_ready = False

            for i in range(max_retries):
                try:
                    response = requests.get(f"{base_url}/health", timeout=1)
                    if response.status_code == 200:
                        print(f"  {Colors.GREEN}✓{Colors.RESET} {sdk_name} ready")
                        working_sdks[sdk_name] = base_url
                        service_ready = True

                        try:
                            caps_response = requests.get(f"{base_url}/capabilities", timeout=1)
                            if caps_response.status_code == 200:
                                self.capabilities[sdk_name] = caps_response.json()
                            else:
                                self.capabilities[sdk_name] = {"attrsSeq": False}
                        except Exception:
                            self.capabilities[sdk_name] = {"attrsSeq": False}

                        break
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
                    reason = result.get("reason", "Not supported")
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.YELLOW}⊘ SKIP{Colors.RESET} ({reason})")
                    else:
                        print(f"{Colors.YELLOW}SKIP{Colors.RESET}  {scenario['name']}: {reason}")
                elif result["passed"]:
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.GREEN}✓ PASS{Colors.RESET}")
                    else:
                        print(f"{Colors.GREEN}PASS{Colors.RESET}  {scenario['name']}")
                else:
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
                if self.verbose:
                    print(f"  {sdk_name:20} {Colors.RED}✗ ERROR{Colors.RESET}: {exc}")
                else:
                    print(f"{Colors.RED}FAIL{Colors.RESET}  {scenario['name']}: {exc}")
                scenario_results["sdks"][sdk_name] = {"passed": False, "error": str(exc)}

        self.results.append(scenario_results)
        return scenario_results

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
                return {
                    "passed": True,
                    "skipped": True,
                    "reason": f"SDK does not support: {', '.join(missing_caps)}",
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
                if action == "createContextWith":
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

                elif action == "createContext":
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
                return sorted(actual) == sorted(expected)

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
        if expected_key_words and expected_key_words.issubset(actual_words):
            return True

        if not self.loose_error_match:
            return False

        if len(expected_key_words) == 0:
            return True

        matches = expected_key_words & actual_words
        match_ratio = len(matches) / len(expected_key_words)
        return match_ratio >= 0.9

    def generate_report(self, output_file: str, failed_sdks: List[str] = None) -> int:
        if failed_sdks is None:
            failed_sdks = []

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
            sdk_stats[sdk_name] = {
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "skipped": skipped,
                "tested": tested,
                "total": total_tests,
                "pass_rate": (passed / tested * 100) if tested > 0 else 100.0,
            }

        for sdk_name in failed_sdks:
            sdk_stats[sdk_name] = {
                "passed": 0,
                "failed": 0,
                "errors": total_tests,
                "total": total_tests,
                "pass_rate": 0.0,
                "service_failed": True,
            }

        report = {"total_scenarios": total_tests, "sdk_stats": sdk_stats, "results": self.results}
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

            if stats["failed"] == 0 and stats["errors"] == 0:
                status = f"{Colors.GREEN}✓ PASS{Colors.RESET}"
            else:
                status = f"{Colors.RED}✗ FAIL{Colors.RESET}"

            skipped_info = f", {stats['skipped']} skipped" if stats["skipped"] > 0 else ""
            print(
                f"  {sdk_name:20} {status:8} "
                f"({stats['passed']}/{stats['tested']} tested{skipped_info}, {stats['pass_rate']:.1f}%)"
            )

        print(f"{'=' * 60}")

        skipped_scenarios: Dict[str, List[str]] = {}
        for scenario_result in self.results:
            scenario_name = scenario_result["name"]
            for sdk_name, sdk_result in scenario_result["sdks"].items():
                if sdk_result.get("skipped"):
                    skipped_scenarios.setdefault(scenario_name, []).append(sdk_name)

        if skipped_scenarios:
            print("\nSkipped Scenarios:")
            print(f"{'-' * 60}")
            for scenario_name, sdks in skipped_scenarios.items():
                print(f"  {scenario_name}")
                print(f"    Skipped on: {', '.join(sdks)}")

        print(f"\nDetailed report: {output_file}\n")

        all_passed = all(stats["passed"] == stats["tested"] for stats in sdk_stats.values())
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
