#!/usr/bin/env python3
"""Blocking SDK event-emission gate against the local collector.

Each wrapper runs its real SDK client and default publisher with
ABSMARTLY_E2E_ENDPOINT pointed at local-collector/local_collector.py. This runner sends
a fixed plan of exposures and goals per SDK, then reads every raw publish the
collector stored and checks it field by field:

- exposures: exactly one per planned unit per experiment, with the expected
  experiment id/name and variant (the context assigns variant 1 to everyone);
- goals: the exact multiset of (unit, name, properties), compared as JSON
  values (nested objects, booleans, numbers by value, Unicode, null), so a
  missing, extra, duplicated or mutated goal fails;
- revenue: the sum of the received purchase `amount` properties, never a
  count times a price;
- transport: application/environment/agent headers on every request, and
  every body parsed as JSON.

This verifies what the SDK emits and transports. It does not test analysis
or the backend; test-1 (orchestrator/e2e_runner.py) is the optional smoke run.

Units are matched by the publish `units` array ({type, uid}); `uid` is the
SDK's hashed unit, so the expected uids are hashed the same way
(MD5, base64url without padding) independently of any SDK.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from collections import Counter
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests

for _dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "local-collector"), "/local-collector"):
    sys.path.insert(0, _dir)
from local_collector import EXPERIMENT_ID, EXPERIMENT_NAME, SESSION_EXPERIMENT_ID, SESSION_EXPERIMENT_NAME  # noqa: E402

AGENT_LANGUAGE = {
    "swift-v1": "swift", "flutter": "dart", "java-v1": "java", "liquid": "ruby",
    "react": "javascript", "vue2": "javascript", "vue3": "javascript", "angular": "javascript",
}

# The planned cases: (case, goals). Properties cover nested objects, booleans,
# integer/decimal/negative/zero/large numbers, Unicode, null and no properties.
GOAL_CASES: List[Tuple[str, List[Optional[Dict[str, Any]]]]] = [
    ("purchase_amounts", [{"amount": 10}, {"amount": 10.5}, {"amount": 0.1},
                          {"amount": -4}, {"amount": 0}, {"amount": 1000000}]),
    ("strings", [{"currency": "EUR"}, {"currency": "USD"}, {"label": "Größe 🧪 用戶"}]),
    ("nested", [{"order": {"total": 10.5, "items": [1, 2, {"sku": "a"}]}}]),
    ("booleans", [{"gift": True}, {"gift": False}]),
    ("null_value", [{"amount": None}]),
    ("no_properties", [None]),
    ("repeated", [{"amount": 1}] * 5),
]


def unit_hash(uid: Any) -> str:
    return base64.urlsafe_b64encode(hashlib.md5(str(uid).encode("utf-8")).digest()).decode().rstrip("=")


def plan(run_id: str, sdk: str) -> List[Dict[str, Any]]:
    cases = []
    for name, goals in GOAL_CASES:
        cases.append({"name": name, "units": {"user_id": f"lc-{run_id}-{sdk}-{name}"},
                      "goals": goals, "omit_properties": name == "no_properties"})
    cases.append({"name": "unicode_unit", "units": {"user_id": f"lc-{run_id}-{sdk}-用戶-🧪-é"}, "goals": []})
    cases.append({"name": "multiple_units", "units": {"user_id": f"lc-{run_id}-{sdk}-mu",
                                                       "session_id": f"lc-{run_id}-{sdk}-mu-session"},
                  "goals": []})
    return cases


def _canonical(value: Any) -> str:
    """JSON value as a comparable string; numbers compared by value without type collisions."""
    def walk(v):
        if isinstance(v, dict):
            return ["object", [[k, walk(v[k])] for k in sorted(v)]]
        if isinstance(v, list):
            return ["array", [walk(x) for x in v]]
        if isinstance(v, bool):
            return ["boolean", v]
        if v is None:
            return ["null"]
        if isinstance(v, (int, float, Decimal)):
            d = Decimal(str(v))
            if not d.is_finite():
                raise ValueError("non-finite number")
            return ["number", format(d.normalize(), "f") if d else "0"]
        if isinstance(v, str):
            return ["string", v]
        raise ValueError(f"unexpected JSON value {v!r}")
    parsed = json.loads(json.dumps(value), parse_float=Decimal, parse_int=Decimal)
    return json.dumps(walk(parsed), ensure_ascii=False, separators=(",", ":"))


def _publish_units(record: Dict[str, Any]) -> Tuple[set, List[str]]:
    """The publish's (type, uid) units and a diagnostic for each malformed entry."""
    body = record.get("json")
    if not isinstance(body, dict):
        return set(), []
    raw = body.get("units")
    if raw is None:
        return set(), []
    if not isinstance(raw, list):
        return set(), [f"publish #{record.get('seq')} units is not an array: {raw!r}"]
    units, errors = set(), []
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("type"), str) and isinstance(entry.get("uid"), str):
            units.add((entry["type"], entry["uid"]))
        else:
            errors.append(f"publish #{record.get('seq')} has a malformed unit entry: {entry!r}")
    return units, errors


