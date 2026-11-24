#!/usr/bin/env python3
import base64
from decimal import Decimal
import hashlib
from collections import Counter
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

from e2e_cases import case_specs, eq_filter, matches, measured_total, metric_specs


# Fallback environment when neither config["environment"] nor
# ABSMARTLY_E2E_ENVIRONMENT is set. Experiment creation and the context poll
# MUST agree on this name: create places the experiment in this environment and
# the poll queries the SDK context for the same one, so a mismatch makes the
# poll never find the experiment and mark every exposure suspect.
DEFAULT_E2E_ENVIRONMENT = "production"

# Profile names are interpolated into shell pipelines during cleanup, so they
# must be a strict identifier (no shell metacharacters).
PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_profile(profile: str) -> str:
    if profile and not PROFILE_RE.match(profile):
        raise ValueError(
            f"Invalid profile {profile!r}: must match [A-Za-z0-9_-]+ "
            "(it is interpolated into shell cleanup commands)"
        )
    return profile


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def generate_run_id() -> str:
    return uuid.uuid4().hex[:8]


# Only an HTTP 429 from the API retries; a "429" elsewhere (an ID) does not. CLI 1.18.0
# reports a 429 as "Rate limit exceeded." (lib/api/axios-adapter.js handleError).
RATE_LIMITED_RE = re.compile(r"(^|\n)(Error: )?Rate limit exceeded\.|status code 429|HTTP 429|\b429 Too Many Requests")
RATE_LIMIT_SLEEP = 120


def run_abs(args: List[str], profile: str = "", api_key: str = "", endpoint: str = "", output_json: bool = True,
            deadline: Optional[float] = None) -> Tuple[int, str]:
    cmd = ["abs"] + args
    if api_key and endpoint:
        cmd += ["--api-key", api_key, "--endpoint", endpoint]
    elif profile:
        cmd += ["--profile", profile]
    if output_json:
        cmd += ["-o", "json"]
    for attempt in range(3):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 or not RATE_LIMITED_RE.search(result.stdout + result.stderr):
            break
        if attempt == 2 or (deadline is not None and time.time() + RATE_LIMIT_SLEEP >= deadline):
            break
        time.sleep(RATE_LIMIT_SLEEP)
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr.strip():
        output = output + "\n" + result.stderr.strip() if output else result.stderr.strip()
    return result.returncode, output


def parse_json_output(output: str) -> Any:
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for start_char in ('[', '{'):
        idx = output.find(start_char)
        if idx >= 0:
            try:
                result, _ = decoder.raw_decode(output, idx)
                return result
            except json.JSONDecodeError:
                continue
    return None


def _extract_id(output: str) -> Optional[str]:
    data = parse_json_output(output)
    if data and isinstance(data, dict):
        for key in ("id", "experiment_id"):
            if key in data:
                return str(data[key])
    import re
    match = re.search(r"ID:\s*(\d+)", output)
    if match:
        return match.group(1)
    return None


