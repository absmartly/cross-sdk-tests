#!/usr/bin/env python3
import argparse
import base64
import hashlib
from collections import Counter
from decimal import Decimal
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


# Pass iff expected <= actual <= expected * OVER_PUBLISH_TOLERANCE. The small
# allowance absorbs publish retries; anything above it is treated as an
# over-publish (duplicate exposures/goals) and fails.
OVER_PUBLISH_TOLERANCE = 1.05

# Revenue metric on the `purchase` goal: goal_property summing the `amount`
# property each tracked purchase carries. Its per-variant `count` is the summed
# amount, so revenue is MEASURED by the backend, never computed from goals.
REVENUE_METRIC_NAME = "e2e_purchase_revenue"
REVENUE_PROPERTY = "amount"
# Amount sent with every tracked `purchase` (see _run_sdk_scenario). Only the
# PLANNED revenue (planned goals x this) is computed; the actual is measured.
PURCHASE_AMOUNT = 10

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
        self.revenue_metric_id: Optional[str] = None
        self.case_metric_ids: Dict[str, str] = {}
        self.case_results: Dict[str, Dict[str, Any]] = {}
        with open(os.path.join(os.path.dirname(__file__), "expected-capabilities.json")) as src:
            self.capability_policy = json.load(src)
        self.session_experiment_id: Optional[int] = None
        self.session_experiment_name: Optional[str] = None
        self.case_experiment_id: Optional[int] = None
        self.case_experiment_name: Optional[str] = None
        self.experiment_suffix: Optional[str] = None
        # Every side experiment created, so cleanup archives it even after a later failure.
        self.side_experiment_ids: List[int] = []
        self.event_cases_expected = False
        self.case_run_deadline: Optional[float] = None

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

        self.event_cases_expected = True
        try:
            self.ensure_resources()
            if not self.dry_run:
                self._ensure_case_metrics()
            self.create_experiment()
            self.start_experiment()
            self.create_case_experiments()
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
        self._ensure_revenue_metric()
        print(
            f"  {Colors.GREEN}Resources ready{Colors.RESET} app={self.app_id} "
            f"unit_type={self.unit_type} owner={self.owner_id} "
            f"metric={self.metric_id} revenue_metric={self.revenue_metric_id}"
        )

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

    def _ensure_revenue_metric(self) -> None:
        """Find or create the goal_property revenue metric on `purchase`.

        Created with `--goal purchase` (the goal by name; --goal-id is
        deprecated for metrics create) and `--value-source-property amount`.
        It is deliberately NOT attached to the experiment: `experiments
        metrics results <exp> --metric <id>` returns any metric's result
        whether or not the experiment lists it (verified live on test-1), so
        the experiment keeps e2e_purchase_count as its only metric.
        """
        self.revenue_metric_id = self._find_resource("metrics", REVENUE_METRIC_NAME)
        if self.revenue_metric_id:
            self.log(f"Found metric '{REVENUE_METRIC_NAME}' with id={self.revenue_metric_id}")
            return

        rc, output = run_abs([
            "metrics", "create",
            "--name", REVENUE_METRIC_NAME,
            "--type", "goal_property",
            "--goal", "purchase",
            "--value-source-property", REVENUE_PROPERTY,
            "--owner", self.owner_id,
            "--description", "E2E test metric summing the purchase goal amount property",
        ], self.profile)

        if rc != 0 and "already exists" in output:
            self.revenue_metric_id = self._find_resource("metrics", REVENUE_METRIC_NAME)
            if self.revenue_metric_id:
                return
        if rc != 0:
            raise RuntimeError(f"Failed to create revenue metric: {output}")

        self.revenue_metric_id = _extract_id(output)
        if not self.revenue_metric_id:
            raise RuntimeError(f"Could not determine revenue metric ID from output: {output}")

        for cmd in ["request", "approve"]:
            run_abs(["metrics", "review", cmd, self.revenue_metric_id], self.profile, output_json=False)
        run_abs(["metrics", "activate", self.revenue_metric_id, "--reason", "E2E testing"], self.profile, output_json=False)

        self.log(f"Created and activated metric '{REVENUE_METRIC_NAME}' with id={self.revenue_metric_id}")

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

    def create_case_experiments(self):
        """Two run-scoped side experiments, so case units never touch the baseline experiment.

        `-cases` (user_id) carries every case's user exposure and goals; `-session`
        (session_id) carries the multiple-units case's second exposure.
        """
        if self.dry_run:
            return
        session_type = self._find_resource("units", "session_id")
        if not session_type:
            rc, output = run_abs(["units", "create", "--name", "session_id",
                                  "--description", "E2E multi-unit collector check"], self.profile)
            session_type = _extract_id(output) if rc == 0 else None
            if not session_type:
                raise RuntimeError(f"Cannot provision session_id: {output}")
        self.case_experiment_id, self.case_experiment_name = self._create_side_experiment("cases", self.unit_type)
        self.session_experiment_id, self.session_experiment_name = self._create_side_experiment("session", session_type)

    def _create_side_experiment(self, suffix, unit_type):
        parent = self.experiment_id, self.experiment_name, self.unit_type
        created = None
        try:
            self.unit_type = unit_type
            self.experiment_suffix = suffix
            self.experiment_id = None
            self.create_experiment()
            created = (self.experiment_id, self.experiment_name) if self.experiment_id else None
        finally:
            self.experiment_id, self.experiment_name, self.unit_type = parent
            self.experiment_suffix = None
            if created:
                self.side_experiment_ids.append(created[0])
        if not created:
            raise RuntimeError(f"The {suffix} experiment was not created")
        rc, output = run_abs(["experiments", "start", str(created[0])], self.profile)
        if rc:
            raise RuntimeError(f"Start {suffix} experiment: {output}")
        try:
            self.experiment_id = created[0]
            if not self._wait_for_experiment_in_context():
                raise RuntimeError(f"The {suffix} experiment is not visible in the collector context")
        finally:
            self.experiment_id = parent[0]
        return created

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
        # Each SDK gets its own --timeout share for sending, so one slow wrapper
        # cannot spend the budget of the SDKs after it.
        for sdk, base_url in self.sdks.items():
            self.case_run_deadline = time.time() + self.timeout
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

    def _wrapper_post(self, url, body=None, deadline=None):
        for attempt in range(3):
            # The budget bounds the calls themselves, not only the 429 waits.
            if deadline is not None and time.time() >= deadline:
                raise RuntimeError(f"Wrapper call budget exhausted before POST {url}")
            response = requests.post(url, json=body or {}, timeout=30)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt == 2 or (deadline is not None and time.time() + RATE_LIMIT_SLEEP >= deadline):
                break
            time.sleep(RATE_LIMIT_SLEEP)
        response.raise_for_status()

    def _send_event_case(self, sdk, base_url, case):
        # A rate-limited wrapper never sleeps past this case's budget, nor the run's.
        deadline = time.time() + self.timeout
        if self.case_run_deadline is not None:
            deadline = min(deadline, self.case_run_deadline)
        units = {"user_id": case["uid"]}
        if "session" in case:
            units["session_id"] = case["session"]
        payload = {"mode": "e2e", "units": units,
                   "attributes": {"e2e_sdk": sdk, "e2e_case": case["name"]}}
        if case.get("pre_ready"):
            payload["preReadyGoals"] = [{"goalName": "purchase", "properties": g} for g in case["goals"]]
        if case.get("pre_ready"):
            # Establish the unit's exposure before the pre-ready achievement:
            # analysis metrics count goals only after first exposure.
            prime = dict(payload)
            prime.pop("preReadyGoals")
            primed = self._wrapper_post(base_url + "/context", prime, deadline=deadline)["result"]["contextId"]
            prime_url = base_url + "/context/" + primed
            try:
                self._wrapper_post(prime_url + "/treatment", {"experimentName": self.case_experiment_name}, deadline=deadline)
                self._wrapper_post(prime_url + "/publish", deadline=deadline)
            finally:
                requests.delete(prime_url, timeout=10).raise_for_status()
        created = self._wrapper_post(base_url + "/context", payload, deadline=deadline)
        result = created.get("result", {})
        context_id = result.get("contextId")
        if not context_id:
            raise RuntimeError(f"Context was not created: {created}")
        url = base_url + "/context/" + context_id
        try:
            # Inside the try: a context the wrapper reports as not ready is still deleted.
            if result.get("ready") is not True or result.get("failed"):
                raise RuntimeError(f"Context is not ready: {created}")
            if case.get("pre_ready") and result.get("preReadyTracked") is not True:
                raise RuntimeError("Wrapper did not confirm tracking in the SDK's not-ready state")
            self._wrapper_post(url + "/treatment", {"experimentName": self.case_experiment_name}, deadline=deadline)
            if "session" in case:
                self._wrapper_post(url + "/treatment", {"experimentName": self.session_experiment_name}, deadline=deadline)
            if not case.get("pre_ready"):
                for properties in case["goals"]:
                    body = {"goalName": "purchase"}
                    if not case.get("omit_properties"):
                        body["properties"] = properties
                    self._wrapper_post(url + "/track", body, deadline=deadline)
            # Exactly one publish for the batch; close case must never publish explicitly.
            self._wrapper_post(url + ("/finalize" if case.get("close") else "/publish"), deadline=deadline)
        finally:
            requests.delete(url, timeout=10).raise_for_status()

    def _query_metric(self, metric_id, sdk, case, experiment_id=None, deadline=None):
        rc, output = run_abs([
            "experiments", "metrics", "results", str(experiment_id or self.case_experiment_id),
            "--metric", str(metric_id), "--filter", json.dumps(eq_filter(e2e_sdk=sdk, e2e_case=case)),
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
        # The case check has its own budget: on a rate-limited backend no single 429
        # sleep starts past it, so verification cannot run for hours.
        deadline = time.time() + self.timeout
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
                        cache[source] = self._query_metric(self.case_metric_ids[source], sdk, name, deadline=deadline)
                    actual = measured_total(cache[source], "unit_count" if key == "exposures" else "count")
                    metrics[key] = {"expected": str(expected), "actual": str(actual) if actual is not None else None,
                                    "pass": matches(expected, actual)}
                if "session" in case:
                    metric = self._query_metric(self.case_metric_ids["events"], sdk, name, self.session_experiment_id, deadline=deadline)
                    actual = measured_total(metric, "unit_count")
                    metrics["session_exposures"] = {"expected": "1", "actual": str(actual) if actual is not None else None,
                                                    "pass": matches(1, actual)}
                arrival = self._verify_arrival(sdk, case, deadline)
                rows[name]["arrival"] = arrival
                rows[name]["status"] = "pass" if all(m["pass"] for m in metrics.values()) and arrival["pass"] else "fail"
                if rows[name]["status"] != "pass":
                    results["overall_pass"] = False

    def _verify_arrival(self, sdk, case, deadline=None):
        units = {"user_id": case["uid"]}
        if "session" in case:
            units["session_id"] = case["session"]
        errors = []
        for unit_type, uid in units.items():
            hashed = base64.urlsafe_b64encode(hashlib.md5(str(uid).encode("utf-8")).digest()).decode().rstrip("=")
            events = []
            for offset in range(0, 5000, 500):
                rc, output = run_abs(["events", "list", "--from", "1d", "--unit-uid", hashed,
                                      "--items", "500", "--skip", str(offset)], self.profile, deadline=deadline)
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
                         and e.get("experiment_id") == (self.session_experiment_id if unit_type == "session_id" else self.case_experiment_id)]
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
                if not isinstance(attrs, dict) or attrs.get("e2e_sdk") != sdk or attrs.get("e2e_case") != case["name"]:
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
        self.experiment_name = f"e2e-{self.run_id}" + (f"-{self.experiment_suffix}" if self.experiment_suffix else "")
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
        # trivially. Expected comes from planned units below, not successes.
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
                    "attributes": {"sdk_name": sdk_name},
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
                        json={"goalName": "purchase", "properties": {REVENUE_PROPERTY: PURCHASE_AMOUNT}},
                        timeout=10,
                    )
                    if track_resp.status_code == 200:
                        goals_sent += 1
                        revenue_sent += PURCHASE_AMOUNT
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
        """One cheap query per SDK: the batch case's goal count (its largest event volume)."""
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
        """Unsegmented metric result for the e2e metric (aggregate across SDKs)."""
        if not self.experiment_id:
            return None

        rc, output = run_abs([
            "experiments", "metrics", "results", str(self.experiment_id),
        ], self.profile, **({"deadline": deadline} if deadline is not None else {}))

        if rc != 0:
            self.log(f"Metrics fetch failed: {output}")
            return None

        return self._select_metric(parse_json_output(output))

    def _select_metric(self, data: Any) -> Optional[Dict[str, Any]]:
        """Pick the e2e metric out of `abs ... metrics results -o json` output.

        With `-o json` the CLI prints a list of MetricResult objects, one per
        metric on the experiment (primary first): {metric_id, name, type,
        effect?, variants: [VariantResult]} (absmartly/cli
        src/api-client/metric-results.ts, MetricResult/VariantResult and
        fetchAllMetricResults; printed raw by src/commands/experiments/metrics.ts
        resultsCommand). Select ours by metric_id, falling back to the
        "primary" entry; never guess at an arbitrary row.
        """
        metric = self._select_metric_by_id(data, self.metric_id)
        if metric is not None:
            return metric
        for m in self._metric_list(data):
            if m.get("type") == "primary":
                return m
        return None

    @staticmethod
    def _metric_list(data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict)]

    def _select_metric_by_id(self, data: Any, metric_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """The MetricResult whose metric_id matches exactly; no fallback."""
        if metric_id is None:
            return None
        for m in self._metric_list(data):
            if str(m.get("metric_id")) == str(metric_id):
                return m
        return None

    @staticmethod
    def _revenue_total(metric: Any) -> Tuple[Optional[float], Optional[str]]:
        """Measured revenue = sum of variants[].count of the revenue metric.

        For the goal_property metric `count` (column "metric_<id>", see
        _variant_counts) is the summed `amount` property in that variant.
        Returns (total, None) or (None, reason) when any count is missing or
        non-numeric.
        """
        variants = metric.get("variants") if isinstance(metric, dict) else None
        if not isinstance(variants, list) or not variants:
            return None, "result has no variants"
        total: float = 0
        for v in variants:
            count = v.get("count") if isinstance(v, dict) else None
            if isinstance(count, bool) or not isinstance(count, (int, float)):
                return None, f"variant count is not numeric ({count!r})"
            total += count
        if isinstance(total, float) and total.is_integer():
            total = int(total)
        return total, None

    def _fetch_revenue(self, sdk_name: Optional[str] = None) -> Tuple[Optional[float], Optional[str]]:
        """Measured revenue for one SDK segment (or the aggregate when None).

        Queries `abs experiments metrics results <exp> --metric <revenue id>
        [--filter <sdk segment>]`. --metric is a real flag (absmartly/cli
        src/commands/experiments/metrics.ts resultsCommand); with it the CLI
        returns a single MetricResult for that metric id
        (src/core/experiments/metrics.ts getMetricResults), which is still
        selected by metric_id. Returns (revenue, None) or (None, reason).
        """
        if not self.revenue_metric_id:
            return None, "no revenue metric id"
        if not self.experiment_id:
            return None, "no experiment id"
        args = [
            "experiments", "metrics", "results", str(self.experiment_id),
            "--metric", str(self.revenue_metric_id),
        ]
        if sdk_name is not None:
            args += ["--filter", self.sdk_segment_filter(sdk_name)]
        rc, output = run_abs(args, self.profile)
        if rc != 0:
            first_line = (output.strip().splitlines() or [""])[0][:200]
            reason = f"query failed (abs rc={rc})"
            return None, f"{reason}: {first_line}" if first_line else reason
        data = parse_json_output(output)
        if data is None:
            return None, "output could not be parsed"
        metric = self._select_metric_by_id(data, self.revenue_metric_id)
        if metric is None:
            return None, f"no result for metric {self.revenue_metric_id}"
        return self._revenue_total(metric)

    @staticmethod
    def _variant_counts(metric: Any) -> Optional[List[Dict[str, int]]]:
        """Per-variant (variant, unit_count, count) from a MetricResult.

        Field mapping (absmartly/cli src/api-client/metric-results.ts,
        parseMetricData, lines 29-72 at 919ede3), built from the engine's
        column-oriented response:
          - unit_count <- column "unit_count": units exposed in that variant
                          (participants). Summed over variants = exposures.
          - count      <- column "metric_<id>": the metric numerator. For the
                          e2e goal_unique_count metric this is the number of
                          units in that variant that converted on `purchase`.
                          Summed over variants = goals.
          - mean       <- column "metric_<id>_mean" (= count / unit_count);
                          not compared.
        The CLI's human table (formatResultRows, lines 81-133) shows
        `samples` = the TREATMENT variant's unit_count only, plus
        `<control> count` / `<treatment> count` = per-variant `count`. So a row
        like "samples 56, control 27, treatment 23" means 56 treatment
        participants (control holds the rest) and 27+23 = 50 goals; it must
        not be read as total exposures.

        Returns None when there are no variants or any variant lacks a numeric
        unit_count/count, so the caller treats the SDK as unverified.
        """
        if not isinstance(metric, dict):
            return None
        variants = metric.get("variants")
        if not isinstance(variants, list) or not variants:
            return None
        out: List[Dict[str, int]] = []
        for v in variants:
            if not isinstance(v, dict):
                return None
            units, count = v.get("unit_count"), v.get("count")
            if not isinstance(units, (int, float)) or not isinstance(count, (int, float)):
                return None
            out.append({"variant": v.get("variant"), "unit_count": int(units), "count": int(count)})
        return out

    def _extract_total_participants(self, metrics: Any) -> int:
        """Sum of variants[].unit_count (exposed units); 0 if unavailable."""
        counts = self._variant_counts(metrics)
        return sum(v["unit_count"] for v in counts) if counts else 0

    def _extract_total_goals(self, metrics: Any) -> int:
        """Sum of variants[].count (goal_unique_count numerator); 0 if unavailable."""
        counts = self._variant_counts(metrics)
        return sum(v["count"] for v in counts) if counts else 0

    def _within_tolerance(self, expected: int, actual: Optional[int]) -> bool:
        """Pass iff expected <= actual <= expected * OVER_PUBLISH_TOLERANCE.

        Equality-with-tolerance (not actual >= expected) so a duplicate/over-
        publish is caught as a failure instead of silently passing. When nothing
        was planned, the backend must also show nothing.
        """
        if actual is None:
            return False
        if expected <= 0:
            return actual == 0
        return expected <= actual <= int(expected * OVER_PUBLISH_TOLERANCE)

    @staticmethod
    def sdk_segment_filter(sdk_name: str) -> str:
        """--filter payload selecting units whose `sdk_name` attribute == sdk_name.

        The CLI forwards --filter verbatim as `filters.segments` of
        POST /experiments/{id}/metrics/{metric_id} (absmartly/cli
        src/core/experiments/metrics.ts getMetricResults). The backend expects
        an audience JSON-expression object under a top-level "filter" key:
        {"filter": {"and": [{"eq": [{"var": {"path": "sdk_name"}}, {"value": X}]}]}}.
        Verified live on test-1 (experiment 6143): the java segment returned
        unit_count 44+56 = 100 and count 27+23 = 50, against unfiltered
        138+162 = 300 and 69+81 = 150. The earlier {"attributes": {...}} shape
        is rejected. Built with json.dumps so the SDK name is never
        string-interpolated into JSON.
        """
        return json.dumps({
            "filter": {
                "and": [
                    {"eq": [{"var": {"path": "sdk_name"}}, {"value": sdk_name}]},
                ],
            },
        })

    def _fetch_metrics_for_sdk(self, sdk_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Fetch the e2e metric segmented to one SDK via the sdk_name attribute.

        Each e2e unit is created with attribute {"sdk_name": <sdk>}. Returns
        (metric, None) on success or (None, reason) when the segmented result
        is unavailable (query rejected, unparsable, empty). The caller FAILS
        the SDK as unverified in that case; there is no aggregate fallback.
        """
        if not self.experiment_id:
            return None, "no experiment id"
        rc, output = run_abs(
            [
                "experiments", "metrics", "results", str(self.experiment_id),
                "--filter", self.sdk_segment_filter(sdk_name),
            ],
            self.profile,
        )
        if rc != 0:
            # `output` already carries the CLI stderr (see run_abs).
            print(
                f"  {Colors.RED}Segmentation query failed for {sdk_name}"
                f"{Colors.RESET} (abs rc={rc}): {output}"
            )
            first_line = (output.strip().splitlines() or [""])[0][:200]
            reason = f"segmentation query failed (abs rc={rc})"
            return None, f"{reason}: {first_line}" if first_line else reason
        data = parse_json_output(output)
        if data is None:
            print(
                f"  {Colors.RED}Segmentation output for {sdk_name} could not be "
                f"parsed{Colors.RESET}: {output[:300]}"
            )
            return None, "segmentation output could not be parsed"
        metric = self._select_metric(data)
        if metric is None:
            return None, "segmentation returned no result for the e2e metric"
        if self._variant_counts(metric) is None:
            return None, "segmented result has no per-variant unit_count/count"
        return metric, None

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
                    "revenue": {"expected": sdk_result["planned_goals"] * PURCHASE_AMOUNT, "actual": 0, "pass": True},
                }
                continue

            active[sdk_name] = sdk_result

        if not self.dry_run and self.experiment_id:
            self._verify_active_sdks(results, active)
            # run() sets this before any stage, so a run that never sends the cases
            # still checks them and fails with "Case was not executed".
            if self.event_cases_expected:
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

        # Any failing or unmeasured SDK metric fails the run, whatever path
        # produced the row.
        if not self.dry_run and self.verification_problems(results):
            results["overall_pass"] = False

        return results

    def _error_counts(self, sdk_result: Dict[str, Any]) -> Dict[str, int]:
        return {
            "context": sdk_result.get("context_errors", 0),
            "treatment": sdk_result.get("treatment_errors", 0),
            "track": sdk_result.get("track_errors", 0),
        }

    def _verify_active_sdks(self, results: Dict[str, Any], active: Dict[str, Dict[str, Any]]) -> None:
        # Aggregate backend numbers: an informational cross-check and the
        # reference for detecting a backend that ignores the segmentation filter.
        agg = self._fetch_metrics()
        agg_participants = self._extract_total_participants(agg) if agg else 0
        agg_goals = self._extract_total_goals(agg) if agg else 0
        agg_revenue, agg_revenue_reason = self._fetch_revenue()

        # First pass: per-SDK segmented results. A missing result carries the
        # reason it is unverified.
        segmented: Dict[str, Dict[str, Any]] = {}
        unverified_reason: Dict[str, str] = {}
        for sdk_name in active:
            metric, reason = self._fetch_metrics_for_sdk(sdk_name)
            if metric is None:
                unverified_reason[sdk_name] = reason or "segmentation unavailable"
            else:
                segmented[sdk_name] = metric

        # Measured revenue per segmented SDK (goal_property metric, summed
        # `amount`). An SDK whose revenue cannot be measured fails below.
        revenue: Dict[str, Any] = {}
        revenue_reason: Dict[str, str] = {}
        for sdk_name in segmented:
            value, reason = self._fetch_revenue(sdk_name)
            if value is None:
                revenue_reason[sdk_name] = reason or "unavailable"
            else:
                revenue[sdk_name] = value

        # Filter-trust sanity checks. If the backend ignores the filter, every
        # SDK comes back with the aggregate total and per-SDK "verification"
        # would pass everyone. Either condition below fails every segmented SDK.
        seg_warning: Optional[str] = None
        exp_actuals = [self._extract_total_participants(m) for m in segmented.values()]
        if len(exp_actuals) >= 2 and agg_participants > 0:
            # Planned volumes are identical across SDKs, so key off the actuals
            # collapsing onto the aggregate, not off expected variance.
            if len(set(exp_actuals)) == 1 and exp_actuals[0] == agg_participants:
                seg_warning = (
                    "every SDK returned the same participant count equal to the "
                    f"aggregate total ({agg_participants}); the backend appears "
                    "to ignore the segmentation filter"
                )
            elif sum(exp_actuals) > int(agg_participants * OVER_PUBLISH_TOLERANCE):
                seg_warning = (
                    f"sum of per-SDK participants ({sum(exp_actuals)}) exceeds the "
                    f"aggregate total ({agg_participants}) beyond tolerance; "
                    "segmentation is double-counting or the filter is ignored"
                )
        if seg_warning:
            print(f"  {Colors.RED}Segmentation unreliable{Colors.RESET}: {seg_warning}")
            for sdk_name in segmented:
                unverified_reason[sdk_name] = seg_warning

        # The same filter-trust checks on the revenue metric. A segmentation
        # already judged untrustworthy above fails the whole SDK, revenue
        # included, so these only add the revenue-specific cases.
        rev_warning: Optional[str] = None
        rev_actuals = list(revenue.values())
        if len(rev_actuals) >= 2 and agg_revenue:
            if len(set(rev_actuals)) == 1 and rev_actuals[0] == agg_revenue:
                rev_warning = (
                    "every SDK returned the same revenue equal to the aggregate "
                    f"total ({agg_revenue}); the filter appears to be ignored"
                )
            elif sum(rev_actuals) > agg_revenue * OVER_PUBLISH_TOLERANCE:
                rev_warning = (
                    f"sum of per-SDK revenue ({sum(rev_actuals)}) exceeds the "
                    f"aggregate total ({agg_revenue}) beyond tolerance"
                )
        if rev_warning:
            print(f"  {Colors.RED}Revenue segmentation unreliable{Colors.RESET}: {rev_warning}")
            for sdk_name in list(revenue):
                revenue_reason[sdk_name] = rev_warning
                del revenue[sdk_name]

        # Second pass: finalize each active SDK. Unverified is a FAIL.
        for sdk_name, sdk_result in active.items():
            expected_exposures = sdk_result["planned_exposures"]
            expected_goals = sdk_result["planned_goals"]
            # Planned amount sent; the actual is always measured.
            expected_revenue = expected_goals * PURCHASE_AMOUNT
            errors = self._error_counts(sdk_result)
            has_errors = any(errors.values())
            error_note = (
                f"wrapper errors ctx={errors['context']} "
                f"treat={errors['treatment']} track={errors['track']}"
                if has_errors else None
            )

            metric = segmented.get(sdk_name)
            variants = self._variant_counts(metric) if metric is not None else None
            actual_exposures = sum(v["unit_count"] for v in variants) if variants else None
            actual_goals = sum(v["count"] for v in variants) if variants else None

            reason = unverified_reason.get(sdk_name)
            if reason is not None:
                note = f"unverified: {reason}"
                if error_note:
                    note += f"; {error_note}"
                results["sdks"][sdk_name] = {
                    "status": "fail",
                    "verified": "none",
                    "errors": errors,
                    "note": note,
                    "variants": variants,
                    "exposures": {"expected": expected_exposures, "actual": actual_exposures, "pass": False, "unverified": True},
                    "goals": {"expected": expected_goals, "actual": actual_goals, "pass": False, "unverified": True},
                    "revenue": {"expected": expected_revenue, "actual": None, "pass": False, "unverified": True},
                }
                results["overall_pass"] = False
                continue

            exposures_pass = self._within_tolerance(expected_exposures, actual_exposures)
            goals_pass = self._within_tolerance(expected_goals, actual_goals)
            revenue_actual = revenue.get(sdk_name)
            rev_reason = revenue_reason.get(sdk_name)
            revenue_pass = rev_reason is None and self._within_tolerance(expected_revenue, revenue_actual)
            sdk_pass = exposures_pass and goals_pass and revenue_pass and not has_errors

            note = None
            if rev_reason is not None:
                note = f"unverified: revenue {rev_reason}"
            elif actual_exposures > int(expected_exposures * OVER_PUBLISH_TOLERANCE):
                note = f"over-published exposures ({actual_exposures} > {expected_exposures})"
            elif actual_goals > int(expected_goals * OVER_PUBLISH_TOLERANCE):
                note = f"over-published goals ({actual_goals} > {expected_goals})"
            elif not exposures_pass or not goals_pass:
                note = (
                    f"short counts (exposures {actual_exposures}/{expected_exposures}, "
                    f"goals {actual_goals}/{expected_goals})"
                )
            elif revenue_actual > expected_revenue * OVER_PUBLISH_TOLERANCE:
                note = f"over-published revenue ({revenue_actual} > {expected_revenue})"
            elif not revenue_pass:
                note = f"short revenue ({revenue_actual}/{expected_revenue})"
            elif has_errors:
                note = error_note
            if note and has_errors and error_note not in note:
                note += f"; {error_note}"

            results["sdks"][sdk_name] = {
                "status": "ok" if sdk_pass else "fail",
                "verified": "per-sdk",
                "errors": errors,
                "note": note,
                "variants": variants,
                "exposures": {"expected": expected_exposures, "actual": actual_exposures, "pass": exposures_pass},
                "goals": {"expected": expected_goals, "actual": actual_goals, "pass": goals_pass},
                "revenue": (
                    {"expected": expected_revenue, "actual": None, "pass": False, "unverified": True}
                    if rev_reason is not None else
                    {"expected": expected_revenue, "actual": revenue_actual, "pass": revenue_pass}
                ),
            }
            if not sdk_pass:
                results["overall_pass"] = False

        # Aggregate cross-check, informational only. Expected comes from
        # PLANNED units. The unsegmented aggregate is unreliable on this backend
        # (it can report 0 while per-SDK segmentation returns real counts), and
        # every SDK is already gated on its own segmented result above.
        total_expected = sum(r["planned_exposures"] for r in active.values())
        total_goals_expected = sum(r["planned_goals"] for r in active.values())
        total_revenue_expected = total_goals_expected * PURCHASE_AMOUNT
        agg_pass = (
            total_expected > 0
            and self._within_tolerance(total_expected, agg_participants)
            and self._within_tolerance(total_goals_expected, agg_goals)
            and self._within_tolerance(total_revenue_expected, agg_revenue)
        )
        results["aggregate"] = {
            "expected_participants": total_expected,
            "actual_participants": agg_participants,
            "expected_goals": total_goals_expected,
            "actual_goals": agg_goals,
            "expected_revenue": total_revenue_expected,
            "actual_revenue": agg_revenue,
            "revenue_note": agg_revenue_reason,
            "pass": agg_pass,
        }

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

        total_exp_expected = 0
        total_exp_actual = 0
        total_goal_expected = 0
        total_goal_actual = 0
        total_rev_expected = 0
        total_rev_actual = 0
        # Set when at least one verified row was excluded from the actual totals
        # because it was unverified — the TOTAL actual is then a lower bound.
        total_partially_unverified = False

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

            total_exp_expected += exp["expected"]
            total_goal_expected += goal["expected"]
            total_rev_expected += rev["expected"]
            # Only sum actuals we actually verified. Unverified rows carry no
            # trustworthy actual, so excluding them keeps the TOTAL honest.
            if any(m.get("unverified") or m.get("actual") is None for m in (exp, goal, rev)):
                total_partially_unverified = True
            else:
                total_exp_actual += exp.get("actual") or 0
                total_goal_actual += goal.get("actual") or 0
                total_rev_actual += rev.get("actual") or 0

            exp_str = self._format_metric(exp)
            goal_str = self._format_metric(goal)
            rev_str = self._format_metric(rev)

            print(f"  {sdk_name:<{col_sdk}}{exp_str:<{col_data}}{goal_str:<{col_data}}{rev_str:<{col_data}}")

            note = sdk_data.get("note")
            if note:
                note_color = Colors.RED if sdk_data.get("status") == "fail" else Colors.YELLOW
                print(f"  {note_color}  ↳ {note}{Colors.RESET}")

            # Show both compared columns per variant so a mismatch between
            # exposed units (unit_count) and the goal numerator (count) is
            # visible; see _variant_counts for the field mapping.
            variants = sdk_data.get("variants")
            if variants:
                parts = ", ".join(
                    f"v{v['variant']} participants={v['unit_count']} goals={v['count']}"
                    for v in variants
                )
                print(f"    ↳ per variant: {parts}")

        print(f"  {separator}")

        total_exp = self._format_total(total_exp_expected, total_exp_actual, total_partially_unverified)
        total_goal = self._format_total(total_goal_expected, total_goal_actual, total_partially_unverified)
        total_rev = self._format_total(total_rev_expected, total_rev_actual, total_partially_unverified)

        print(f"  {Colors.BOLD}{'TOTAL':<{col_sdk}}{Colors.RESET}{total_exp:<{col_data}}{total_goal:<{col_data}}{total_rev:<{col_data}}")
        if total_partially_unverified:
            print(f"  {Colors.YELLOW}(TOTAL actual excludes unverified SDKs — lower bound){Colors.RESET}")
        print()

        agg = results.get("aggregate")
        if agg:
            # Per-SDK segmentation is the only gate; the unsegmented aggregate
            # is informational (unreliable on this backend).
            print(
                f"  {Colors.YELLOW}Backend aggregate (info): participants "
                f"{agg['actual_participants']}/{agg['expected_participants']}, goals "
                f"{agg['actual_goals']}/{agg['expected_goals']}, revenue "
                f"{'?' if agg.get('actual_revenue') is None else agg['actual_revenue']}"
                f"/{agg.get('expected_revenue')}{Colors.RESET}"
            )
            print()

        for sdk, cases in results.get("event_cases", {}).items():
            for name, result in cases.items():
                print(f"  {sdk} / {name}: {result['status'].upper()} {result.get('reason', '')}")
                for error in result.get("arrival", {}).get("errors", []):
                    print(f"    arrival: {error}")
                for metric, value in result.get("metrics", {}).items():
                    print(f"    {metric}: {value['actual']}/{value['expected']} "
                          f"{'PASS' if value['pass'] else 'FAIL'}")
        print()

        if self.dry_run:
            print(f"  {Colors.YELLOW}DRY-RUN{Colors.RESET} Nothing was verified against the backend")
        elif self.report_passes(results):
            print(f"  {Colors.GREEN}PASS{Colors.RESET} All metrics verified")
        else:
            print(f"  {Colors.RED}FAIL{Colors.RESET} Some metrics did not match or could not be verified")
            for problem in self.verification_problems(results):
                print(f"  {Colors.RED}  - {problem}{Colors.RESET}")
        print()

    @staticmethod
    def report_passes(results: Dict[str, Any]) -> bool:
        """PASS only if the run passed AND every SDK row was measured and passed.

        Gates both the PASS banner and the exit code. Computed from the rows as
        well as overall_pass, so a path that forgets to clear overall_pass can
        never print PASS (or exit 0) over an unverified or failing SDK.
        """
        if not results.get("overall_pass"):
            return False
        if E2ERunner.verification_problems(results):
            return False
        return any(row.get("status") == "ok" for row in results.get("sdks", {}).values())

    @staticmethod
    def verification_problems(results: Dict[str, Any]) -> List[str]:
        """Every SDK row that is not measured-and-passing, as readable lines.

        Skipped SDKs carry no metrics (an all-skipped run already fails via
        overall_pass). Every other row needs status "ok" and a measured,
        passing exposures, goals and revenue figure. Dry-run rows measure
        nothing, so they count as problems too.
        """
        problems: List[str] = []
        for sdk_name, row in results.get("sdks", {}).items():
            status = row.get("status")
            if status == "skipped":
                continue
            if status == "error":
                problems.append(f"{sdk_name}: wrapper error ({row.get('error') or 'unknown'})")
                continue
            if status == "dry_run":
                problems.append(f"{sdk_name}: dry run, nothing measured")
                continue
            row_problems: List[str] = []
            for key in ("exposures", "goals", "revenue"):
                metric = row.get(key) or {}
                if metric.get("unverified") or metric.get("actual") is None:
                    row_problems.append(f"{sdk_name} {key}: UNVERIFIED (expected {metric.get('expected')})")
                elif not metric.get("pass", False):
                    row_problems.append(f"{sdk_name} {key}: {metric.get('actual')}/{metric.get('expected')} FAIL")
            if status != "ok" and not row_problems:
                row_problems.append(f"{sdk_name}: {row.get('note') or status}")
            problems.extend(row_problems)
        for sdk_name, cases in results.get("event_cases", {}).items():
            for name, row in cases.items():
                if row.get("status") not in ("pass", "unsupported"):
                    problems.append(f"{sdk_name} event case {name}: {row.get('status')} {row.get('reason', '')}".rstrip())
        return problems

    def _format_metric(self, metric: Dict[str, Any]) -> str:
        # Unverified rows FAIL: the backend never gave a trustworthy per-SDK
        # actual (any number shown came from an untrusted segmentation).
        if metric.get("unverified") or metric.get("actual") is None:
            shown = "?" if metric.get("actual") is None else metric["actual"]
            return f"{Colors.RED}{shown}/{metric['expected']} FAIL (unverified){Colors.RESET}"
        actual = metric["actual"]
        expected = metric["expected"]
        passed = metric.get("pass", False)
        color = Colors.GREEN if passed else Colors.RED
        label = "PASS" if passed else "FAIL"
        return f"{color}{actual}/{expected} {label}{Colors.RESET}"

    def _format_total(self, expected: int, actual: int, partial: bool) -> str:
        # With unverified SDKs excluded, `actual` is a lower bound, so an equality
        # gate would be misleading; require only actual >= expected here and flag
        # the caveat separately.
        passed = actual >= expected if partial else self._within_tolerance(expected, actual)
        color = Colors.YELLOW if partial else (Colors.GREEN if passed else Colors.RED)
        label = "PASS" if passed else "FAIL"
        if partial:
            label += "*"
        return f"{color}{actual}/{expected} {label}{Colors.RESET}"

    def cleanup_experiment(self) -> None:
        parent = self.experiment_id
        for side_id in self.side_experiment_ids:
            self.experiment_id = side_id
            try:
                self._archive_experiment()
            finally:
                self.experiment_id = parent
        self.side_experiment_ids = []
        self._archive_experiment()

    def _archive_experiment(self) -> None:
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

    # Exit 0 only when every SDK was measured and passed. A dry run measures
    # nothing and prints DRY-RUN (never PASS); it exits 0 unless a wrapper errored.
    if config["dry_run"]:
        if not results.get("overall_pass"):
            sys.exit(1)
    elif not E2ERunner.report_passes(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
