#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import requests


# The one scenario that exercises the literal createContextWith API (pre-fetched
# data construction). Coupled by exact name: run_scenario routes only this
# scenario through the createContextWith path and every other createContext(With)
# through live fetch. If this scenario is renamed in test_scenarios_complete.json
# without updating this constant, that coverage silently reroutes to live-fetch,
# so main() prints a loud warning at startup when the name is absent.
CREATE_WITH_SCENARIO = "01 - Context Creation - Ready with Data"

# Context-free pure-function actions (capability `switchback`), routed to fixed wrapper paths.
SWITCHBACK_ACTIONS = {
    "switchbackAssign": "/switchback/assign",
    "switchbackPermutation": "/switchback/permutation",
    "switchbackBucket": "/switchback/bucket",
}

# Checked-in capability policy: which `requires:` capabilities each SDK must
# advertise, and which are reviewed exceptions (see the file's _comment).
DEFAULT_POLICY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "expected-capabilities.json"
)

# Result lists whose order is not part of the contract. Every other list is
# compared positionally (a scenario can still force ordering with
# orderedResult: true, which also overrides this allowlist).
UNORDERED_RESULT_ACTIONS = frozenset(
    {"variableKeys", "customFieldKeys", "globalCustomFieldKeys", "experiments"}
)
# Actions whose result is a map of key -> list, where each list is unordered.
UNORDERED_MAP_VALUE_ACTIONS = frozenset({"variableKeysMap"})

# Actions whose object result is a status record the scenario asserts only in
# part (contextId and the readyError message are SDK-specific). Every other
# object result is data and must match the expected key set exactly.
PARTIAL_RESULT_ACTIONS = frozenset(
    {"createContext", "createContextWith", "createContextFailed", "readyError"}
)

# Exposure fields every wrapper reports (scenarios assert all of them in 90+
# steps), compared across SDKs even where a scenario only asserts the event type.
EXPOSURE_CONSISTENCY_KEYS = (
    "id",
    "name",
    "unit",
    "variant",
    "assigned",
    "eligible",
    "overridden",
    "fullOn",
    "custom",
    "audienceMismatch",
)

# Publish attributes the SDK adds about itself; their values differ per SDK.
SYSTEM_ATTRIBUTE_NAMES = frozenset({"sdk_name", "sdk_version", "application", "environment", "app_version"})

# Keys of a /capabilities response that are not capability flags but are read
# by the harness, with the JSON type each must have when present.
CAPABILITY_METADATA_TYPES = {
    "isWrapper": bool,
    "wrapsSDK": str,
    "passThroughOperations": list,
}

CONTEXT_CREATE_ACTIONS = ("createContext", "createContextWith", "createContextFailed")


class _Missing:
    """Sentinel for a JSON key that is absent (distinct from an explicit null)."""

    def __repr__(self) -> str:
        return "<missing>"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


def describe(value: Any) -> Any:
    """Make a value safe for the JSON report (MISSING is not serializable)."""
    if value is MISSING:
        return "<missing>"
    return value


class ConfigError(Exception):
    """A scenario file or capability policy that cannot be used as-is."""


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Capability policy
# ---------------------------------------------------------------------------


def validate_capability_policy(policy: Any) -> List[str]:
    """Return a list of problems with a capability policy (empty if valid)."""
    errors: List[str] = []
    if not isinstance(policy, dict):
        return ["policy must be a JSON object"]

    capabilities = policy.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        errors.append("'capabilities' must be a non-empty object of name -> description")
        capabilities = {}
    for name, desc in capabilities.items():
        if not isinstance(desc, str) or not desc.strip():
            errors.append(f"capability {name!r} needs a non-empty description")

    sdks = policy.get("sdks")
    if not isinstance(sdks, dict) or not sdks:
        errors.append("'sdks' must be a non-empty object of sdk -> {required, unsupported}")
        return errors

    known = set(capabilities)
    for sdk, entry in sdks.items():
        if not isinstance(entry, dict):
            errors.append(f"sdk {sdk!r}: entry must be an object")
            continue
        required = entry.get("required", [])
        unsupported = entry.get("unsupported", {})
        if not isinstance(required, list) or not all(isinstance(c, str) for c in required):
            errors.append(f"sdk {sdk!r}: 'required' must be a list of capability names")
            required = []
        if not isinstance(unsupported, dict):
            errors.append(f"sdk {sdk!r}: 'unsupported' must be an object of capability -> reason")
            unsupported = {}
        if len(set(required)) != len(required):
            errors.append(f"sdk {sdk!r}: 'required' lists a capability twice")
        for cap in required:
            if cap not in known:
                errors.append(f"sdk {sdk!r}: required capability {cap!r} is not declared in 'capabilities'")
        for cap, reason in unsupported.items():
            if cap not in known:
                errors.append(f"sdk {sdk!r}: unsupported capability {cap!r} is not declared in 'capabilities'")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"sdk {sdk!r}: unsupported capability {cap!r} needs a one-line reason")
        overlap = set(required) & set(unsupported)
        if overlap:
            errors.append(
                f"sdk {sdk!r}: {sorted(overlap)} listed as both required and unsupported"
            )
        unclassified = known - set(required) - set(unsupported)
        if unclassified:
            errors.append(
                f"sdk {sdk!r}: capabilities {sorted(unclassified)} are neither required nor a "
                f"reviewed unsupported exception"
            )
    return errors


