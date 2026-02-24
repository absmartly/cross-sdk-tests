#!/usr/bin/env python3
import json
import requests
import sys
import time
import os
from typing import Dict, List, Any

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

class TestOrchestrator:
    def __init__(self, sdks: Dict[str, str], verbose: bool = False):
        self.sdks = sdks
        self.results = []
        self.capabilities = {}
        self.verbose = verbose

    def wait_for_services(self):
        """Wait for all SDK services to be healthy, return working and failed SDKs"""
        print("Waiting for SDK services to be ready...")
        working_sdks = {}
        failed_sdks = []

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

                        # Query capabilities
                        try:
                            caps_response = requests.get(f"{base_url}/capabilities", timeout=1)
                            if caps_response.status_code == 200:
                                self.capabilities[sdk_name] = caps_response.json()
                            else:
                                # Default capabilities if endpoint doesn't exist
                                self.capabilities[sdk_name] = {"asyncContext": False, "attrsSeq": False}
                        except:
                            self.capabilities[sdk_name] = {"asyncContext": False, "attrsSeq": False}

                        break
                except:
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

    def run_scenario(self, scenario: Dict) -> Dict:
        if self.verbose:
            print(f"\n{'='*60}")
            print(f"=== {scenario['name']}")
            print(f"{'='*60}")
            if scenario.get('description'):
                print(f"{scenario['description']}\n")

        scenario_results = {
            'name': scenario['name'],
            'description': scenario.get('description', ''),
            'sdks': {}
        }

        for sdk_name, base_url in self.sdks.items():
            try:
                result = self.run_and_validate_sdk(
                    sdk_name,
                    base_url,
                    scenario
                )
                scenario_results['sdks'][sdk_name] = result

                if result.get('skipped'):
                    status = "SKIP"
                    reason = result.get('reason', 'Not supported')
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.YELLOW}⊘ SKIP{Colors.RESET} ({reason})")
                    else:
                        print(f"{Colors.YELLOW}SKIP{Colors.RESET}  {scenario['name']}: {reason}")
                elif result['passed']:
                    status = "PASS"
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.GREEN}✓ PASS{Colors.RESET}")
                    else:
                        print(f"{Colors.GREEN}PASS{Colors.RESET}  {scenario['name']}")
                else:
                    status = "FAIL"
                    failure_count = len(result['failures'])
                    if self.verbose:
                        print(f"  {sdk_name:20} {Colors.RED}✗ FAIL{Colors.RESET} ({failure_count} failures)")
                        for failure in result['failures'][:3]:
                            print(f"      {failure}")
                    else:
                        first_failure = result['failures'][0] if result['failures'] else {}
                        error_msg = first_failure.get('error', first_failure.get('actual', ''))
                        print(f"{Colors.RED}FAIL{Colors.RESET}  {scenario['name']}: {error_msg}")
            except Exception as e:
                if self.verbose:
                    print(f"  {sdk_name:20} {Colors.RED}✗ ERROR{Colors.RESET}: {e}")
                else:
                    print(f"{Colors.RED}FAIL{Colors.RESET}  {scenario['name']}: {e}")
                scenario_results['sdks'][sdk_name] = {
                    'passed': False,
                    'error': str(e)
                }

        self.results.append(scenario_results)
        return scenario_results

    def should_skip_wrapper_test(self, sdk_name: str, scenario: Dict) -> tuple:
        """
        Check if a wrapper should skip a test because its underlying SDK is
        already being tested and all operations are pass-through.
        Returns (should_skip, reason) tuple.
        """
        sdk_caps = self.capabilities.get(sdk_name, {})

        if not sdk_caps.get('isWrapper'):
            return False, None

        underlying_sdk = sdk_caps.get('wrapsSDK')
        if not underlying_sdk or underlying_sdk not in self.sdks:
            return False, None

        pass_through_ops = set(sdk_caps.get('passThroughOperations', []))
        if not pass_through_ops:
            return False, None

        scenario_actions = set()
        for step in scenario.get('steps', []):
            action = step.get('action')
            if action and action != 'createContext':
                scenario_actions.add(action)

        if not scenario_actions:
            return False, None

        if scenario_actions.issubset(pass_through_ops):
            return True, f"Pass-through to {underlying_sdk} SDK"

        return False, None

    def run_and_validate_sdk(
        self,
        sdk_name: str,
        base_url: str,
        scenario: Dict
    ) -> Dict:
        # Check if wrapper should skip because underlying SDK is being tested
        should_skip, skip_reason = self.should_skip_wrapper_test(sdk_name, scenario)
        if should_skip:
            return {
                'passed': True,
                'skipped': True,
                'reason': skip_reason
            }

        # Check if SDK has required capabilities
        if 'requires' in scenario:
            sdk_caps = self.capabilities.get(sdk_name, {})
            missing_caps = [cap for cap in scenario['requires'] if not sdk_caps.get(cap, False)]
            if missing_caps:
                return {
                    'passed': True,
                    'skipped': True,
                    'reason': f"SDK does not support: {', '.join(missing_caps)}"
                }

        context_id = None
        failures = []

        for step_index, step in enumerate(scenario['steps']):
            action = step['action']
            params = step['params']
            expected = step['expect']

            try:
                # Special case: createContext doesn't have a context_id yet
                if action == 'createContext':
                    create_with = params.get('options', {}).get('createContextWith', True)

                    if create_with:
                        # Sync: Use existing flow (createContextWith)
                        response = requests.post(
                            f"{base_url}/context",
                            json={
                                'data': scenario['contextData'],
                                'units': params['units'],
                                'options': params.get('options', {})
                            },
                            timeout=5
                        )
                    else:
                        # Async: Store payload first, then SDK fetches from endpoint/context
                        import uuid
                        payload_id = f"payload-{uuid.uuid4()}"
                        payload_response = requests.put(
                            f"{base_url}/context_payload/{payload_id}",
                            json={'data': scenario['contextData']},
                            timeout=5
                        )
                        payload_response.raise_for_status()

                        # Create async context - SDK will call GET {endpoint}/context
                        endpoint = f"{base_url}/context_payload/{payload_id}"
                        response = requests.post(
                            f"{base_url}/context",
                            json={
                                'endpoint': endpoint,
                                'units': params['units'],
                                'options': params.get('options', {})
                            },
                            timeout=5
                        )

                    response.raise_for_status()
                    data = response.json()
                    context_id = data['result']['contextId']

                # Special case: refresh has custom parameter wrapping
                elif action == 'refresh':
                    response = requests.post(
                        f"{base_url}/context/{context_id}/refresh",
                        json={'newData': params['newData']},
                        timeout=5
                    )
                    response.raise_for_status()
                    data = response.json()

                # Special case: waitForReady polls until context is ready
                elif action == 'waitForReady':
                    max_wait = params.get('timeout', 5000) / 1000  # Convert ms to seconds
                    poll_interval = 0.1
                    elapsed = 0
                    ready = False

                    while elapsed < max_wait and not ready:
                        response = requests.get(
                            f"{base_url}/context/{context_id}/isReady",
                            timeout=5
                        )
                        response.raise_for_status()
                        data = response.json()
                        ready = data.get('result', False)
                        if not ready:
                            time.sleep(poll_interval)
                            elapsed += poll_interval

                    # Return final ready state
                    data = {'result': ready, 'events': []}

                # Actions that use GET method
                elif action in ['pending', 'isFinalized', 'isReady', 'isFailed', 'experiments']:
                    response = requests.get(
                        f"{base_url}/context/{context_id}/{action}",
                        timeout=5
                    )
                    response.raise_for_status()
                    data = response.json()

                # All other actions use POST and pass action name directly as endpoint
                else:
                    # Determine if params should be sent
                    if params:
                        response = requests.post(
                            f"{base_url}/context/{context_id}/{action}",
                            json=params,
                            timeout=5
                        )
                    else:
                        response = requests.post(
                            f"{base_url}/context/{context_id}/{action}",
                            timeout=5
                        )
                    response.raise_for_status()
                    data = response.json()

                step_failures = self.validate_step(
                    step_index,
                    action,
                    data,
                    expected
                )

                if step_failures:
                    failures.extend(step_failures)

            except requests.HTTPError as e:
                # HTTP error occurred (400, 500, etc.)
                error_msg = None

                # Extract error message from response body
                if e.response is not None:
                    try:
                        error_body = e.response.json()
                        error_msg = error_body.get('error')
                    except:
                        error_msg = e.response.text

                if not error_msg:
                    error_msg = str(e)

                # Check if test EXPECTS an error
                if 'error' in expected:
                    # This is an error-testing scenario
                    if self.error_matches(error_msg, expected['error']):
                        # Error message matches - PASS (don't add to failures)
                        pass
                    else:
                        # Wrong error message - FAIL
                        failures.append({
                            'step': step_index,
                            'action': action,
                            'field': 'error',
                            'expected': expected['error'],
                            'actual': error_msg
                        })
                else:
                    # Unexpected error - FAIL
                    failures.append({
                        'step': step_index,
                        'action': action,
                        'error': f"Request failed: {error_msg}"
                    })

            except requests.RequestException as e:
                failures.append({
                    'step': step_index,
                    'action': action,
                    'error': f"Request failed: {str(e)}"
                })
            except Exception as e:
                failures.append({
                    'step': step_index,
                    'action': action,
                    'error': str(e)
                })

        if context_id:
            try:
                requests.delete(
                    f"{base_url}/context/{context_id}",
                    timeout=5
                )
            except:
                pass

        return {
            'passed': len(failures) == 0,
            'failures': failures
        }

    def validate_step(
        self,
        step_index: int,
        action: str,
        actual: Dict,
        expected: Dict
    ) -> List[Dict]:
        failures = []

        if 'result' in expected:
            actual_result = actual.get('result')
            expected_result = expected['result']

            if not self.values_match(actual_result, expected_result):
                failures.append({
                    'step': step_index,
                    'action': action,
                    'field': 'result',
                    'expected': expected_result,
                    'actual': actual_result
                })

        if 'events' in expected:
            actual_events = actual.get('events', [])
            expected_events = expected['events']

            if len(actual_events) != len(expected_events):
                failures.append({
                    'step': step_index,
                    'action': action,
                    'field': 'events.length',
                    'expected': len(expected_events),
                    'actual': len(actual_events)
                })
            else:
                for i, (actual_event, expected_event) in enumerate(
                    zip(actual_events, expected_events)
                ):
                    if actual_event.get('type') != expected_event.get('type'):
                        failures.append({
                            'step': step_index,
                            'action': action,
                            'field': f'events[{i}].type',
                            'expected': expected_event.get('type'),
                            'actual': actual_event.get('type')
                        })

                    if 'data' in expected_event:
                        actual_data = actual_event.get('data', {})
                        expected_data = expected_event['data']

                        for key, expected_value in expected_data.items():
                            actual_value = actual_data.get(key)

                            if not self.values_match(actual_value, expected_value):
                                failures.append({
                                    'step': step_index,
                                    'action': action,
                                    'field': f'events[{i}].data.{key}',
                                    'expected': expected_value,
                                    'actual': actual_value
                                })

        return failures

    def values_match(self, actual, expected) -> bool:
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

            # Sort both arrays if they contain only primitives (order-independent)
            if all(isinstance(x, (str, int, float, bool, type(None))) for x in expected):
                return sorted(actual) == sorted(expected)

            # For complex objects, maintain order
            return all(
                self.values_match(a, e)
                for a, e in zip(actual, expected)
            )

        return actual == expected

    def error_matches(self, actual_error: str, expected_error: str) -> bool:
        """
        Flexible error message matching to accommodate SDK variations.
        Normalizes both messages and checks if core content matches.
        """
        import re

        def normalize(msg: str) -> str:
            # Convert to lowercase, remove extra whitespace, strip punctuation
            msg = msg.lower().strip()
            msg = re.sub(r'[.\n\r]+$', '', msg)  # Remove trailing periods/newlines
            msg = re.sub(r'\s+', ' ', msg)  # Normalize whitespace
            return msg

        actual_norm = normalize(actual_error)
        expected_norm = normalize(expected_error)

        # Exact match after normalization
        if actual_norm == expected_norm:
            return True

        # Check if actual contains all key words from expected (order-independent)
        # Extract key words (alphanumeric + quoted strings)
        expected_words = set(re.findall(r"'[^']+'|\w+", expected_norm))
        actual_words = set(re.findall(r"'[^']+'|\w+", actual_norm))

        # Remove common filler words
        filler = {'must', 'be', 'not', 'the', 'a', 'an', 'is', 'of'}
        expected_key_words = expected_words - filler

        # Actual should contain most of the key words (at least 70%)
        if len(expected_key_words) == 0:
            return True

        matches = expected_key_words & actual_words
        match_ratio = len(matches) / len(expected_key_words)

        return match_ratio >= 0.7

    def generate_report(self, output_file: str, failed_sdks: List[str] = None):
        if failed_sdks is None:
            failed_sdks = []

        total_tests = len(self.results)
        sdk_stats = {}

        for sdk_name in self.sdks.keys():
            passed = 0
            failed = 0
            errors = 0
            skipped = 0

            for scenario_result in self.results:
                sdk_result = scenario_result['sdks'].get(sdk_name, {})
                if sdk_result.get('skipped'):
                    skipped += 1
                elif 'error' in sdk_result:
                    errors += 1
                elif sdk_result.get('passed'):
                    passed += 1
                else:
                    failed += 1

            tested = total_tests - skipped
            sdk_stats[sdk_name] = {
                'passed': passed,
                'failed': failed,
                'errors': errors,
                'skipped': skipped,
                'tested': tested,
                'total': total_tests,
                'pass_rate': (passed / tested * 100) if tested > 0 else 100.0
            }

        # Add failed SDKs to stats with 0 tests passed
        for sdk_name in failed_sdks:
            sdk_stats[sdk_name] = {
                'passed': 0,
                'failed': 0,
                'errors': total_tests,
                'total': total_tests,
                'pass_rate': 0.0,
                'service_failed': True
            }

        report = {
            'total_scenarios': total_tests,
            'sdk_stats': sdk_stats,
            'results': self.results
        }

        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"\n{'='*60}")
        print(f"{'='*60}")
        print(f"{'TEST SUMMARY':^60}")
        print(f"{'='*60}")
        print(f"\nTotal Scenarios: {total_tests}\n")
        print(f"SDK Results:")
        print(f"{'-'*60}")

        for sdk_name, stats in sdk_stats.items():
            if stats.get('service_failed'):
                status = f"{Colors.YELLOW}⚠ DOWN{Colors.RESET}"
                print(f"  {sdk_name:20} {status:8} "
                      f"(service failed to start)")
            else:
                if stats['failed'] == 0 and stats['errors'] == 0:
                    status = f"{Colors.GREEN}✓ PASS{Colors.RESET}"
                else:
                    status = f"{Colors.RED}✗ FAIL{Colors.RESET}"
                skipped_info = f", {stats['skipped']} skipped" if stats['skipped'] > 0 else ""
                print(f"  {sdk_name:20} {status:8} "
                      f"({stats['passed']}/{stats['tested']} tested{skipped_info}, "
                      f"{stats['pass_rate']:.1f}%)")

        print(f"{'='*60}")

        # Print skipped scenarios summary
        skipped_scenarios = {}
        for scenario_result in self.results:
            scenario_name = scenario_result['name']
            for sdk_name, sdk_result in scenario_result['sdks'].items():
                if sdk_result.get('skipped'):
                    if scenario_name not in skipped_scenarios:
                        skipped_scenarios[scenario_name] = []
                    skipped_scenarios[scenario_name].append(sdk_name)

        if skipped_scenarios:
            print(f"\nSkipped Scenarios (Not Bugs):")
            print(f"{'-'*60}")
            for scenario_name, sdks in skipped_scenarios.items():
                print(f"  {scenario_name}")
                print(f"    Skipped on: {', '.join(sdks)}")

        print(f"\nDetailed report: {output_file}\n")

        all_passed = all(
            stats['passed'] == stats['tested']
            for stats in sdk_stats.values()
        )
        return 0 if all_passed else 1