class E2ERunner:
    def __init__(self, sdks: Dict[str, str], config: Dict[str, Any]):
        self.sdks = sdks
        self.config = config
        self.experiment_id: Optional[int] = None
        self.experiment_name: Optional[str] = None
        self.run_id = generate_run_id()
        self.profile = validate_profile(config.get("profile", "e2e"))
        # Resolve the ABSmartly environment once so experiment creation and the
        # context poll agree: config wins, then the env var, then the default.
        self.environment = config.get("environment") or os.getenv(
            "ABSMARTLY_E2E_ENVIRONMENT", DEFAULT_E2E_ENVIRONMENT
        )
        self.units_per_sdk = config.get("units", 100)
        self.timeout = config.get("timeout", 300)
        self.verbose = config.get("verbose", False)
        self.dry_run = config.get("dry_run", False)
        self.sdk_results: Dict[str, Dict[str, Any]] = {}
        self.skipped_sdks: List[str] = []
        self.experiment_context_ready = True
        self.app_id: Optional[str] = None
        self.unit_type: Optional[str] = None
        self.owner_id: Optional[str] = None
        self.metric_id: Optional[str] = None
        self.goal_id: Optional[str] = None
        self.case_metric_ids = {}
        self.case_results = {}
        with open(os.path.join(os.path.dirname(__file__), "expected-capabilities.json")) as src:
            self.capability_policy = json.load(src)
        self.session_experiment_id = None
        self.session_experiment_name = None

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"  {Colors.CYAN}[debug]{Colors.RESET} {msg}")

    def _find_resource(self, resource_type: str, name: str) -> Optional[str]:
        rc, output = run_abs([resource_type, "list", "--items", "500"], self.profile)
        if rc != 0:
            self.log(f"_find_resource({resource_type}, {name}) failed: {output}")
            return None
        data = parse_json_output(output)
        if data and isinstance(data, list):
            for item in data:
                if item.get("name") == name:
                    return str(item["id"])
        return None

    def run(self) -> Dict[str, Any]:
        print(f"\n{Colors.BOLD}E2E Test Run: {self.run_id}{Colors.RESET}")
        print(f"  SDKs: {', '.join(self.sdks.keys())}")
        print(f"  Units per SDK: {self.units_per_sdk}")
        print(f"  Profile: {self.profile}")
        print()

        try:
            self.ensure_resources()
            self.create_experiment()
            self.start_experiment()
            self.create_session_experiment()
            self.run_sdk_scenarios()
            self.run_event_cases()
            self.wait_for_metrics()
            results = self.verify_metrics()
            self.print_report(results)
            return results
        finally:
            self.cleanup_experiment()

    def ensure_resources(self) -> None:
        if self.dry_run:
            print(f"  {Colors.YELLOW}[dry-run]{Colors.RESET} Skipping resource provisioning")
            return

        print("Ensuring required resources exist...")
        self._ensure_application()
        self._ensure_unit_type()
        self._ensure_owner()
        self._ensure_goal()
        self._ensure_metric()
        self._ensure_case_metrics()
        print(f"  {Colors.GREEN}Resources ready{Colors.RESET} app={self.app_id} unit_type={self.unit_type} owner={self.owner_id} metric={self.metric_id}")

    def _ensure_application(self) -> None:
        self.app_id = self._find_resource("apps", "e2e-tests")
        if self.app_id:
            self.log(f"Found application 'e2e-tests' with id={self.app_id}")
            return

        rc, output = run_abs(["apps", "create", "--name", "e2e-tests"], self.profile)
        if rc != 0 and "already exists" in output:
            self.app_id = self._find_resource("apps", "e2e-tests")
            if self.app_id:
                self.log(f"Found application 'e2e-tests' with id={self.app_id} (after create conflict)")
                return
            raise RuntimeError(f"Application 'e2e-tests' exists but could not find its ID")
        if rc != 0:
            raise RuntimeError(f"Failed to create application 'e2e-tests': {output}")

        self.app_id = _extract_id(output)
        if not self.app_id:
            raise RuntimeError(f"Could not determine application ID from output: {output}")
        self.log(f"Created application 'e2e-tests' with id={self.app_id}")

    def _ensure_unit_type(self) -> None:
        self.unit_type = self._find_resource("units", "user_id")
        if self.unit_type:
            self.log(f"Found unit type 'user_id' with id={self.unit_type}")
            return
        raise RuntimeError("Unit type 'user_id' not found")

    def _ensure_owner(self) -> None:
        rc, output = run_abs(["users", "list", "--items", "500"], self.profile)
        if rc != 0:
            raise RuntimeError(f"Failed to list users: {output}")

        data = parse_json_output(output)
        if not data:
            raise RuntimeError(f"Could not parse users output: {output}")

        items = data if isinstance(data, list) else []
        for item in items:
            if not item.get("archived", False):
                self.owner_id = str(item["id"])
                self.log(f"Found owner '{item.get('name', item.get('email', 'unknown'))}' with id={self.owner_id}")
                return

        raise RuntimeError("No non-archived users found")

    def _ensure_goal(self) -> None:
        self.goal_id = self._find_resource("goals", "purchase")
        if self.goal_id:
            self.log(f"Found goal 'purchase' with id={self.goal_id}")
            return

        rc, output = run_abs(["goals", "create", "--name", "purchase"], self.profile)
        if rc != 0 and "already exists" in output:
            self.goal_id = self._find_resource("goals", "purchase")
            if self.goal_id:
                return
        if rc != 0:
            raise RuntimeError(f"Failed to create goal 'purchase': {output}")

        self.goal_id = _extract_id(output)
        if not self.goal_id:
            raise RuntimeError(f"Could not determine goal ID from output: {output}")
        self.log(f"Created goal 'purchase' with id={self.goal_id}")

    def _ensure_metric(self) -> None:
        self.metric_id = self._find_resource("metrics", "e2e_purchase_count")
        if self.metric_id:
            self.log(f"Found metric 'e2e_purchase_count' with id={self.metric_id}")
            return

        rc, output = run_abs([
            "metrics", "create",
            "--name", "e2e_purchase_count",
            "--type", "goal_unique_count",
            "--description", "E2E test metric tracking purchase goal",
            "--goal-id", self.goal_id,
            "--owner", self.owner_id,
        ], self.profile)

        if rc != 0 and "already exists" not in output:
            raise RuntimeError(f"Failed to create metric: {output}")

        self.metric_id = _extract_id(output)
        if not self.metric_id:
            raise RuntimeError(f"Could not determine metric ID from output: {output}")

        for cmd in ["request", "approve"]:
            run_abs(["metrics", "review", cmd, self.metric_id], self.profile, output_json=False)
        run_abs(["metrics", "activate", self.metric_id, "--reason", "E2E testing"], self.profile, output_json=False)

        self.log(f"Created and activated metric 'e2e_purchase_count' with id={self.metric_id}")

    def _ensure_case_metrics(self):
        for key, spec in metric_specs().items():
            metric_id = self._find_resource("metrics", spec["name"])
            if not metric_id:
                args = ["metrics", "create", "--name", spec["name"],
                        "--type", spec["type"], "--description", "E2E collector-arrival check",
                        "--goal-id", self.goal_id, "--owner", self.owner_id,
                        "--outlier-limit-method", "unlimited"]
                if "path" in spec:
                    args += ["--value-source-property", spec["path"]]
                if "filter" in spec:
                    args += ["--property-filter", json.dumps(spec["filter"])]
                rc, output = run_abs(args, self.profile)
                if rc != 0:
                    raise RuntimeError(f"Metric {spec['name']}: {output}")
                metric_id = _extract_id(output)
                if not metric_id:
                    raise RuntimeError(f"Metric {spec['name']} missing id: {output}")
                for action in ("request", "approve"):
                    rc, output = run_abs(["metrics", "review", action, metric_id], self.profile)
                    if rc:
                        raise RuntimeError(f"Metric review {action}: {output}")
                rc, output = run_abs(["metrics", "activate", metric_id, "--reason", "E2E testing"], self.profile)
                if rc:
                    raise RuntimeError(f"Metric activation: {output}")
            rc, output = run_abs(["metrics", "get", metric_id], self.profile)
            detail = parse_json_output(output) if rc == 0 else None
            if not isinstance(detail, dict):
                raise RuntimeError(f"Cannot validate existing metric {spec['name']}: {output}")
            detail = detail.get("metric", detail)
            if detail.get("type") != spec["type"] or str(detail.get("goal_id")) != str(self.goal_id):
                raise RuntimeError(f"Metric {spec['name']} has the wrong type/goal")
            if "path" in spec and detail.get("value_source_property") != spec["path"]:
                raise RuntimeError(f"Metric {spec['name']} has the wrong property path")
            raw_filter = detail.get("property_filter")
            if isinstance(raw_filter, str) and raw_filter:
                raw_filter = json.loads(raw_filter)
            if "filter" in spec and raw_filter != spec["filter"]:
                raise RuntimeError(f"Metric {spec['name']} has the wrong property filter")
            if detail.get("outlier_limit_method") != "unlimited":
                raise RuntimeError(f"Metric {spec['name']} must not limit outliers")
            self.case_metric_ids[key] = metric_id

    def create_session_experiment(self):
        if self.dry_run:
            return
        session_type = self._find_resource("units", "session_id")
        if not session_type:
            rc, output = run_abs(["units", "create", "--name", "session_id",
                                  "--description", "E2E multi-unit collector check"], self.profile)
            session_type = _extract_id(output) if rc == 0 else None
            if not session_type:
                raise RuntimeError(f"Cannot provision session_id: {output}")
        parent = self.experiment_id, self.experiment_name, self.unit_type
        try:
            self.unit_type = session_type
            self.creating_session = True
            self.experiment_id = None
            self.create_experiment()
        finally:
            self.session_experiment_id = self.experiment_id if self.experiment_id and self.experiment_id != parent[0] else None
            self.session_experiment_name = self.experiment_name if self.session_experiment_id else None
            self.experiment_id, self.experiment_name, self.unit_type = parent
            self.creating_session = False
        if not self.session_experiment_id:
            raise RuntimeError("Session experiment was not created")
        rc, output = run_abs(["experiments", "start", str(self.session_experiment_id)], self.profile)
        if rc:
            raise RuntimeError(f"Start session experiment: {output}")
        parent_id = self.experiment_id
        try:
            self.experiment_id = self.session_experiment_id
            if not self._wait_for_experiment_in_context():
                raise RuntimeError("Session experiment is not visible in the collector context")
        finally:
            self.experiment_id = parent_id

    def _case_skip(self, sdk, case):
        cap = case.get("capability")
        entry = self.capability_policy["sdks"].get(sdk)
        if entry is None:
            raise RuntimeError(f"Missing capability policy for {sdk}")
        if cap == "numericUnitIds":
            if cap in entry["required"]:
                return None
            reason = entry["unsupported"].get(cap)
        elif cap:
            policy = entry.get("e2e", {})
            if cap in policy.get("required", []):
                return None
            reason = policy.get("unsupported", {}).get(cap)
        else:
            return None
        if not reason:
            raise RuntimeError(f"Unclassified e2e capability {cap} for {sdk}")
        return reason

    def run_event_cases(self):
        for sdk, base_url in self.sdks.items():
            self.case_results[sdk] = {}
            for case in case_specs(self.run_id, sdk):
                name = case["name"]
                try:
                    reason = self._case_skip(sdk, case)
                    if reason:
                        self.case_results[sdk][name] = {"status": "unsupported", "reason": reason}
                        continue
                    if self.dry_run:
                        self.case_results[sdk][name] = {"status": "dry_run"}
                        continue
                    self._send_event_case(sdk, base_url, case)
                    self.case_results[sdk][name] = {"status": "sent"}
                except Exception as exc:
                    self.case_results[sdk][name] = {"status": "fail", "reason": str(exc)}

    def _wrapper_post(self, url, body=None):
        for attempt in range(3):
            response = requests.post(url, json=body or {}, timeout=30)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt < 2:
                time.sleep(120)
        response.raise_for_status()

    def _send_event_case(self, sdk, base_url, case):
        units = {"user_id": case["uid"]}
        if "session" in case:
            units["session_id"] = case["session"]
        payload = {"mode": "e2e", "units": units,
                   "attributes": {"sdk_name": sdk, "e2e_case": case["name"]}}
        if case.get("pre_ready"):
            payload["preReadyGoals"] = [{"goalName": "purchase", "properties": g} for g in case["goals"]]
        if case.get("pre_ready"):
            # Establish the unit's exposure before the pre-ready achievement:
            # analysis metrics count goals only after first exposure.
            prime = dict(payload)
            prime.pop("preReadyGoals")
            primed = self._wrapper_post(base_url + "/context", prime)["result"]["contextId"]
            prime_url = base_url + "/context/" + primed
            self._wrapper_post(prime_url + "/treatment", {"experimentName": self.experiment_name})
            self._wrapper_post(prime_url + "/publish")
            requests.delete(prime_url, timeout=10).raise_for_status()
        created = self._wrapper_post(base_url + "/context", payload)
        result = created.get("result", {})
        context_id = result.get("contextId")
        if not context_id or result.get("ready") is not True or result.get("failed"):
            raise RuntimeError(f"Context is not ready: {created}")
        if case.get("pre_ready") and result.get("preReadyTracked") is not True:
            raise RuntimeError("Wrapper did not confirm tracking in the SDK's not-ready state")
        url = base_url + "/context/" + context_id
        try:
            self._wrapper_post(url + "/treatment", {"experimentName": self.experiment_name})
            if "session" in case:
                self._wrapper_post(url + "/treatment", {"experimentName": self.session_experiment_name})
            if not case.get("pre_ready"):
                for properties in case["goals"]:
                    body = {"goalName": "purchase"}
                    if not case.get("omit_properties"):
                        body["properties"] = properties
                    self._wrapper_post(url + "/track", body)
            # Exactly one publish for the batch; close case must never publish explicitly.
            self._wrapper_post(url + ("/finalize" if case.get("close") else "/publish"))
        finally:
            requests.delete(url, timeout=10).raise_for_status()

    def _query_metric(self, metric_id, sdk, case="baseline", experiment_id=None, deadline=None):
        rc, output = run_abs([
            "experiments", "metrics", "results", str(experiment_id or self.experiment_id),
            "--metric", str(metric_id), "--filter", json.dumps(eq_filter(sdk_name=sdk, e2e_case=case)),
        ], self.profile, deadline=deadline)
        if rc:
            self.log(f"Unmeasurable {sdk}/{case}/{metric_id}: {output}")
            return None
        data = parse_json_output(output)
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            if str(data[0].get("metric_id")) == str(metric_id):
                return data[0]
        return None

    def verify_event_cases(self, results):
        results["event_cases"] = {}
        for sdk in self.sdks:
            rows = results["event_cases"][sdk] = {}
            for case in case_specs(self.run_id, sdk):
                name = case["name"]
                sent = self.case_results.get(sdk, {}).get(name, {"status": "fail", "reason": "Case was not executed"})
                rows[name] = dict(sent)
                if sent["status"] == "unsupported":
                    continue
                if sent["status"] != "sent":
                    results["overall_pass"] = False
                    continue
                metrics = rows[name]["metrics"] = {}
                cache = {}
                for key, expected in case["expected"].items():
                    source = "events" if key == "exposures" else key
                    if source not in cache:
                        cache[source] = self._query_metric(self.case_metric_ids[source], sdk, name)
                    actual = measured_total(cache[source], "unit_count" if key == "exposures" else "count")
                    metrics[key] = {"expected": str(expected), "actual": str(actual) if actual is not None else None,
                                    "pass": matches(expected, actual)}
                if "session" in case:
                    metric = self._query_metric(self.case_metric_ids["events"], sdk, name, self.session_experiment_id)
                    actual = measured_total(metric, "unit_count")
                    metrics["session_exposures"] = {"expected": "1", "actual": str(actual) if actual is not None else None,
                                                    "pass": matches(1, actual)}
                arrival = self._verify_arrival(sdk, case)
                rows[name]["arrival"] = arrival
                rows[name]["status"] = "pass" if all(m["pass"] for m in metrics.values()) and arrival["pass"] else "fail"
                if rows[name]["status"] != "pass":
                    results["overall_pass"] = False

    def _verify_arrival(self, sdk, case):
        units = {"user_id": case["uid"]}
        if "session" in case:
            units["session_id"] = case["session"]
        errors = []
        for unit_type, uid in units.items():
            hashed = base64.urlsafe_b64encode(hashlib.md5(str(uid).encode("utf-8")).digest()).decode().rstrip("=")
            events = []
            for offset in range(0, 5000, 500):
                rc, output = run_abs(["events", "list", "--from", "1d", "--unit-uid", hashed,
                                      "--items", "500", "--skip", str(offset)], self.profile)
                page = parse_json_output(output) if rc == 0 else None
                if not isinstance(page, list):
                    return {"pass": False, "errors": ["Collector event query unmeasurable"]}
                events.extend(page)
                if len(page) < 500:
                    break
            else:
                return {"pass": False, "errors": ["Collector event pagination limit exceeded"]}
            own = [e for e in events if e.get("unit_uid") == hashed and e.get("unit_type") == unit_type
                   and e.get("application_id") == int(self.app_id) and e.get("environment") == self.environment]
            exposures = [e for e in own if e.get("event_type") == "exposure"
                         and e.get("experiment_id") == (self.session_experiment_id if unit_type == "session_id" else self.experiment_id)]
            if not exposures:
                errors.append(f"No exposure for {unit_type}:{uid!r}")
            language = {"swift-v1": "swift", "java-v1": "java", "liquid": "ruby", "flutter": "dart",
                        "react": "javascript", "vue2": "javascript", "vue3": "javascript", "angular": "javascript"}.get(sdk, sdk)
            versioned = "xAgentHeader" not in self.capability_policy["sdks"].get(sdk, {}).get("unsupported", {})
            agent_re = r"absmartly-" + re.escape(language) + r"-sdk" + (r"/[^ /]+" if versioned else r"(?:/[^ /]+)?")
            for event in own:
                if not isinstance(event.get("agent"), str) or not re.fullmatch(agent_re, event["agent"]):
                    errors.append("Collector SDK agent missing or incorrect")
            for exposure in exposures:
                attrs = exposure.get("unit_attributes")
                try:
                    attrs = json.loads(attrs) if isinstance(attrs, str) else attrs
                except ValueError:
                    attrs = None
                if not isinstance(attrs, dict) or attrs.get("sdk_name") != sdk or attrs.get("e2e_case") != case["name"]:
                    errors.append("Exposure attributes changed")
            if unit_type != "user_id":
                continue
            goals = [e for e in own if e.get("event_type") == "goal" and e.get("goal_name") == "purchase"]
            def numbers(value):
                # Compare numbers by value: 10 and 10.0 are the same amount.
                if isinstance(value, dict):
                    return {k: numbers(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [numbers(v) for v in value]
                if isinstance(value, Decimal):
                    if not value.is_finite():
                        raise ValueError("non-finite property")
                    return {"$number": format(value.normalize(), "f") if value else "0"}
                return value

            def canonical(value):
                if isinstance(value, str):
                    value = json.loads(value, parse_float=Decimal, parse_int=Decimal)
                else:
                    value = json.loads(json.dumps(value), parse_float=Decimal, parse_int=Decimal)
                return json.dumps(numbers(value if value is not None else {}), sort_keys=True,
                                  ensure_ascii=False, separators=(",", ":"))
            try:
                actual = Counter(canonical(e.get("properties")) for e in goals)
                expected = Counter(canonical(g) for g in case["goals"])
            except (ValueError, TypeError):
                errors.append("Malformed collector properties")
            else:
                if actual != expected:
                    errors.append("Collector goal count/properties differ from planned calls")
        return {"pass": not errors, "errors": errors}

    def create_experiment(self) -> None:
        self.experiment_name = f"e2e-{self.run_id}" + ("-session" if getattr(self, "creating_session", False) else "")
        print(f"Creating experiment: {self.experiment_name}")

        if self.dry_run:
            self.experiment_id = 0
            print(f"  {Colors.YELLOW}[dry-run]{Colors.RESET} Skipping experiment creation")
            return

        env_name = self.environment

        create_args = [
            "experiments", "create",
            "--name", self.experiment_name,
            "--variants", "control,treatment",
            "--application-id", self.app_id,
            "--unit-type", self.unit_type,
            "--env", env_name,
            "--percentages", "50,50",
            "--owner", self.owner_id,
        ]
        if self.metric_id:
            create_args += ["--primary-metric", self.metric_id]

        rc, output = run_abs(create_args, self.profile)

        if rc != 0 and "custom field values are required" in output:
            self.log("Retrying with custom field placeholders...")
            create_args = self._add_required_custom_fields(create_args, output)
            rc, output = run_abs(create_args, self.profile)

        if rc != 0:
            raise RuntimeError(f"Failed to create experiment: {output}")

        extracted = _extract_id(output)
        if extracted:
            self.experiment_id = int(extracted)

        if not self.experiment_id:
            self.log(f"Create output: {output}")
            experiment = self._find_experiment_by_name(self.experiment_name)
            if experiment:
                self.experiment_id = experiment.get("id")

        if not self.experiment_id:
            raise RuntimeError(f"Could not determine experiment ID from output: {output}")

        print(f"  {Colors.GREEN}Created{Colors.RESET} experiment {self.experiment_id}")

    def _add_required_custom_fields(self, create_args: List[str], error_output: str) -> List[str]:
        import re
        matches = re.findall(r"'([^']+)'\s*\(id:\s*\d+\)", error_output)
        for field_name in matches:
            slug = field_name.lower().replace(" ", "-")
            if f"--{slug}" in " ".join(create_args):
                continue
            create_args += ["--field", f"{field_name}=E2E automated test"]
            self.log(f"Adding required custom field: {field_name}")
        return create_args

    def _find_experiment_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        rc, output = run_abs([
            "experiments", "list",
            "--search", name,
        ], self.profile)

        if rc != 0:
            return None

        data = parse_json_output(output)
        if not data:
            return None

        experiments = data if isinstance(data, list) else data.get("experiments", data.get("items", []))
        for exp in experiments:
            if exp.get("name") == name:
                return exp
        return experiments[0] if experiments else None

    def start_experiment(self) -> None:
        print(f"Starting experiment {self.experiment_id}...")

        if self.dry_run:
            print(f"  {Colors.YELLOW}[dry-run]{Colors.RESET} Skipping experiment start")
            return

        rc, output = run_abs([
            "experiments", "start", str(self.experiment_id),
        ], self.profile, output_json=False)

        if rc != 0:
            raise RuntimeError(f"Failed to start experiment: {output}")

        print(f"  {Colors.GREEN}Started{Colors.RESET}")
        self.experiment_context_ready = self._wait_for_experiment_in_context()

    def _wait_for_experiment_in_context(self) -> bool:
        """Poll the SDK endpoint until the started experiment is assignable.

        Returns True once the experiment shows up in the context payload. If it
        never appears within the timeout, returns False after an always-printed
        warning: every unit published afterwards is assigned against a context
        that does not yet contain the experiment, so those exposures are suspect
        and each SDK counts them as context errors (see _run_sdk_scenario).
        """
        sdk_endpoint = os.getenv("ABSMARTLY_E2E_ENDPOINT", "")
        sdk_key = os.getenv("ABSMARTLY_E2E_API_KEY", "")
        app = os.getenv("ABSMARTLY_E2E_APPLICATION", "e2e-tests")
        env = self.environment

        if not sdk_endpoint or not sdk_key:
            self.log("No SDK endpoint configured, skipping context poll")
            # Nothing to poll against; assume ready so we don't fabricate errors.
            return True

        for attempt in range(30):
            try:
                resp = requests.get(
                    f"{sdk_endpoint}/context",
                    params={"application": app, "environment": env},
                    headers={"X-API-Key": sdk_key},
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for exp in data.get("experiments", []):
                        if exp.get("id") == self.experiment_id:
                            self.log(f"Experiment visible in context after {attempt + 1} poll(s)")
                            return True
            except requests.RequestException:
                pass
            time.sleep(1)

        print(
            f"  {Colors.RED}Experiment {self.experiment_id} not visible in SDK "
            f"context after 30s{Colors.RESET}: exposures may not be recorded; "
            f"affected units will count as context errors"
        )
        return False

    def run_sdk_scenarios(self) -> None:
        print(f"\nRunning scenarios across {len(self.sdks)} SDK(s)...")

        sdk_list = list(self.sdks.items())
        batch_size = 5

        for batch_start in range(0, len(sdk_list), batch_size):
            batch = sdk_list[batch_start:batch_start + batch_size]
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = {}
                for sdk_name, base_url in batch:
                    future = executor.submit(self._run_sdk_scenario, sdk_name, base_url)
                    futures[future] = sdk_name

                for future in as_completed(futures):
                    sdk_name = futures[future]
                    try:
                        result = future.result()
                        self.sdk_results[sdk_name] = result
                    except Exception as exc:
                        self.sdk_results[sdk_name] = {
                            "status": "error",
                            "error": str(exc),
                            "exposures_sent": 0,
                            "goals_sent": 0,
                            "revenue_sent": 0,
                            "planned_exposures": self.units_per_sdk,
                            "planned_goals": (self.units_per_sdk + 1) // 2,
                            "context_errors": self.units_per_sdk,
                            "treatment_errors": 0,
                            "track_errors": 0,
                        }

    def _run_sdk_scenario(self, sdk_name: str, base_url: str) -> Dict[str, Any]:
        exposures_sent = 0
        goals_sent = 0
        revenue_sent = 0
        # Error counters: any nonzero count fails this SDK. They are kept
        # separate from the *_sent tallies so a wrapper that (say) 500s on
        # every /track can't shrink its own expected-goal target to 0 and pass
        # trivially. Expected is derived from planned units below, not successes.
        context_errors = 0
        treatment_errors = 0
        track_errors = 0

        # Planned volume: one treatment per unit, one track on every other unit.
        # Verification compares the backend against these planned figures, so a
        # wrapper that silently drops calls fails rather than lowering the bar.
        planned_exposures = self.units_per_sdk
        planned_goals = (self.units_per_sdk + 1) // 2

        def scenario_result(status: str, error: Optional[str] = None) -> Dict[str, Any]:
            result = {
                "status": status,
                "exposures_sent": exposures_sent,
                "goals_sent": goals_sent,
                "revenue_sent": revenue_sent,
                "planned_exposures": planned_exposures,
                "planned_goals": planned_goals,
                "context_errors": context_errors,
                "treatment_errors": treatment_errors,
                "track_errors": track_errors,
            }
            if error is not None:
                result["error"] = error
            return result

        try:
            health_resp = requests.get(f"{base_url}/health", timeout=5)
            if health_resp.status_code != 200:
                return scenario_result("error", "Health check failed")
        except requests.RequestException as exc:
            return scenario_result("error", f"Service unreachable: {exc}")

        for i in range(self.units_per_sdk):
            unit_id = f"e2e-{self.run_id}-{sdk_name}-{i}"

            try:
                ctx_resp = requests.post(f"{base_url}/context", json={
                    "mode": "e2e",
                    "units": {"user_id": unit_id},
                    "attributes": {"sdk_name": sdk_name, "e2e_case": "baseline"},
                }, timeout=10)
            except requests.RequestException as exc:
                self.log(f"{sdk_name} unit {i}: context creation failed: {exc}")
                context_errors += 1
                continue

            if ctx_resp.status_code == 501:
                print(f"  {Colors.YELLOW}SKIP{Colors.RESET} {sdk_name} (e2e mode not supported)")
                self.skipped_sdks.append(sdk_name)
                return scenario_result("skipped")

            if ctx_resp.status_code != 200:
                self.log(f"{sdk_name} unit {i}: context creation returned {ctx_resp.status_code}")
                context_errors += 1
                continue

            ctx_data = ctx_resp.json()
            context_id = ctx_data.get("result", {}).get("contextId")
            if not context_id:
                self.log(f"{sdk_name} unit {i}: no contextId in response")
                context_errors += 1
                continue

            # If the experiment never became visible in the SDK context, the
            # assignment this unit gets does not include it. Count it so the SDK
            # fails loudly instead of silently recording bogus exposures.
            if not self.experiment_context_ready:
                context_errors += 1

            try:
                treat_resp = requests.post(
                    f"{base_url}/context/{context_id}/treatment",
                    json={"experimentName": self.experiment_name},
                    timeout=10,
                )
                if treat_resp.status_code == 200:
                    exposures_sent += 1
                else:
                    self.log(f"{sdk_name} unit {i}: treatment returned {treat_resp.status_code}")
                    treatment_errors += 1
            except requests.RequestException as exc:
                self.log(f"{sdk_name} unit {i}: treatment failed: {exc}")
                treatment_errors += 1

            if i % 2 == 0:
                try:
                    track_resp = requests.post(
                        f"{base_url}/context/{context_id}/track",
                        json={"goalName": "purchase", "properties": {"amount": 10}},
                        timeout=10,
                    )
                    if track_resp.status_code == 200:
                        goals_sent += 1
                        revenue_sent += 10
                    else:
                        self.log(f"{sdk_name} unit {i}: track returned {track_resp.status_code}")
                        track_errors += 1
                except requests.RequestException as exc:
                    self.log(f"{sdk_name} unit {i}: track failed: {exc}")
                    track_errors += 1

            self._publish_with_retry(base_url, context_id, sdk_name, i)

        errors_note = ""
        if context_errors or treatment_errors or track_errors:
            errors_note = (
                f" {Colors.RED}[errors ctx={context_errors} "
                f"treat={treatment_errors} track={track_errors}]{Colors.RESET}"
            )
        print(
            f"  {Colors.GREEN}Done{Colors.RESET} {sdk_name}: "
            f"{exposures_sent} exposures, {goals_sent} goals, {revenue_sent} revenue{errors_note}"
        )

        return scenario_result("ok")

    def _publish_with_retry(self, base_url: str, context_id: str, sdk_name: str, unit_index: int) -> bool:
        for attempt in range(3):
            try:
                resp = requests.post(f"{base_url}/context/{context_id}/publish", timeout=10)
                if resp.status_code == 200:
                    return True
                if resp.status_code >= 500:
                    self.log(f"{sdk_name} unit {unit_index}: publish returned {resp.status_code}, retrying ({attempt + 1}/3)")
                    time.sleep(1)
                    continue
                return False
            except requests.ConnectionError:
                self.log(f"{sdk_name} unit {unit_index}: publish connection error, retrying ({attempt + 1}/3)")
                time.sleep(1)
            except requests.RequestException:
                return False
        return False

    def wait_for_metrics(self) -> None:
        if self.dry_run:
            print(f"\n{Colors.YELLOW}[dry-run]{Colors.RESET} Skipping metrics polling")
            return

        active_sdks = {k: v for k, v in self.sdk_results.items() if v.get("status") == "ok"}
        if not active_sdks:
            print("\nNo active SDKs to verify metrics for")
            return

        total_expected_exposures = sum(r["exposures_sent"] for r in active_sdks.values())
        if total_expected_exposures == 0:
            return

        rc, _ = run_abs(["experiments", "request-update", str(self.experiment_id)], self.profile, output_json=False)
        if rc == 0:
            self.log("Requested analysis update")

        print(f"\nWaiting for metrics (timeout: {self.timeout}s)...")

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            metrics = self._fetch_metrics(deadline)
            if metrics:
                total_participants = self._extract_total_participants(metrics)
                if total_participants >= total_expected_exposures and self._sentinels_landed(deadline):
                    # The full case check runs once, in verify_metrics, after the poll.
                    print(f"  {Colors.GREEN}Metrics ready{Colors.RESET} ({total_participants} participants)")
                    return
                if total_participants > 0:
                    self.log(f"Participants so far: {total_participants}/{total_expected_exposures}")
            time.sleep(min(5, max(0, deadline - time.time())))

        # Metrics never reached the expected total within the timeout. This is a
        # real failure (events did not land on the backend), not a soft note —
        # verify_metrics() will see the shortfall and fail the run.
        final = self._fetch_metrics(deadline)
        final_participants = self._extract_total_participants(final) if final else 0
        print(
            f"  {Colors.RED}Metrics incomplete{Colors.RESET}: "
            f"{final_participants}/{total_expected_exposures} participants after {self.timeout}s"
        )

    def _sentinels_landed(self, deadline: float) -> bool:
        """One cheap query per SDK: the batch case's goal count, the last case sent."""
        for sdk in self.sdks:
            if time.time() >= deadline:
                return False
            if self.case_results.get(sdk, {}).get("batch", {}).get("status") != "sent":
                continue
            batch = next(c for c in case_specs(self.run_id, sdk) if c["name"] == "batch")
            metric = self._query_metric(self.case_metric_ids["events"], sdk, "batch", deadline=deadline)
            if not matches(batch["expected"]["events"], measured_total(metric, "count")):
                return False
        return True

    def _fetch_metrics(self, deadline: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not self.experiment_id:
            return None

        rc, output = run_abs([
            "experiments", "metrics", "results", str(self.experiment_id),
            "--metric", str(self.metric_id),
            "--filter", json.dumps(eq_filter(e2e_case="baseline")),
        ], self.profile, deadline=deadline)

        if rc != 0:
            self.log(f"Metrics fetch failed: {output}")
            return None

        data = parse_json_output(output)
        if data and isinstance(data, list) and data:
            return data[0]
        return None

    def _extract_total_participants(self, metrics: Any) -> int:
        if not metrics:
            return 0

        if isinstance(metrics, dict):
            variants = metrics.get("variants", [])
            return sum(v.get("unit_count", 0) for v in variants)

        if isinstance(metrics, list):
            total = 0
            for metric in metrics:
                variants = metric.get("variants", [])
                total += sum(v.get("unit_count", 0) for v in variants)
            return total

        return 0

    def _fetch_metrics_for_sdk(self, sdk_name: str) -> Optional[Dict[str, Any]]:
        return self._query_metric(self.metric_id, sdk_name)

    def verify_metrics(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {
            "run_id": self.run_id,
            "experiment_name": self.experiment_name,
            "experiment_id": self.experiment_id,
            "sdks": {},
            "overall_pass": True,
        }

        # Partition SDKs. skipped/error rows are terminal; only "ok" SDKs get
        # verified against the backend.
        active: Dict[str, Dict[str, Any]] = {}
        for sdk_name, sdk_result in self.sdk_results.items():
            status = sdk_result.get("status")
            if status == "skipped":
                results["sdks"][sdk_name] = {
                    "status": "skipped",
                    "exposures": {"expected": 0, "actual": 0, "pass": True},
                    "goals": {"expected": 0, "actual": 0, "pass": True},
                    "revenue": {"expected": 0, "actual": 0, "pass": True},
                }
                continue

            if status == "error":
                results["sdks"][sdk_name] = {
                    "status": "error",
                    "error": sdk_result.get("error"),
                    "errors": self._error_counts(sdk_result),
                    "exposures": {"expected": 0, "actual": 0, "pass": False},
                    "goals": {"expected": 0, "actual": 0, "pass": False},
                    "revenue": {"expected": 0, "actual": 0, "pass": False},
                }
                results["overall_pass"] = False
                continue

            if self.dry_run:
                results["sdks"][sdk_name] = {
                    "status": "dry_run",
                    "exposures": {"expected": sdk_result["planned_exposures"], "actual": 0, "pass": True},
                    "goals": {"expected": sdk_result["planned_goals"], "actual": 0, "pass": True},
                    "revenue": {"expected": sdk_result["planned_goals"] * 10, "actual": 0, "pass": True},
                }
                continue

            active[sdk_name] = sdk_result

        if not self.dry_run and self.experiment_id:
            self._verify_active_sdks(results, active)
            self.verify_event_cases(results)

        # An all-skipped (or otherwise all-inactive) run verifies nothing: every
        # wrapper returning 501 when the e2e env is unset yields skipped rows
        # that each carry pass:True, so without this guard overall_pass stays
        # True and the run exits 0 with zero coverage. If SDKs were under test
        # but none are active, fail loudly.
        if not self.dry_run and self.sdk_results and not active:
            results["overall_pass"] = False
            n = len(self.sdk_results)
            n_skipped = sum(1 for r in self.sdk_results.values() if r.get("status") == "skipped")
            n_errored = sum(1 for r in self.sdk_results.values() if r.get("status") == "error")
            results["skip_reason"] = (
                f"all {n} SDK(s) skipped/errored "
                f"({n_skipped} skipped, {n_errored} errored) — nothing verified"
            )
            print(
                f"  {Colors.RED}No active SDKs{Colors.RESET}: nothing was "
                f"verified against the backend; failing the run"
            )

        if not self.dry_run and (not self.experiment_id or not self.sdk_results):
            results["overall_pass"] = False
        return results

    def _error_counts(self, sdk_result: Dict[str, Any]) -> Dict[str, int]:
        return {
            "context": sdk_result.get("context_errors", 0),
            "treatment": sdk_result.get("treatment_errors", 0),
            "track": sdk_result.get("track_errors", 0),
        }

    def _verify_active_sdks(self, results, active):
        for sdk, planned in active.items():
            counts = self._query_metric(self.metric_id, sdk)
            revenue = self._query_metric(self.case_metric_ids["amount"], sdk)
            expectations = {"exposures": planned["planned_exposures"],
                            "goals": planned["planned_goals"],
                            "revenue": planned["planned_goals"] * 10}
            actuals = {"exposures": measured_total(counts, "unit_count"),
                       "goals": measured_total(counts, "count"),
                       "revenue": measured_total(revenue, "count")}
            row = {"verified": "per-sdk", "errors": self._error_counts(planned)}
            for key, expected in expectations.items():
                actual = actuals[key]
                row[key] = {"expected": expected, "actual": float(actual) if actual is not None else None,
                            "pass": matches(expected, actual)}
            passed = all(row[key]["pass"] for key in expectations) and not any(row["errors"].values())
            row["status"] = "ok" if passed else "fail"
            results["sdks"][sdk] = row
            if not passed:
                results["overall_pass"] = False

    def print_report(self, results: Dict[str, Any]) -> None:
        sdks = results.get("sdks", {})
        if not sdks:
            print("\nNo results to report.")
            return

        col_sdk = 17
        col_data = 22
        header = (
            f"{'SDK':<{col_sdk}}"
            f"{'Exposures':<{col_data}}"
            f"{'Goals':<{col_data}}"
            f"{'Revenue':<{col_data}}"
        )
        separator = f"{'-' * col_sdk}{'-' * col_data}{'-' * col_data}{'-' * col_data}"

        print(f"\n{Colors.BOLD}E2E Results: {results.get('experiment_name', 'unknown')}{Colors.RESET}\n")
        print(f"  {header}")
        print(f"  {separator}")

        for sdk_name, sdk_data in sdks.items():
            if sdk_data.get("status") == "skipped":
                print(
                    f"  {sdk_name:<{col_sdk}}"
                    f"{Colors.YELLOW}{'SKIP (no e2e)':<{col_data}}{Colors.RESET}"
                    f"{Colors.YELLOW}{'SKIP':<{col_data}}{Colors.RESET}"
                    f"{Colors.YELLOW}{'SKIP':<{col_data}}{Colors.RESET}"
                )
                continue

            if sdk_data.get("status") == "error":
                error_msg = (sdk_data.get("error") or "error")[:18]
                print(
                    f"  {sdk_name:<{col_sdk}}"
                    f"{Colors.RED}{'ERR: ' + error_msg:<{col_data}}{Colors.RESET}"
                    f"{Colors.RED}{'-':<{col_data}}{Colors.RESET}"
                    f"{Colors.RED}{'-':<{col_data}}{Colors.RESET}"
                )
                continue

            exp = sdk_data["exposures"]
            goal = sdk_data["goals"]
            rev = sdk_data["revenue"]

            exp_str = self._format_metric(exp)
            goal_str = self._format_metric(goal)
            rev_str = self._format_metric(rev)

            print(f"  {sdk_name:<{col_sdk}}{exp_str:<{col_data}}{goal_str:<{col_data}}{rev_str:<{col_data}}")

            note = sdk_data.get("note")
            if note:
                print(f"  {Colors.YELLOW}  ↳ {note}{Colors.RESET}")

        print(f"  {separator}")

        print()

        for sdk, cases in results.get("event_cases", {}).items():
            for name, result in cases.items():
                print(f"  {sdk} / {name}: {result['status'].upper()} {result.get('reason', '')}")
                for error in result.get("arrival", {}).get("errors", []):
                    print(f"    arrival: {error}")
                for metric, value in result.get("metrics", {}).items():
                    print(f"    {metric}: {value['actual']}/{value['expected']} "
                          f"{'PASS' if value['pass'] else 'FAIL'}")

        if results.get("overall_pass"):
            print(f"  {Colors.GREEN}PASS{Colors.RESET} All metrics verified")
        else:
            print(f"  {Colors.RED}FAIL{Colors.RESET} Some metrics did not match")
        print()

    def _format_metric(self, metric: Dict[str, Any]) -> str:
        # Unverified rows are neither PASS nor FAIL: the backend never gave us a
        # trustworthy actual, so render them distinctly instead of as a green ✓.
        if metric.get("unverified") or metric.get("actual") is None:
            return f"{Colors.RED}?/{metric['expected']} FAIL (unmeasurable){Colors.RESET}"
        actual = metric["actual"]
        expected = metric["expected"]
        passed = metric.get("pass", False)
        color = Colors.GREEN if passed else Colors.RED
        label = "PASS" if passed else "FAIL"
        if metric.get("derived"):
            label += " (derived)"
        return f"{color}{actual}/{expected} {label}{Colors.RESET}"

    def cleanup_experiment(self) -> None:
        if self.session_experiment_id:
            parent = self.experiment_id
            self.experiment_id, self.session_experiment_id = self.session_experiment_id, None
            try:
                self.cleanup_experiment()
            finally:
                self.experiment_id = parent
        if not self.experiment_id or self.dry_run:
            return

        print(f"Cleaning up experiment {self.experiment_id}...")
        profile_flag = f"--profile {self.profile}" if self.profile else ""
        exp_id = self.experiment_id
        # `pipefail` so a failing `abs experiments stop` (masked otherwise by the
        # archive stage's exit code) is reported. self.profile is validated as a
        # strict identifier at startup, so interpolation here is safe.
        result = subprocess.run(
            ["bash", "-o", "pipefail", "-c",
             f"echo {exp_id} | abs experiments stop {profile_flag} | abs experiments archive {profile_flag}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  {Colors.GREEN}Archived{Colors.RESET}")
        else:
            print(
                f"  {Colors.RED}Cleanup failed{Colors.RESET} (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )


def cleanup_stale_experiments(profile: str, verbose: bool = False) -> None:
    print("Cleaning up stale e2e experiments...")
    profile = validate_profile(profile)
    profile_flag = f"--profile {profile}" if profile else ""

    def run_pipeline(pipeline: str) -> subprocess.CompletedProcess:
        # `pipefail` so a failing `abs experiments stop` mid-pipeline is not
        # masked by the exit code of the final `archive` stage. profile is
        # validated above, so it is safe to interpolate into the command.
        return subprocess.run(
            ["bash", "-o", "pipefail", "-c", pipeline],
            capture_output=True, text=True,
        )

    result = run_pipeline(
        f"abs experiments list --search e2e- --state running,ready {profile_flag} "
        f"| abs experiments stop {profile_flag} | abs experiments archive {profile_flag}"
    )
    if result.returncode != 0:
        print(
            f"  {Colors.RED}Cleanup pipeline failed{Colors.RESET} "
            f"(rc={result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    elif result.stdout.strip():
        archived = result.stdout.strip().count("archived")
        print(f"  {Colors.GREEN}Archived {archived} experiment(s){Colors.RESET}")
    else:
        print("  No stale experiments found")

    result = run_pipeline(
        f"abs experiments list --search e2e- --state stopped {profile_flag} "
        f"| abs experiments archive {profile_flag}"
    )
    if result.returncode != 0:
        print(
            f"  {Colors.RED}Stopped-cleanup pipeline failed{Colors.RESET} "
            f"(rc={result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    elif result.stdout.strip():
        archived = result.stdout.strip().count("archived")
        print(f"  {Colors.GREEN}Archived {archived} stopped experiment(s){Colors.RESET}")
    print()


def discover_sdks() -> Dict[str, str]:
    sdk_urls_override = os.getenv("SDK_URLS_OVERRIDE", "")
    if sdk_urls_override:
        sdks = {}
        for entry in sdk_urls_override.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if "=" not in entry:
                raise ValueError(
                    f"Malformed SDK_URLS_OVERRIDE entry {entry!r}: expected "
                    "'name=url' (comma-separated)"
                )
            name, url = entry.split("=", 1)
            sdks[name.strip()] = url.strip()
        if sdks:
            return sdks

    sdk_services_env = os.getenv("SDK_SERVICES", "")
    if not sdk_services_env:
        print("Error: SDK_SERVICES environment variable is not set.")
        print("Set it to a comma-separated list of SDK names (e.g., javascript,python,ruby)")
        sys.exit(1)

    sdk_names = [s.strip() for s in sdk_services_env.split(",") if s.strip()]
    return {name: f"http://{name}-sdk:3000" for name in sdk_names}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run cross-SDK end-to-end tests against the ABsmartly API")
    parser.add_argument("--sdk", type=str, help="Comma-separated list of SDKs to test")
    parser.add_argument("--units", type=int,
                        default=int(os.getenv("ABSMARTLY_E2E_UNITS", "100")),
                        help="Number of units per SDK (default: 100)")
    parser.add_argument("--profile", type=str,
                        default=os.getenv("ABSMARTLY_E2E_PROFILE", "e2e"),
                        help="ABsmartly CLI profile (default: e2e)")
    parser.add_argument("--timeout", type=int,
                        default=int(os.getenv("ABSMARTLY_E2E_TIMEOUT", "300")),
                        help="Timeout in seconds for metrics polling (default: 300)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without creating real experiments")
    parser.add_argument("--cleanup", action="store_true",
                        help="Archive stale e2e experiments and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show verbose debug output")

    args = parser.parse_args()

    if args.cleanup:
        cleanup_stale_experiments(args.profile, verbose=args.verbose)
        return

    all_sdks = discover_sdks()

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

    config = {
        "profile": args.profile,
        "units": args.units,
        "timeout": args.timeout,
        "verbose": args.verbose,
        "dry_run": args.dry_run,
    }

    runner = E2ERunner(sdks, config)
    results = runner.run()

    if not results.get("overall_pass", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