def _covers(units: set, case_units: set) -> bool:
    """Whether a publish with `units` is this case's own publish (no other case's units)."""
    return bool(units) and units <= case_units


def _header(record: Any, name: str) -> Optional[str]:
    """The single value of header `name`; None if absent, repeated or the record is malformed."""
    headers = record.get("headers") if isinstance(record, dict) else None
    if not isinstance(headers, list):
        return None
    values = [pair[1] for pair in headers if isinstance(pair, list) and len(pair) == 2
              and isinstance(pair[0], str) and pair[0].lower() == name.lower()]
    return values[0] if len(values) == 1 else None


def _entries(record: Dict[str, Any], field: str, errors: List[str]) -> List[Dict[str, Any]]:
    """The object entries of a publish's `field` array; a diagnostic for anything else."""
    raw = record["json"].get(field)
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"publish #{record.get('seq')} {field} is not an array: {raw!r}")
        return []
    for entry in raw:
        if not isinstance(entry, dict):
            errors.append(f"publish #{record.get('seq')} has a malformed {field} entry: {entry!r}")
    return [entry for entry in raw if isinstance(entry, dict)]


def verify(sdk: str, cases: List[Dict[str, Any]], publishes: List[Dict[str, Any]],
           application: str, environment: str, xagent_versioned: bool = True) -> Dict[str, Any]:
    """Check this SDK's publishes against its plan; returns {pass, errors, revenue}."""
    errors: List[str] = []
    language = AGENT_LANGUAGE.get(sdk, sdk)
    expected_units = {(unit_type, unit_hash(uid)) for case in cases for unit_type, uid in case["units"].items()}

    exposures = Counter()
    goals = Counter()
    received_amounts: List[Decimal] = []
    for record in publishes:
        body = record.get("json")
        if not isinstance(body, dict):
            errors.append(f"publish #{record.get('seq')} is not a JSON object: {record.get('parseError')}")
            continue
        units, _ = _publish_units(record)
        mine = units & expected_units
        if not mine:
            continue  # another SDK's publish
        if units - expected_units:
            errors.append(f"publish #{record['seq']} carries unplanned units {sorted(units - expected_units)}")
        agent = _header(record, "X-Agent")
        prefix = f"absmartly-{language}-sdk"
        agent_ok = isinstance(agent, str) and (
            agent.startswith(prefix + "/") and len(agent) > len(prefix) + 1
            or (not xagent_versioned and agent == prefix))
        if not agent_ok:
            errors.append(f"publish #{record['seq']} agent {agent!r} is not {prefix}{'/<version>' if xagent_versioned else ''}")
        if _header(record, "X-Application") != application:
            errors.append(f"publish #{record['seq']} application header {_header(record, 'X-Application')!r}")
        if _header(record, "X-Environment") != environment:
            errors.append(f"publish #{record['seq']} environment header {_header(record, 'X-Environment')!r}")
        if body.get("hashed") is not True:
            errors.append(f"publish #{record['seq']} hashed={body.get('hashed')!r}")
        for exposure in _entries(record, "exposures", errors):
            unit_type = exposure.get("unit")
            uid = next((u for t, u in mine if t == unit_type), None)
            exp_id, exp_name = ((SESSION_EXPERIMENT_ID, SESSION_EXPERIMENT_NAME) if unit_type == "session_id"
                                else (EXPERIMENT_ID, EXPERIMENT_NAME))
            if exposure.get("id") != exp_id or exposure.get("name") != exp_name:
                errors.append(f"exposure for {unit_type} names {exposure.get('id')}/{exposure.get('name')}")
            if type(exposure.get("variant")) is not int or exposure.get("variant") != 1:
                errors.append(f"exposure for {unit_type} has variant {exposure.get('variant')!r}, expected 1")
            if exposure.get("assigned") is not True or exposure.get("eligible") is not True:
                errors.append(f"exposure for {unit_type} assigned/eligible not true")
            exposures[(unit_type, uid, exposure.get("name"))] += 1
        user = next((u for t, u in mine if t == "user_id"), None)
        for goal in _entries(record, "goals", errors):
            properties = goal.get("properties")
            try:
                key = (user, goal.get("name"), _canonical(properties if properties is not None else {}))
            except ValueError as exc:
                errors.append(f"goal {goal.get('name')} has malformed properties: {exc}")
                continue
            goals[key] += 1
            if goal.get("name") == "purchase" and isinstance(properties, dict) and properties.get("amount") is not None:
                try:
                    amount = Decimal(str(properties["amount"]))
                    if not amount.is_finite():
                        raise ValueError("amount must be finite")
                    received_amounts.append(amount)
                except (ArithmeticError, TypeError, ValueError) as exc:
                    errors.append(f"purchase.amount is not numeric: {properties['amount']!r} ({exc})")

    want_exposures = Counter()
    want_goals = Counter()
    planned_amounts: List[Decimal] = []
    for case in cases:
        for unit_type, uid in case["units"].items():
            name = SESSION_EXPERIMENT_NAME if unit_type == "session_id" else EXPERIMENT_NAME
            want_exposures[(unit_type, unit_hash(uid), name)] += 1
        for properties in case["goals"]:
            want_goals[(unit_hash(case["units"]["user_id"]), "purchase", _canonical(properties or {}))] += 1
            if properties and properties.get("amount") is not None:
                planned_amounts.append(Decimal(str(properties["amount"])))

    for key in sorted(set(want_exposures) | set(exposures), key=str):
        if exposures[key] != want_exposures[key]:
            errors.append(f"exposure {key[0]}:{key[2]} received {exposures[key]}x, expected {want_exposures[key]}x")
    for key in sorted(set(want_goals) | set(goals), key=str):
        if goals[key] != want_goals[key]:
            errors.append(f"goal {key[1]} {key[2]} received {goals[key]}x, expected {want_goals[key]}x")
    revenue = sum(received_amounts, Decimal(0))
    planned = sum(planned_amounts, Decimal(0))
    if revenue != planned:
        errors.append(f"purchase.amount sum {revenue} != planned {planned}")
    return {"pass": not errors, "errors": errors, "revenue": str(revenue), "planned_revenue": str(planned),
            "exposures": sum(exposures.values()), "goals": sum(goals.values())}