def discover_sdks() -> Dict[str, str]:
    sdk_services_env = os.getenv('SDK_SERVICES', '')
    if not sdk_services_env:
        print("Error: SDK_SERVICES environment variable is not set.")
        print("Set it to a comma-separated list of SDK names (e.g., javascript,python,ruby)")
        sys.exit(1)

    sdk_names = [s.strip() for s in sdk_services_env.split(',') if s.strip()]
    return {name: f'http://{name}-sdk:3000' for name in sdk_names}


def main():
    import argparse

    all_sdks = discover_sdks()

    parser = argparse.ArgumentParser(description='Run cross-SDK tests')
    parser.add_argument('--sdk', type=str, help='Comma-separated list of SDKs to test (e.g., rust,go,javascript)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show verbose output with full test details')
    args = parser.parse_args()

    if args.sdk:
        sdk_names = [s.strip() for s in args.sdk.split(',')]
        sdks = {name: all_sdks[name] for name in sdk_names if name in all_sdks}
        invalid = [name for name in sdk_names if name not in all_sdks]
        if invalid:
            print(f"Warning: Unknown SDK(s): {', '.join(invalid)}")
        if not sdks:
            print(f"No valid SDKs specified. Available: {', '.join(all_sdks.keys())}")
            sys.exit(1)
    else:
        sdks = all_sdks

    scenarios_path = os.getenv('TEST_SCENARIOS_PATH', '/test_scenarios_complete.json')
    if not os.path.exists(scenarios_path):
        scenarios_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'test_scenarios_complete.json')

    with open(scenarios_path) as f:
        all_scenarios = json.load(f)

    # Filter to only scenarios that have 'steps' (testable via API)
    scenarios = [s for s in all_scenarios if 'steps' in s]

    print(f"Loaded {len(all_scenarios)} total scenarios")
    print(f"Running {len(scenarios)} testable scenarios (excluding unit tests)\n")

    orchestrator = TestOrchestrator(sdks, verbose=args.verbose)

    working_sdks, failed_sdks = orchestrator.wait_for_services()

    if not working_sdks:
        print("No SDK services available - all failed to start")
        sys.exit(1)

    # Update orchestrator to use only working SDKs for testing
    orchestrator.sdks = working_sdks

    for scenario in scenarios:
        orchestrator.run_scenario(scenario)

    exit_code = orchestrator.generate_report('/results/report.json', failed_sdks)
    sys.exit(exit_code)

if __name__ == '__main__':
    main()
