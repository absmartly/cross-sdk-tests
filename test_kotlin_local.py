#!/usr/bin/env python3
"""Run cross-SDK tests against the Kotlin wrapper locally to identify failures."""
import json
import requests
import sys
import time
import uuid

BASE_URL = "http://localhost:3098"

def values_match(actual, expected):
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not values_match(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        if all(isinstance(x, (str, int, float, bool, type(None))) for x in expected):
            return sorted(actual) == sorted(expected)
        return all(values_match(a, e) for a, e in zip(actual, expected))
    return actual == expected

def error_matches(actual_error, expected_error):
    import re
    def normalize(msg):
        msg = msg.lower().strip()
        msg = re.sub(r'[.\n\r]+$', '', msg)
        msg = re.sub(r'\s+', ' ', msg)
        return msg
    actual_norm = normalize(actual_error)
    expected_norm = normalize(expected_error)
    if actual_norm == expected_norm:
        return True
    expected_words = set(re.findall(r"'[^']+'|\w+", expected_norm))
    actual_words = set(re.findall(r"'[^']+'|\w+", actual_norm))
    filler = {'must', 'be', 'not', 'the', 'a', 'an', 'is', 'of'}
    expected_key_words = expected_words - filler
    if len(expected_key_words) == 0:
        return True
    matches = expected_key_words & actual_words
    match_ratio = len(matches) / len(expected_key_words)
    return match_ratio >= 0.7

def run_scenario(scenario):
    context_id = None
    failures = []

    for step_index, step in enumerate(scenario['steps']):
        action = step['action']
        params = step['params']
        expected = step['expect']

        try:
            if action == 'createContext':
                create_with = params.get('options', {}).get('createContextWith', True)
                if create_with:
                    response = requests.post(f"{BASE_URL}/context", json={
                        'data': scenario['contextData'],
                        'units': params['units'],
                        'options': params.get('options', {})
                    }, timeout=5)
                else:
                    payload_id = f"payload-{uuid.uuid4()}"
                    requests.put(f"{BASE_URL}/context_payload/{payload_id}",
                                json={'data': scenario['contextData']}, timeout=5)
                    endpoint = f"{BASE_URL}/context_payload/{payload_id}"
                    response = requests.post(f"{BASE_URL}/context", json={
                        'endpoint': endpoint,
                        'units': params['units'],
                        'options': params.get('options', {})
                    }, timeout=5)
                response.raise_for_status()
                data = response.json()
                context_id = data['result']['contextId']
            elif action == 'refresh':
                response = requests.post(f"{BASE_URL}/context/{context_id}/refresh",
                                        json={'newData': params['newData']}, timeout=5)
                response.raise_for_status()
                data = response.json()
            elif action == 'waitForReady':
                max_wait = params.get('timeout', 5000) / 1000
                elapsed = 0
                ready = False
                while elapsed < max_wait and not ready:
                    response = requests.get(f"{BASE_URL}/context/{context_id}/isReady", timeout=5)
                    response.raise_for_status()
                    data = response.json()
                    ready = data.get('result', False)
                    if not ready:
                        time.sleep(0.1)
                        elapsed += 0.1
                data = {'result': ready, 'events': []}
            elif action in ['pending', 'isFinalized', 'isReady', 'isFailed', 'experiments']:
                response = requests.get(f"{BASE_URL}/context/{context_id}/{action}", timeout=5)
                response.raise_for_status()
                data = response.json()
            else:
                if params:
                    response = requests.post(f"{BASE_URL}/context/{context_id}/{action}",
                                            json=params, timeout=5)
                else:
                    response = requests.post(f"{BASE_URL}/context/{context_id}/{action}", timeout=5)
                response.raise_for_status()
                data = response.json()

            # Validate
            if 'result' in expected:
                actual_result = data.get('result')
                if not values_match(actual_result, expected['result']):
                    failures.append({
                        'step': step_index, 'action': action, 'field': 'result',
                        'expected': expected['result'], 'actual': actual_result
                    })

            if 'events' in expected:
                actual_events = data.get('events', [])
                expected_events = expected['events']
                if len(actual_events) != len(expected_events):
                    failures.append({
                        'step': step_index, 'action': action, 'field': 'events.length',
                        'expected': len(expected_events), 'actual': len(actual_events)
                    })
                else:
                    for i, (ae, ee) in enumerate(zip(actual_events, expected_events)):
                        if ae.get('type') != ee.get('type'):
                            failures.append({
                                'step': step_index, 'action': action,
                                'field': f'events[{i}].type',
                                'expected': ee.get('type'), 'actual': ae.get('type')
                            })
                        if 'data' in ee:
                            for key, ev in ee['data'].items():
                                av = ae.get('data', {}).get(key)
                                if not values_match(av, ev):
                                    failures.append({
                                        'step': step_index, 'action': action,
                                        'field': f'events[{i}].data.{key}',
                                        'expected': ev, 'actual': av
                                    })

        except requests.HTTPError as e:
            error_msg = None
            if e.response is not None:
                try:
                    error_body = e.response.json()
                    error_msg = error_body.get('error')
                except:
                    error_msg = e.response.text
            if not error_msg:
                error_msg = str(e)
            if 'error' in expected:
                if not error_matches(error_msg, expected['error']):
                    failures.append({
                        'step': step_index, 'action': action, 'field': 'error',
                        'expected': expected['error'], 'actual': error_msg
                    })
            else:
                failures.append({
                    'step': step_index, 'action': action, 'error': f"Request failed: {error_msg}"
                })
        except Exception as e:
            failures.append({'step': step_index, 'action': action, 'error': str(e)})

    if context_id:
        try:
            requests.delete(f"{BASE_URL}/context/{context_id}", timeout=5)
        except:
            pass

    return failures

def main():
    with open('/Users/joalves/git_tree/sdks/cross-sdk-tests/test_scenarios_complete.json') as f:
        all_scenarios = json.load(f)

    scenarios = [s for s in all_scenarios if 'steps' in s]

    passed = 0
    failed = 0
    failed_scenarios = []

    for scenario in scenarios:
        failures = run_scenario(scenario)
        if failures:
            failed += 1
            failed_scenarios.append((scenario['name'], failures))
            print(f"FAIL  {scenario['name']}")
            for f_item in failures:
                if 'error' in f_item:
                    print(f"      Step {f_item['step']} ({f_item['action']}): {f_item['error']}")
                else:
                    print(f"      Step {f_item['step']} ({f_item['action']}): {f_item['field']}")
                    print(f"        Expected: {f_item['expected']}")
                    print(f"        Actual:   {f_item['actual']}")
        else:
            passed += 1
            print(f"PASS  {scenario['name']}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {passed+failed} total")
    print(f"{'='*60}")

    if failed_scenarios:
        print(f"\nFailed scenarios ({len(failed_scenarios)}):")
        for name, failures in failed_scenarios:
            print(f"  - {name}")
            for f_item in failures[:2]:
                if 'error' in f_item:
                    print(f"    Step {f_item['step']}: {f_item['error']}")
                else:
                    print(f"    Step {f_item['step']}: {f_item['field']} expected={f_item['expected']} actual={f_item['actual']}")

    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