def load_capability_policy(path: str = DEFAULT_POLICY_PATH) -> Dict[str, Any]:
    try:
        with open(path) as src:
            policy = json.load(src)
    except FileNotFoundError:
        raise ConfigError(f"capability policy not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"capability policy {path} is not valid JSON: {exc}")
    errors = validate_capability_policy(policy)
    if errors:
        raise ConfigError(
            f"capability policy {path} is invalid:\n  - " + "\n  - ".join(errors)
        )
    return policy


def validate_capabilities_response(payload: Any, known_capabilities) -> Optional[str]:
    """Return an error string if a /capabilities body is malformed, else None.

    Only capabilities the policy declares (and the metadata keys the harness
    reads) are type-checked; unrelated extra keys a wrapper reports are ignored.
    """
    if not isinstance(payload, dict):
        return f"/capabilities must return a JSON object, got {type(payload).__name__}"
    for cap in sorted(known_capabilities):
        if cap in payload and not isinstance(payload[cap], bool):
            return f"capability {cap!r} must be a JSON boolean, got {payload[cap]!r}"
    for key, expected_type in CAPABILITY_METADATA_TYPES.items():
        if key in payload and not isinstance(payload[key], expected_type):
            return f"{key!r} must be a JSON {expected_type.__name__}, got {payload[key]!r}"
    ops = payload.get("passThroughOperations")
    if isinstance(ops, list) and not all(isinstance(op, str) for op in ops):
        return "'passThroughOperations' must be a list of strings"
    return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _scalar_key(value: Any) -> Tuple[str, Any]:
    # bool must stay distinct from int/float (Python treats True == 1), but
    # int and float of equal value compare equal (JSON has one number type).
    if value is None:
        return ("null", "")
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    return ("other", json.dumps(value, sort_keys=True))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def values_match(actual: Any, expected: Any, unordered: bool = False, exact_objects: bool = False) -> bool:
    """Compare an actual JSON value to an expected one.

    * Objects are matched as a SUBSET by default: every expected key must be
      present in the actual object (absent never matches, not even an expected
      null) and match recursively; extra actual keys are allowed. Event data
      and context-creation results are deliberately partial (dynamic
      timestamps, ids and SDK-specific fields are not asserted).
      With `exact_objects`, every object (at any depth) must have exactly the
      expected key set: used for results that ARE data (getUnits,
      getAttributes, variableKeysMap, JSON custom field values), where an
      extra key is a wrong answer.
    * Arrays must have the same length. They are compared positionally unless
      `unordered` is set and every element is a scalar, in which case they are
      compared as multisets (duplicates still count).
    * Scalars must have the same JSON type (bool is never a number, null is
      never a string) and value; 1 and 1.0 are equal.
    """
    if actual is MISSING:
        return False

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        if exact_objects and set(actual) != set(expected):
            return False
        return all(
            key in actual and values_match(actual[key], expected_value, exact_objects=exact_objects)
            for key, expected_value in expected.items()
        )

    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        if unordered and all(_is_scalar(x) for x in expected) and all(_is_scalar(x) for x in actual):
            return Counter(_scalar_key(x) for x in actual) == Counter(_scalar_key(x) for x in expected)
        return all(values_match(a, e, exact_objects=exact_objects) for a, e in zip(actual, expected))

    if not _is_scalar(actual):
        return False
    return _scalar_key(actual) == _scalar_key(expected)


def result_matches(action: str, actual: Any, expected: Any, ordered: bool = False) -> bool:
    exact = action not in PARTIAL_RESULT_ACTIONS
    if not ordered and action in UNORDERED_RESULT_ACTIONS:
        return values_match(actual, expected, unordered=True, exact_objects=exact)
    if not ordered and action in UNORDERED_MAP_VALUE_ACTIONS and isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(
            values_match(actual[key], value, unordered=True, exact_objects=exact)
            for key, value in expected.items()
        )
    return values_match(actual, expected, exact_objects=exact)


def _canonical(value: Any) -> Any:
    """Normalize a JSON value for cross-SDK comparison (1.0 -> 1, MISSING marker)."""
    if value is MISSING:
        return "<missing>"
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return repr(value)


def _sorted_list(values: List[Any]) -> List[Any]:
    return sorted(values, key=lambda v: json.dumps(v, sort_keys=True))


def _project(actual: Any, expected: Any) -> Any:
    """Keep only the parts of `actual` the scenario asserts (recursively for objects)."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {k: _project(actual.get(k, MISSING), v) for k, v in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [_project(a, e) for a, e in zip(actual, expected)]
    return actual


def _exposure_observation(data: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _canonical(data.get(k, MISSING)) for k in EXPOSURE_CONSISTENCY_KEYS}


def _context_data_observation(data: Dict[str, Any]) -> Any:
    """ready/refresh payload: which experiments (id, name) the SDK loaded.

    Only id and name are compared: the rest of each experiment is the SDK's own
    deserialized model, whose shape legitimately differs per language.
    """
    experiments = data.get("experiments", MISSING)
    if not isinstance(experiments, list):
        return {"experiments": _canonical(experiments)}
    return {
        "experiments": [
            {"id": _canonical(e.get("id", MISSING)), "name": _canonical(e.get("name", MISSING))}
            if isinstance(e, dict)
            else {"invalid": _canonical(e)}
            for e in experiments
        ]
    }


def _publish_observation(data: Dict[str, Any]) -> Dict[str, Any]:
    """publish payload minus what is volatile or SDK-identifying.

    Left out: publishedAt/exposedAt/achievedAt/setAt (clock), sdkVersion, and
    the system attributes (sdk_name, sdk_version, application, environment,
    app_version) whose values name the SDK. An omitted goals/exposures/
    attributes array is the same as an empty one (JS omits empty arrays).
    """

    def items(key: str) -> List[Any]:
        value = data.get(key)
        if value is None:
            return []
        return value if isinstance(value, list) else [{"invalid": _canonical(value)}]

    units = [
        {"type": _canonical(u.get("type", MISSING)), "uid": _canonical(u.get("uid", MISSING))}
        if isinstance(u, dict)
        else {"invalid": _canonical(u)}
        for u in items("units")
    ]
    return {
        "hashed": _canonical(data.get("hashed", MISSING)),
        "units": _sorted_list(units),
        "exposures": [_exposure_observation(x) if isinstance(x, dict) else {"invalid": _canonical(x)} for x in items("exposures")],
        "goals": [
            {"name": _canonical(g.get("name", MISSING)), "properties": _canonical(g.get("properties"))}
            if isinstance(g, dict)
            else {"invalid": _canonical(g)}
            for g in items("goals")
        ],
        "attributes": [
            {"name": _canonical(a.get("name", MISSING)), "value": _canonical(a.get("value", MISSING))}
            if isinstance(a, dict)
            else {"invalid": _canonical(a)}
            for a in items("attributes")
            if not (isinstance(a, dict) and a.get("name") in SYSTEM_ATTRIBUTE_NAMES)
        ],
    }


def observation_for_step(action: str, actual: Any, expected: Dict[str, Any], ordered: bool = False) -> str:
    """Normalized, comparable view of what an SDK returned for one step.

    Volatile fields are left out by construction: partial (status) results are
    projected onto the keys the scenario asserts (so contextId, readyError
    messages and similar SDK-specific detail never participate), and event
    timestamps are not included. Exposure events contribute the canonical
    exposure fields, ready/refresh the loaded experiments, and publish the
    request payload without clock values or SDK identification.
    """
    if not isinstance(actual, dict):
        return json.dumps({"response": _canonical(actual)}, sort_keys=True)

    result = actual.get("result", MISSING)
    expected_result = expected.get("result", MISSING)
    if isinstance(result, dict) and action in PARTIAL_RESULT_ACTIONS:
        if isinstance(expected_result, dict):
            result = _project(result, expected_result)
        else:
            result = {k: v for k, v in result.items() if k != "contextId"}
    result = _canonical(result)
    if not ordered and isinstance(result, list) and action in UNORDERED_RESULT_ACTIONS:
        result = _sorted_list(result)
    if not ordered and isinstance(result, dict) and action in UNORDERED_MAP_VALUE_ACTIONS:
        result = {k: _sorted_list(v) if isinstance(v, list) else v for k, v in result.items()}

    events_obs: Any = "<missing>"
    events = actual.get("events", MISSING)
    if isinstance(events, list):
        events_obs = []
        for event in events:
            if not isinstance(event, dict):
                events_obs.append({"invalid": _canonical(event)})
                continue
            entry: Dict[str, Any] = {"type": _canonical(event.get("type", MISSING))}
            data = event.get("data")
            if entry["type"] == "exposure" and isinstance(data, dict):
                entry["data"] = _exposure_observation(data)
            elif entry["type"] == "goal" and isinstance(data, dict):
                entry["data"] = {"name": _canonical(data.get("name", MISSING))}
            elif entry["type"] in ("ready", "refresh") and isinstance(data, dict):
                entry["data"] = _context_data_observation(data)
            elif entry["type"] == "publish" and isinstance(data, dict):
                entry["data"] = _publish_observation(data)
            events_obs.append(entry)

    return json.dumps({"result": result, "events": events_obs}, sort_keys=True)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestOrchestrator:
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        sdks: Dict[str, str],
        verbose: bool = False,
        allow_wrapper_skip: bool = False,
        loose_error_match: bool = False,
        capability_policy: Optional[Dict[str, Any]] = None,
    ):
        self.sdks = sdks
        self.results: List[Dict[str, Any]] = []
        self.capabilities: Dict[str, Dict[str, Any]] = {}
        # SDKs whose /capabilities endpoint errored (unreachable, non-200 or a
        # malformed body). Maps sdk_name -> reason. These are distinct from an
        # SDK that answered /capabilities but simply lacks a capability.
        self.capabilities_fetch_failed: Dict[str, str] = {}
        # sdk_name -> list of human-readable capability policy violations.
        self.capability_policy_violations: Dict[str, List[str]] = {}
        # Per-scenario actual outcome ("pass"/"fail"/"skip"/"error") per SDK,
        # used for the cross-SDK consistency pass after all SDKs have run.
        self.scenario_outcomes: Dict[str, Dict[str, str]] = {}
        # scenario name -> sdk name -> per-step normalized observations.
        self.step_observations: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.consistency_failures: Dict[str, List[str]] = {}
        self.consistency_report: List[Dict[str, Any]] = []
        self.consistency_comparisons = 0
        self.verbose = verbose
        # Pass-through skipping (a wrapper declaring an operation as a straight
        # pass-through to another SDK under test) is OFF by default: a skipped
        # scenario proves nothing about the wrapper. It is an explicit opt-in
        # for fast local runs only.
        self.allow_wrapper_skip = allow_wrapper_skip
        self.loose_error_match = loose_error_match
        self.policy = capability_policy if capability_policy is not None else load_capability_policy()
        policy_errors = validate_capability_policy(self.policy)
        if policy_errors:
            raise ConfigError("capability policy is invalid:\n  - " + "\n  - ".join(policy_errors))
        self.known_capabilities = set(self.policy["capabilities"])

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
            # Generous timeout: some wrappers compute capabilities lazily from a
            # behavioral self-test on the first request.
            caps_response = requests.get(f"{base_url}/capabilities", timeout=15)
            if caps_response.status_code == 200:
                try:
                    payload = caps_response.json()
                except ValueError:
                    payload = None
                    reason = "invalid /capabilities response: body is not JSON"
                else:
                    reason = validate_capabilities_response(payload, self.known_capabilities)
                    if reason is None:
                        self.set_capabilities(sdk_name, payload)
                        return
                    reason = f"invalid /capabilities response: {reason}"
            else:
                reason = f"HTTP {caps_response.status_code}"
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__

        # The endpoint itself errored (unreachable, non-200 or malformed)
        # rather than reporting a missing capability. Record it loudly: the
        # report must not attribute those skips to an unsupported feature, and
        # this SDK's run is marked failed.
        self.capabilities[sdk_name] = {}
        self.capabilities_fetch_failed[sdk_name] = reason
        print(
            f"  {Colors.RED}⚠ CAPABILITIES FETCH FAILED{Colors.RESET} {sdk_name}: {reason} - "
            f"all `requires:`-gated scenarios will be skipped and this SDK's run marked FAILED"
        )

    def set_capabilities(self, sdk_name: str, payload: Dict[str, Any]) -> None:
        """Record a validated /capabilities body and check it against the policy."""
        self.capabilities[sdk_name] = payload
        violations = self.policy_violations_for(sdk_name, payload)
        if violations:
            self.capability_policy_violations[sdk_name] = violations
            for violation in violations:
                print(f"  {Colors.RED}⚠ CAPABILITY POLICY{Colors.RESET} {sdk_name}: {violation}")

    def policy_violations_for(self, sdk_name: str, caps: Dict[str, Any]) -> List[str]:
        entry = self.policy["sdks"].get(sdk_name)
        if entry is None:
            return [
                f"no entry in the capability policy for {sdk_name!r}; add one listing its "
                f"required capabilities and reviewed exceptions"
            ]
        violations: List[str] = []
        for cap in entry.get("required", []):
            if caps.get(cap) is not True:
                got = "missing" if cap not in caps else json.dumps(caps[cap])
                violations.append(
                    f"required capability {cap!r} is not advertised (got {got}); its scenarios "
                    f"run anyway and are expected to fail"
                )
        for cap, reason in entry.get("unsupported", {}).items():
            if caps.get(cap) is True:
                violations.append(
                    f"advertises {cap!r} but the policy lists it as a reviewed exception "
                    f"({reason}); move it to 'required' in expected-capabilities.json"
                )
        return violations

    def capability_gate(self, sdk_name: str, scenario: Dict[str, Any]) -> Optional[str]:
        """Return a skip reason if a `requires:` scenario must not run, else None."""
        required = scenario.get("requires") or []
        if not required:
            return None
        if sdk_name in self.capabilities_fetch_failed:
            return (
                f"capabilities fetch failed ({self.capabilities_fetch_failed[sdk_name]}); "
                f"cannot confirm support for: {', '.join(required)}"
            )
        entry = self.policy["sdks"].get(sdk_name)
        if entry is None:
            return f"no capability policy entry for {sdk_name}; cannot classify: {', '.join(required)}"

        caps = self.capabilities.get(sdk_name, {})
        excepted: List[str] = []
        for cap in required:
            if cap in entry.get("required", []):
                continue  # must run: a missing capability is a policy violation, not a skip
            reason = entry.get("unsupported", {}).get(cap)
            if reason is None:
                return f"capability {cap!r} is not classified for {sdk_name} in the policy"
            if caps.get(cap) is True:
                continue  # advertised despite the exception: run it (and flag the policy)
            excepted.append(f"{cap} ({reason})")
        if excepted:
            return "reviewed exception: " + "; ".join(excepted)
        return None

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
        if sdk_caps.get("isWrapper") is not True:
            return False, None

        underlying_sdk = sdk_caps.get("wrapsSDK")
        if not underlying_sdk or underlying_sdk not in self.sdks:
            return False, None

        pass_through_ops = set(sdk_caps.get("passThroughOperations") or [])
        if not pass_through_ops:
            return False, None

        scenario_actions = set()
        for step in scenario.get("steps", []):
            action = step.get("action")
            if action and action not in CONTEXT_CREATE_ACTIONS:
                scenario_actions.add(action)

        if not scenario_actions:
            return False, None

        if scenario_actions.issubset(pass_through_ops):
            return True, f"Pass-through to {underlying_sdk} SDK (opt-in --wrapper-skip)"

        return False, None

    def run_and_validate_sdk(self, sdk_name: str, base_url: str, scenario: Dict[str, Any]) -> Dict[str, Any]:
        # Capability eligibility first: a scenario the SDK is not eligible for
        # is skipped for that reason, never attributed to a pass-through.
        gate_reason = self.capability_gate(sdk_name, scenario)
        if gate_reason:
            return {"passed": True, "skipped": True, "reason": gate_reason}

        should_skip, skip_reason = self.should_skip_wrapper_test(sdk_name, scenario)
        if should_skip:
            return {"passed": True, "skipped": True, "passthrough": True, "reason": skip_reason}

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
        # Successful step responses by index, for `sameShapeAs` references.
        responses: List[Any] = [None] * len(steps)
        pure_session: Optional[requests.Session] = None
        observations = self.step_observations.setdefault(scenario.get("name", ""), {}).setdefault(sdk_name, {})
        observations.clear()

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
                        ready = data.get("result", False) is True
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

                elif action in SWITCHBACK_ACTIONS:
                    # One keep-alive session for a scenario's pure-function steps (up to 294 calls).
                    if pure_session is None:
                        pure_session = requests.Session()
                    response = pure_session.post(f"{base_url}{SWITCHBACK_ACTIONS[action]}", json=params, timeout=5)
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

                step_failures = self.validate_step(step_index, action, data, expected, responses)
                if step_failures:
                    failures.extend(step_failures)
                responses[step_index] = data
                observations[step_index] = (
                    action,
                    observation_for_step(action, data, expected, ordered=expected.get("orderedResult", False)),
                )

            except requests.HTTPError as exc:
                error_msg = None
                error_body = None
                if exc.response is not None:
                    try:
                        error_body = exc.response.json()
                        error_msg = error_body.get("error") if isinstance(error_body, dict) else None
                        if error_msg is not None and not isinstance(error_msg, str):
                            error_msg = json.dumps(error_msg)
                    except Exception:
                        error_msg = exc.response.text
                if not error_msg:
                    error_msg = str(exc)

                if isinstance(error_body, dict):
                    observations[step_index] = (
                        action,
                        observation_for_step(
                            action, {"result": "<error>", "events": error_body.get("events", MISSING)}, expected
                        ),
                    )
                    failures.extend(
                        self.validate_events(
                            step_index,
                            action,
                            error_body.get("events", MISSING),
                            expected,
                            context="events on error response: ",
                        )
                    )
                else:
                    failures.append(
                        {
                            "step": step_index,
                            "action": action,
                            "field": "events",
                            "expected": describe(expected.get("events", [])),
                            "actual": "error response body is not a JSON object",
                        }
                    )

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
                elif "errorAnyOf" in expected:
                    # The same failure may be worded differently by two released SDK
                    # versions (e.g. 1.6.3 "... is closed" vs. "... is finalized"); the call
                    # passes if the actual message matches any listed alternative.
                    alternatives = expected["errorAnyOf"]
                    if not any(self.error_matches(error_msg, alt) for alt in alternatives):
                        failures.append(
                            {
                                "step": step_index,
                                "action": action,
                                "field": "errorAnyOf",
                                "expected": " | ".join(alternatives),
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

        if pure_session is not None:
            pure_session.close()

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
        actual: Any,
        expected: Dict[str, Any],
        prior_responses: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        failures: List[Dict[str, Any]] = []

        def fail(field: str, expected_value: Any, actual_value: Any) -> None:
            failures.append(
                {
                    "step": step_index,
                    "action": action,
                    "field": field,
                    "expected": describe(expected_value),
                    "actual": describe(actual_value),
                }
            )

        # validate_step only runs on the success path (raise_for_status did not
        # raise), so if the scenario expected an error, the call succeeding is
        # itself a failure. Without this, error scenarios pass vacuously whenever
        # the SDK returns HTTP 200 instead of the expected failure.
        if "errorContains" in expected or "error" in expected or "errorAnyOf" in expected:
            if "errorAnyOf" in expected:
                expected_error = " | ".join(expected["errorAnyOf"])
            else:
                expected_error = expected.get("errorContains", expected.get("error"))
            http_status = actual.get("status", 200) if isinstance(actual, dict) else 200
            fail(
                "errorContains"
                if "errorContains" in expected
                else ("errorAnyOf" if "errorAnyOf" in expected else "error"),
                expected_error,
                f"expected error but call succeeded (got HTTP {http_status})",
            )
            return failures

        if not isinstance(actual, dict):
            failures.append(
                {
                    "step": step_index,
                    "action": action,
                    "error": f"response body must be a JSON object, got {type(actual).__name__}",
                }
            )
            return failures

        if "result" in expected:
            # An omitted `result` member is NOT the same as `result: null`: a
            # wrapper that drops the key is violating the wire contract.
            actual_result = actual.get("result", MISSING)
            expected_result = expected["result"]
            if not result_matches(
                action, actual_result, expected_result, ordered=expected.get("orderedResult", False)
            ):
                fail("result", expected_result, actual_result)

        actual_events = actual.get("events", MISSING)
        failures.extend(
            self.validate_events(step_index, action, actual_events, expected, prior_responses=prior_responses)
        )
        return failures

    def validate_events(
        self,
        step_index: int,
        action: str,
        actual_events: Any,
        expected: Dict[str, Any],
        context: str = "",
        prior_responses: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Check a response's events window against the step's `events`."""
        failures: List[Dict[str, Any]] = []
        prior_responses = prior_responses or []

        def fail(field: str, expected_value: Any, actual_value: Any) -> None:
            failures.append(
                {
                    "step": step_index,
                    "action": action,
                    "field": field,
                    "expected": describe(expected_value),
                    "actual": f"{context}{describe(actual_value)}" if context else describe(actual_value),
                }
            )

        if actual_events is MISSING:
            # The wire contract requires an `events` array on every response.
            fail("events", expected.get("events", []), MISSING)
            return failures
        if "events" not in expected:
            # A step that does not list events expects none. Any emitted event
            # is unexpected and must not pass silently.
            if actual_events != []:
                if not isinstance(actual_events, list):
                    fail("events", [], actual_events)
                else:
                    types = [e.get("type") if isinstance(e, dict) else e for e in actual_events]
                    fail("events", "no events (step lists no 'events')", f"unexpected events: {types}")
            return failures

        expected_events = expected["events"]
        if not isinstance(actual_events, list):
            fail("events", expected_events, actual_events)
            return failures

        if len(actual_events) != len(expected_events):
            failures.append(
                {
                    "step": step_index,
                    "action": action,
                    "field": "events.length",
                    "expected": len(expected_events),
                    "actual": len(actual_events),
                    "actual_types": [e.get("type") if isinstance(e, dict) else e for e in actual_events],
                }
            )
            return failures

        for i, (actual_event, expected_event) in enumerate(zip(actual_events, expected_events)):
            if not isinstance(actual_event, dict):
                fail(f"events[{i}]", expected_event, actual_event)
                continue

            actual_type = actual_event.get("type", MISSING)
            if not values_match(actual_type, expected_event.get("type")):
                fail(f"events[{i}].type", expected_event.get("type"), actual_type)

            if "sameShapeAs" in expected_event:
                reference, error = self.resolve_reference(
                    prior_responses, expected_event["sameShapeAs"], step_index, i
                )
                if error is not None:
                    fail(f"events[{i}].sameShapeAs", "a usable baseline event", error)
                elif (mismatch := self.shape_mismatch(actual_event.get("data", MISSING), reference, "data")) is not None:
                    fail(
                        f"events[{i}].{mismatch[0]} (sameShapeAs step {expected_event['sameShapeAs']})",
                        mismatch[1],
                        mismatch[2],
                    )

            if "data" in expected_event:
                actual_data = actual_event.get("data", MISSING)
                expected_data = expected_event["data"]
                if isinstance(expected_data, dict) and isinstance(actual_data, dict):
                    for key, expected_value in expected_data.items():
                        actual_value = actual_data.get(key, MISSING)
                        # A goal's null `properties` may be omitted: the Java SDK's
                        # GoalAchievement is @JsonInclude(NON_NULL), so omission is
                        # that SDK's real wire output. Every other event member is
                        # strict: an omitted member never matches an expected null.
                        if (
                            expected_value is None
                            and actual_value is MISSING
                            and expected_event.get("type") == "goal"
                            and key == "properties"
                        ):
                            continue
                        if not values_match(actual_value, expected_value):
                            fail(f"events[{i}].data.{key}", expected_value, actual_value)
                elif not values_match(actual_data, expected_data):
                    fail(f"events[{i}].data", expected_data, actual_data)

        return failures

    @staticmethod
    def resolve_reference(
        prior_responses: List[Any], reference: Any, step_index: int, event_index: int
    ) -> Tuple[Any, Optional[str]]:
        """Resolve `sameShapeAs: <step>` to the data of the same-index event of an earlier step.
        Returns (data, None), or (None, reason) when there is no usable baseline: the reference
        is not an integer in [0, step_index), that step has no response, or its event or data is
        missing or null. Empty objects and arrays are valid baselines."""
        if isinstance(reference, bool) or not isinstance(reference, int):
            return None, f"reference must be an integer step index, got {reference!r}"
        if not 0 <= reference < step_index:
            return None, f"reference step {reference} is not an earlier step of step {step_index}"
        response = prior_responses[reference] if reference < len(prior_responses) else None
        if not isinstance(response, dict):
            return None, f"reference step {reference} has no response"
        events = response.get("events")
        if not isinstance(events, list) or event_index >= len(events) or not isinstance(events[event_index], dict):
            return None, f"reference step {reference} has no event {event_index}"
        data = events[event_index].get("data")
        if data is None:
            return None, f"reference step {reference} event {event_index} has no data"
        return data, None

    def shape_mismatch(self, actual: Any, reference: Any, path: str) -> Optional[Tuple[str, Any, Any]]:
        """`sameShapeAs: <step>` compares an event's data with the same-index event the SDK
        emitted at an earlier step: object key sets must be equal at every depth and arrays must
        have equal length, element by element. Values are not compared (`data` does that), so a
        payload is pinned to the SDK's own baseline shape and any added or dropped field fails
        without hardcoding SDK-specific envelope fields. Returns (path, expected, actual) of the
        first difference, or None."""
        if isinstance(reference, dict):
            if not isinstance(actual, dict):
                return path, "object", type(actual).__name__
            if set(actual) != set(reference):
                return f"{path} keys", sorted(reference), sorted(actual)
            for key in reference:
                mismatch = self.shape_mismatch(actual[key], reference[key], f"{path}.{key}")
                if mismatch is not None:
                    return mismatch
            return None

        if isinstance(reference, list):
            if not isinstance(actual, list) or len(actual) != len(reference):
                return f"{path} length", len(reference), len(actual) if isinstance(actual, list) else actual
            for index, (a, r) in enumerate(zip(actual, reference)):
                mismatch = self.shape_mismatch(a, r, f"{path}[{index}]")
                if mismatch is not None:
                    return mismatch
            return None

        if isinstance(actual, (dict, list)):
            return path, type(reference).__name__, type(actual).__name__
        return None

    def values_match(self, actual: Any, expected: Any, ordered: bool = False) -> bool:
        """Backwards-compatible method form of the module-level values_match.

        Keeps the historical signature (ordered=False compares top-level scalar
        lists as multisets); validate_step itself uses result_matches, which
        only relaxes ordering for the documented unordered actions.
        """
        return values_match(actual, expected, unordered=not ordered)

    def error_matches(self, actual_error: str, expected_error: str) -> bool:
        def normalize(msg: str) -> str:
            msg = msg.lower().strip()
            msg = re.sub(r"[.\n\r]+$", "", msg)
            msg = re.sub(r"\s+", " ", msg)
            return msg

        actual_norm = normalize(actual_error or "")
        expected_norm = normalize(expected_error or "")

        if actual_norm == expected_norm:
            return True

        # Negation changes the meaning ("not finalized" vs "finalized"), so it
        # is never filler: both messages must agree on whether they negate.
        negation = re.compile(r"\b(?:not|no|cannot|never|unable|without)\b|n't\b")
        if bool(negation.search(expected_norm)) != bool(negation.search(actual_norm)):
            return False

        # Allow a verbose SDK error that CONTAINS the expected phrase. Only the
        # forward direction is safe: a short actual message that merely happens
        # to be a substring of a specific expected phrase must NOT pass (it would
        # bypass the keyword-subset rule below), so reverse containment is out.
        if expected_norm and expected_norm in actual_norm:
            return True

        expected_words = set(re.findall(r"'[^']+'|\w+", expected_norm))
        actual_words = set(re.findall(r"'[^']+'|\w+", actual_norm))
        filler = {"must", "be", "the", "a", "an", "is", "of"}
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

    @staticmethod
    def _majority(values: Dict[str, Any]) -> Tuple[Any, List[str]]:
        counts = Counter(values.values())
        max_count = counts.most_common(1)[0][1]
        majority = [value for value, c in counts.items() if c == max_count]
        if len(majority) == 1:
            return majority[0], [sdk for sdk, v in values.items() if v != majority[0]]
        return None, list(values.keys())  # tie: no majority, everyone disagrees

    def check_cross_sdk_consistency(self) -> None:
        """Compare each scenario's actual outcome across the SDKs that ran it.

        Baked expectations catch an SDK that disagrees with the spec, but not
        two SDKs that agree with each other while both drifting from a third.
        For every scenario executed (pass/fail, not skipped/errored) by 2+ SDKs,
        flag any SDK whose outcome differs from the majority. On a tie there is
        no majority, so every participating SDK is reported as disagreeing.

        Also compare normalized step observations, excluding volatile and
        SDK-specific fields as defined by observation_for_step.
        """
        self.consistency_report = []
        self.consistency_failures = {}
        self.consistency_comparisons = 0
        if len(self.sdks) < 2:
            return

        for scenario_name, outcomes in self.scenario_outcomes.items():
            executed = {sdk: o for sdk, o in outcomes.items() if o in ("pass", "fail")}
            if len(executed) < 2:
                continue
            self.consistency_comparisons += 1
            if len(set(executed.values())) > 1:
                majority_outcome, disagreeing = self._majority(executed)
                self.consistency_report.append(
                    {
                        "kind": "outcome",
                        "scenario": scenario_name,
                        "outcomes": dict(executed),
                        "majority": majority_outcome,
                        "disagreeing": disagreeing,
                    }
                )
                for sdk in disagreeing:
                    self.consistency_failures.setdefault(sdk, []).append(scenario_name)

            per_sdk = self.step_observations.get(scenario_name, {})
            step_indexes = sorted({i for sdk in executed for i in per_sdk.get(sdk, {})})
            for step_index in step_indexes:
                observed = {
                    sdk: per_sdk[sdk][step_index][1]
                    for sdk in executed
                    if step_index in per_sdk.get(sdk, {})
                }
                if len(observed) < 2:
                    continue
                self.consistency_comparisons += 1
                if len(set(observed.values())) <= 1:
                    continue
                action = next(per_sdk[sdk][step_index][0] for sdk in observed)
                majority_obs, disagreeing = self._majority(observed)
                self.consistency_report.append(
                    {
                        "kind": "observation",
                        "scenario": scenario_name,
                        "step": step_index,
                        "action": action,
                        "observations": {sdk: json.loads(obs) for sdk, obs in observed.items()},
                        "majority": json.loads(majority_obs) if majority_obs is not None else None,
                        "disagreeing": disagreeing,
                    }
                )
                label = f"{scenario_name} [step {step_index} {action}]"
                for sdk in disagreeing:
                    if label not in self.consistency_failures.get(sdk, []):
                        self.consistency_failures.setdefault(sdk, []).append(label)

    def generate_report(self, output_file: str, failed_sdks: List[str] = None) -> int:
        if failed_sdks is None:
            failed_sdks = []

        self.check_cross_sdk_consistency()
        # With 2+ SDKs the consistency check is part of the verdict; if it made
        # zero comparisons (e.g. every scenario skipped on all but one SDK) the
        # cross-SDK guarantee was never checked, which must not read as "agree".
        consistency_unchecked = len(self.sdks) >= 2 and self.consistency_comparisons == 0

        total_tests = len(self.results)
        sdk_stats: Dict[str, Dict[str, Any]] = {}

        for sdk_name in self.sdks.keys():
            passed = 0
            failed = 0
            errors = 0
            skipped = 0
            passthrough_skipped = 0

            for scenario_result in self.results:
                sdk_result = scenario_result["sdks"].get(sdk_name, {})
                if sdk_result.get("skipped"):
                    skipped += 1
                    if sdk_result.get("passthrough"):
                        passthrough_skipped += 1
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
                "passthrough_skipped": passthrough_skipped,
                "tested": tested,
                "total": total_tests,
                "pass_rate": (passed / tested * 100) if tested > 0 else 0.0,
                "consistency_failed": consistency_failed,
                "consistency_unchecked": consistency_unchecked,
                "capabilities_failed": capabilities_failed,
                "capabilities_error": self.capabilities_fetch_failed.get(sdk_name),
                "capability_policy_violations": list(self.capability_policy_violations.get(sdk_name, [])),
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
                "passthrough_skipped": 0,
                "tested": total_tests,
                "total": total_tests,
                "pass_rate": 0.0,
                "consistency_failed": [],
                "consistency_unchecked": False,
                "capabilities_failed": False,
                "capabilities_error": None,
                "capability_policy_violations": [],
                "no_tests_run": False,
                "service_failed": True,
            }

        def sdk_run_failed(stats: Dict[str, Any]) -> bool:
            return bool(
                stats.get("service_failed")
                or stats.get("capabilities_failed")
                or stats.get("capability_policy_violations")
                or stats.get("no_tests_run")
                or stats.get("consistency_failed")
                or stats.get("consistency_unchecked")
                or stats["failed"] > 0
                or stats["errors"] > 0
            )

        report = {
            "total_scenarios": total_tests,
            "wrapper_skip_enabled": self.allow_wrapper_skip,
            "consistency_comparisons": self.consistency_comparisons,
            "sdk_stats": sdk_stats,
            "results": self.results,
            "scenario_outcomes": self.scenario_outcomes,
            "step_observations": {
                name: {sdk: {str(i): list(obs) for i, obs in steps.items()} for sdk, steps in per_sdk.items()}
                for name, per_sdk in self.step_observations.items()
            },
        }
        if self.consistency_report:
            report["cross_sdk_consistency"] = self.consistency_report
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_file, "w") as out:
            json.dump(report, out, indent=2, default=describe)

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
            policy_info = (
                f", {len(stats['capability_policy_violations'])} capability policy violation(s)"
                if stats["capability_policy_violations"]
                else ""
            )
            print(
                f"  {sdk_name:20} {status:8} "
                f"({stats['passed']}/{stats['tested']} tested{skipped_info}, "
                f"{stats['pass_rate']:.1f}%{consistency_info}{policy_info})"
            )

        print(f"{'=' * 60}")

        if self.capability_policy_violations:
            print("\nCapability policy violations:")
            print(f"{'-' * 60}")
            for sdk_name in sorted(self.capability_policy_violations):
                for violation in self.capability_policy_violations[sdk_name]:
                    print(f"  {sdk_name}: {violation}")

        # Per-SDK skip counts with reasons, so the report distinguishes reviewed
        # capability exceptions from opt-in wrapper pass-through skips (and from
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

        if self.allow_wrapper_skip:
            print(
                f"\n{Colors.YELLOW}NOTE{Colors.RESET}: wrapper pass-through skipping was enabled "
                f"(--wrapper-skip); pass-through scenarios were not run against the wrappers."
            )

        if self.consistency_report:
            print("\nCross-SDK consistency:")
            print(f"{'-' * 60}")
            for entry in self.consistency_report:
                if entry.get("kind") == "observation":
                    print(f"  {entry['scenario']} [step {entry['step']} {entry['action']}]")
                    for sdk, obs in sorted(entry["observations"].items()):
                        print(f"    {sdk}: {json.dumps(obs, sort_keys=True)}")
                else:
                    print(f"  {entry['scenario']}")
                    outcome_str = ", ".join(
                        f"{sdk}={outcome}" for sdk, outcome in sorted(entry["outcomes"].items())
                    )
                    print(f"    outcomes: {outcome_str}")
                if entry["majority"] is None:
                    print(f"    {Colors.RED}no majority (tie){Colors.RESET} - all disagreeing")
                else:
                    print(f"    {Colors.RED}disagreeing: {', '.join(entry['disagreeing'])}{Colors.RESET}")
        elif consistency_unchecked:
            print(
                f"\nCross-SDK consistency: {Colors.RED}NOT CHECKED{Colors.RESET} "
                f"(0 comparisons across {len(self.sdks)} SDKs - no scenario ran on 2+ SDKs)"
            )
        elif len(self.sdks) > 1:
            print(
                f"\nCross-SDK consistency: {Colors.GREEN}all SDKs agree{Colors.RESET} "
                f"({self.consistency_comparisons} comparisons)"
            )

        if skipped_scenarios:
            print("\nSkipped Scenarios:")
            print(f"{'-' * 60}")
            for scenario_name, sdks in skipped_scenarios.items():
                print(f"  {scenario_name}")
                print(f"    Skipped on: {', '.join(sdks)}")

        print(f"\nDetailed report: {output_file}\n")

        all_passed = not any(sdk_run_failed(stats) for stats in sdk_stats.values())
        return 0 if all_passed else 1