def drive(sdk: str, base_url: str, cases: List[Dict[str, Any]], timeout: float = 30) -> List[str]:
    """Run the plan through the wrapper's e2e mode; returns wrapper errors."""
    errors = []
    for case in cases:
        try:
            created = requests.post(f"{base_url}/context", json={
                "mode": "e2e", "units": case["units"], "attributes": {"e2e_sdk": sdk, "e2e_case": case["name"]},
            }, timeout=timeout)
            if created.status_code == 501:
                return [f"{sdk}: e2e mode not configured (501)"]
            created.raise_for_status()
            try:
                result = created.json().get("result")
            except (ValueError, AttributeError):
                result = None
            if not isinstance(result, dict):
                errors.append(f"{case['name']}: wrapper returned an invalid /context response: {created.text[:200]}")
                continue
            context_id = result.get("contextId")
            if not context_id or result.get("ready") is not True or result.get("failed"):
                errors.append(f"{case['name']}: context not ready: {created.text[:200]}")
                continue
            url = f"{base_url}/context/{context_id}"
            try:
                requests.post(f"{url}/treatment", json={"experimentName": EXPERIMENT_NAME},
                              timeout=timeout).raise_for_status()
                if "session_id" in case["units"]:
                    requests.post(f"{url}/treatment", json={"experimentName": SESSION_EXPERIMENT_NAME},
                                  timeout=timeout).raise_for_status()
                for properties in case["goals"]:
                    body = {"goalName": "purchase"}
                    if not case.get("omit_properties"):
                        body["properties"] = properties
                    requests.post(f"{url}/track", json=body, timeout=timeout).raise_for_status()
                requests.post(f"{url}/publish", timeout=timeout).raise_for_status()
            finally:
                requests.delete(url, timeout=10)
        except requests.RequestException as exc:
            errors.append(f"{case['name']}: wrapper call failed: {exc}")
    return errors


def fetch_publishes(collector_url: str, expected_cases: List[set], settle: float = 2.0,
                    timeout: float = 30, poll: float = 0.5) -> List[Dict[str, Any]]:
    """Read publishes until every planned case has its own publish, then settle and re-read.
    A case counts only for a publish whose units all belong to that case, so a stray, a duplicate
    or one publish combining two cases does not satisfy a missing case; on timeout, whatever arrived."""
    deadline = time.monotonic() + timeout
    while True:
        publishes = requests.get(f"{collector_url}/publishes", timeout=10).json()["publishes"]
        arrived = [_publish_units(r)[0] for r in publishes]
        if all(any(_covers(units, case_units) for units in arrived) for case_units in expected_cases):
            time.sleep(settle)
            return requests.get(f"{collector_url}/publishes", timeout=10).json()["publishes"]
        if time.monotonic() >= deadline:
            return publishes
        time.sleep(poll)