# ---------------------------------------------------------------------------
# Scenario loading (shared by main() and run-tests.sh)
# ---------------------------------------------------------------------------


def discover_sdks() -> Dict[str, str]:
    # SDK_URLS_OVERRIDE ("name=url,name=url") points the runner at explicit
    # URLs, e.g. published localhost ports for run-tests.sh --sdk runs.
    override = os.getenv("SDK_URLS_OVERRIDE", "")
    if override.strip():
        sdks: Dict[str, str] = {}
        for entry in override.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if "=" not in entry:
                print(f"Error: malformed SDK_URLS_OVERRIDE entry {entry!r}: expected 'name=url'")
                sys.exit(1)
            name, url = entry.split("=", 1)
            sdks[name.strip()] = url.strip().rstrip("/")
        if sdks:
            return sdks

    sdk_services_env = os.getenv("SDK_SERVICES", "")
    if not sdk_services_env:
        print("Error: SDK_SERVICES environment variable is not set.")
        print("Set it to a comma-separated list of SDK names (e.g., javascript,python,ruby)")
        sys.exit(1)

    sdk_names = [s.strip() for s in sdk_services_env.split(",") if s.strip()]
    return {name: f"http://{name}-sdk:3000" for name in sdk_names}


def validate_and_filter_scenarios(
    all_scenarios: List[Dict[str, Any]],
    known_capabilities=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    valid: List[Dict[str, Any]] = []
    excluded_empty: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []

    if not isinstance(all_scenarios, list):
        return [], [], [{"index": 0, "name": "<file>", "reason": "scenario file must be a JSON array"}]

    seen_names = set()
    for idx, scenario in enumerate(all_scenarios):
        if not isinstance(scenario, dict):
            invalid.append({"index": idx + 1, "name": f"scenario-{idx + 1}", "reason": "scenario is not an object"})
            continue
        name = scenario.get("name", f"scenario-{idx + 1}")
        steps = scenario.get("steps")

        if not isinstance(scenario.get("name"), str) or not scenario["name"].strip():
            invalid.append({"index": idx + 1, "name": name, "reason": "missing 'name'"})
            continue
        if name in seen_names:
            invalid.append({"index": idx + 1, "name": name, "reason": "duplicate scenario name"})
            continue
        seen_names.add(name)

        if "requires" in scenario:
            requires = scenario["requires"]
            if not isinstance(requires, list) or not requires or not all(isinstance(c, str) for c in requires):
                invalid.append(
                    {"index": idx + 1, "name": name, "reason": "'requires' must be a non-empty list of capability names"}
                )
                continue
            if known_capabilities is not None:
                unknown = [c for c in requires if c not in known_capabilities]
                if unknown:
                    invalid.append(
                        {
                            "index": idx + 1,
                            "name": name,
                            "reason": f"'requires' names unknown capability {unknown} "
                            f"(declare it in expected-capabilities.json)",
                        }
                    )
                    continue

        if not isinstance(steps, list):
            invalid.append({"index": idx + 1, "name": name, "reason": "steps must be an array"})
            continue

        if len(steps) == 0:
            excluded_empty.append({"index": idx + 1, "name": name, "reason": "no executable steps"})
            continue

        scenario_invalid = False
        normalized_steps = []
        for step_idx, step in enumerate(steps):
            reason = None
            if not isinstance(step, dict):
                reason = f"step {step_idx} is not an object"
            elif "action" not in step:
                reason = f"step {step_idx} missing 'action'"
            elif "expect" not in step:
                reason = f"step {step_idx} missing 'expect'"
            elif not isinstance(step["expect"], dict):
                reason = f"step {step_idx} 'expect' must be an object"
            elif "events" in step["expect"] and not isinstance(step["expect"]["events"], list):
                reason = f"step {step_idx} 'expect.events' must be an array"
            if reason:
                invalid.append({"index": idx + 1, "name": name, "reason": reason})
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


def load_scenarios(
    path: str, known_capabilities=None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load and validate a scenario file. The one loader every entry point uses."""
    try:
        with open(path) as src:
            all_scenarios = json.load(src)
    except FileNotFoundError:
        raise ConfigError(f"scenario file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"scenario file {path} is not valid JSON: {exc}")
    return validate_and_filter_scenarios(all_scenarios, known_capabilities)


def default_scenarios_path() -> str:
    path = os.getenv("TEST_SCENARIOS_PATH", "/test_scenarios_complete.json")
    if not os.path.exists(path):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_scenarios_complete.json"
        )
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run cross-SDK tests")
    parser.add_argument("--sdk", type=str, help="Comma-separated list of SDKs to test (e.g., rust,go,javascript)")
    skip_group = parser.add_mutually_exclusive_group()
    skip_group.add_argument(
        "--wrapper-skip",
        dest="wrapper_skip",
        action="store_true",
        help=(
            "Opt in to skipping scenarios a wrapper declares as pure pass-through to another SDK "
            "under test. Fast local runs only: skipped scenarios prove nothing about the wrapper."
        ),
    )
    skip_group.add_argument(
        "--no-wrapper-skip",
        dest="no_wrapper_skip",
        action="store_true",
        help="Run every scenario against every wrapper (the default; kept for compatibility)",
    )
    parser.add_argument(
        "--loose-error-match",
        action="store_true",
        help="Use relaxed error message matching instead of strict normalized equality",
    )
    parser.add_argument("--scenarios", type=str, default=None, help="Scenario file (default: TEST_SCENARIOS_PATH or repo file)")
    parser.add_argument(
        "--capability-policy",
        type=str,
        default=os.getenv("CAPABILITY_POLICY_PATH", DEFAULT_POLICY_PATH),
        help="Capability policy file (default: orchestrator/expected-capabilities.json)",
    )
    parser.add_argument("--report", type=str, default="/results/report.json", help="Where to write report.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output with full test details")
    parser.add_argument(
        "--compare-reports",
        nargs="+",
        metavar="REPORT",
        help=(
            "Do not run scenarios: load report.json files written by separate lanes and run the "
            "cross-SDK consistency check over all of them (exit 1 on any disagreement)"
        ),
    )
    parser.add_argument(
        "--expected-sdks",
        type=lambda value: [sdk.strip() for sdk in value.split(",") if sdk.strip()],
        help="Comma-separated SDKs required when comparing lane reports",
    )
    return parser


def compare_reports(
    paths: List[str], output_file: Optional[str] = None, expected_sdks: Optional[List[str]] = None
) -> int:
    """Cross-SDK consistency over reports written by independent lanes."""
    orch = TestOrchestrator({}, capability_policy=load_capability_policy())
    for path in paths:
        try:
            with open(path) as src:
                report = json.load(src)
        except (OSError, ValueError) as exc:
            print(f"Error: cannot read report {path}: {exc}")
            return 1
        if "scenario_outcomes" not in report or "step_observations" not in report:
            print(f"Error: report {path} has no scenario_outcomes/step_observations")
            return 1
        for name, outcomes in report["scenario_outcomes"].items():
            for sdk, outcome in outcomes.items():
                if sdk in orch.sdks:
                    print(f"Error: SDK {sdk!r} appears in more than one report")
                    return 1
                orch.scenario_outcomes.setdefault(name, {})[sdk] = outcome
        lane_sdks = {sdk for outcomes in report["scenario_outcomes"].values() for sdk in outcomes}
        for name, per_sdk in report["step_observations"].items():
            for sdk, steps in per_sdk.items():
                orch.step_observations.setdefault(name, {})[sdk] = {int(i): tuple(obs) for i, obs in steps.items()}
        for sdk in lane_sdks:
            orch.sdks[sdk] = path

    if expected_sdks is not None and set(orch.sdks) != set(expected_sdks):
        print(
            f"Error: missing SDKs {sorted(set(expected_sdks) - set(orch.sdks))}, "
            f"unexpected SDKs {sorted(set(orch.sdks) - set(expected_sdks))}"
        )
        return 1

    orch.check_cross_sdk_consistency()
    print(f"Compared {len(orch.sdks)} SDKs from {len(paths)} reports: {orch.consistency_comparisons} comparisons")
    for sdk, labels in sorted(orch.consistency_failures.items()):
        print(f"  {sdk}: {len(labels)} disagreement(s)")
        for label in labels[:20]:
            print(f"    - {label}")
    if output_file:
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_file, "w") as out:
            json.dump(
                {
                    "sdks": sorted(orch.sdks),
                    "consistency_comparisons": orch.consistency_comparisons,
                    "consistency_failures": orch.consistency_failures,
                    "cross_sdk_consistency": orch.consistency_report,
                },
                out,
                indent=2,
                default=describe,
            )
    if len(orch.sdks) < 2 or orch.consistency_comparisons == 0:
        print("Error: fewer than 2 SDKs or no comparisons; consistency was not checked")
        return 1
    return 1 if orch.consistency_failures else 0


def main(argv: Optional[List[str]] = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.compare_reports:
        sys.exit(compare_reports(args.compare_reports, args.report, args.expected_sdks))
    all_sdks = discover_sdks()

    if args.sdk:
        sdk_names = [s.strip() for s in args.sdk.split(",") if s.strip()]
        sdks = {name: all_sdks[name] for name in sdk_names if name in all_sdks}
        invalid_sdks = [name for name in sdk_names if name not in all_sdks]
        if invalid_sdks:
            print(f"Warning: Unknown SDK(s): {', '.join(invalid_sdks)}")
        if not sdks:
            print(f"No valid SDKs specified. Available: {', '.join(all_sdks.keys())}")
            sys.exit(1)
    else:
        sdks = all_sdks

    try:
        policy = load_capability_policy(args.capability_policy)
        scenarios_path = args.scenarios or default_scenarios_path()
        scenarios, excluded_empty, invalid = load_scenarios(scenarios_path, set(policy["capabilities"]))
    except ConfigError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    total_loaded = len(scenarios) + len(excluded_empty) + len(invalid)

    # CREATE_WITH_SCENARIO is coupled to run_scenario by exact name. If it was
    # renamed in the scenario file, the createContextWith coverage silently
    # reroutes to live-fetch; warn loudly so the rename is caught.
    if not any(s.get("name") == CREATE_WITH_SCENARIO for s in scenarios):
        print(
            f"{Colors.YELLOW}WARNING{Colors.RESET}: scenario "
            f"{CREATE_WITH_SCENARIO!r} not found — the createContextWith "
            f"(pre-fetched data) path is now UNCOVERED; every context is built "
            f"via live-fetch. Update CREATE_WITH_SCENARIO in test_runner.py if "
            f"the scenario was renamed."
        )

    print(f"Loaded {total_loaded} total scenarios from {scenarios_path}")
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
    if args.wrapper_skip:
        print(f"{Colors.YELLOW}Wrapper pass-through skipping ENABLED (--wrapper-skip){Colors.RESET}")
    print()

    orchestrator = TestOrchestrator(
        sdks,
        verbose=args.verbose,
        allow_wrapper_skip=args.wrapper_skip,
        loose_error_match=args.loose_error_match,
        capability_policy=policy,
    )

    working_sdks, failed_sdks = orchestrator.wait_for_services()
    if not working_sdks:
        print("No SDK services available - all failed to start")
        sys.exit(1)

    # Drive only the SDKs that came up, so a down SDK isn't run through every
    # scenario at a 5s timeout each. The failed set is still passed to
    # generate_report() below, so a down SDK fails the run rather than vanishing.
    orchestrator.sdks = working_sdks
    for scenario in scenarios:
        orchestrator.run_scenario(scenario)

    exit_code = orchestrator.generate_report(args.report, failed_sdks)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