def load_policy() -> Dict[str, Any]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expected-capabilities.json")
    with open(path) as src:
        return json.load(src)


def run(sdks: Dict[str, str], collector_url: str, application: str, environment: str,
        run_id: str) -> Dict[str, Any]:
    policy = load_policy()["sdks"]
    results: Dict[str, Any] = {"run_id": run_id, "sdks": {}, "overall_pass": True}
    requests.delete(f"{collector_url}/publishes", timeout=10).raise_for_status()
    plans = {sdk: plan(run_id, sdk) for sdk in sdks}
    driver_errors = {sdk: drive(sdk, url, plans[sdk]) for sdk, url in sdks.items()}
    case_units = {sdk: {case["name"]: {(unit_type, unit_hash(uid)) for unit_type, uid in case["units"].items()}
                        for case in cases} for sdk, cases in plans.items()}
    planned_units = set().union(*(units for cases in case_units.values() for units in cases.values()))
    # Every case ends in one /publish call with at least one exposure queued.
    publishes = fetch_publishes(collector_url, [units for cases in case_units.values() for units in cases.values()])
    arrived = []
    for record in publishes:
        units, unit_errors = _publish_units(record)
        arrived.append(units)
        if isinstance(record.get("json"), dict) and not units & planned_units:
            unit_errors.append(f"publish #{record.get('seq')} carries no planned unit: {sorted(units, key=str)}")
        if unit_errors:
            results.setdefault("transport_errors", []).extend(unit_errors)
    try:
        request_rows = requests.get(f"{collector_url}/requests", timeout=10).json().get("requests")
        if not isinstance(request_rows, list):
            raise ValueError(f"requests is {request_rows!r}")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        results.setdefault("transport_errors", []).append(f"collector request log unavailable: {exc}")
        request_rows = []
    for request in request_rows:
        if _header(request, "X-Application") != application:
            results_error = f"context request application header {_header(request, 'X-Application')!r}"
            results.setdefault("transport_errors", []).append(results_error)
        if _header(request, "X-Environment") != environment:
            results.setdefault("transport_errors", []).append(f"context request environment header {_header(request, 'X-Environment')!r}")

    for sdk in sdks:
        entry = policy.get(sdk, {})
        unsupported = entry.get("e2e", {}).get("unsupported", {})
        sdk_cases = [c for c in plans[sdk]
                     if not (c["name"] == "null_value" and "nullPropertyValues" in unsupported)]
        versioned = "xAgentHeader" not in entry.get("unsupported", {})
        outcome = verify(sdk, sdk_cases, publishes, application, environment, versioned)
        missing = [case["name"] for case in sdk_cases
                   if not any(_covers(units, case_units[sdk][case["name"]]) for units in arrived)]
        outcome["errors"] = driver_errors[sdk] + outcome["errors"] + [
            f"case {name}: no publish carrying only this case's units" for name in missing]
        outcome["pass"] = not outcome["errors"]
        results["sdks"][sdk] = outcome
        results["overall_pass"] &= outcome["pass"]
    if results.get("transport_errors"):
        results["overall_pass"] = False
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Blocking SDK event-emission gate against the local collector")
    parser.add_argument("--sdk", required=True, help="comma-separated SDK names")
    parser.add_argument("--collector", default=os.getenv("LOCAL_COLLECTOR_URL", "http://local-collector:8090"))
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    names = [s.strip() for s in args.sdk.split(",") if s.strip()]
    override = dict(e.split("=", 1) for e in os.getenv("SDK_URLS_OVERRIDE", "").split(",") if "=" in e)
    sdks = {name: override.get(name, f"http://{name}-sdk:3000") for name in names}
    results = run(sdks, args.collector, os.getenv("ABSMARTLY_E2E_APPLICATION", "e2e-tests"),
                  os.getenv("ABSMARTLY_E2E_ENVIRONMENT", "production"), os.urandom(4).hex())
    for sdk, outcome in results["sdks"].items():
        status = "PASS" if outcome["pass"] else "FAIL"
        print(f"{sdk}: {status} exposures={outcome['exposures']} goals={outcome['goals']} "
              f"revenue={outcome['revenue']}/{outcome['planned_revenue']}")
        for error in outcome["errors"]:
            print(f"  - {error}")
    if args.report:
        with open(args.report, "w") as out:
            json.dump(results, out, indent=2, ensure_ascii=False)
    sys.exit(0 if results["overall_pass"] else 1)


if __name__ == "__main__":
    main()
