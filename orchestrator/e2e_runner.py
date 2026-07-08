#!/usr/bin/env python3
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


# Pass iff expected <= actual <= expected * OVER_PUBLISH_TOLERANCE. The small
# allowance absorbs publish retries; anything above it is treated as an
# over-publish (duplicate exposures/goals) and fails.
OVER_PUBLISH_TOLERANCE = 1.05

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


def run_abs(args: List[str], profile: str = "", api_key: str = "", endpoint: str = "", output_json: bool = True) -> Tuple[int, str]:
    cmd = ["abs"] + args
    if api_key and endpoint:
        cmd += ["--api-key", api_key, "--endpoint", endpoint]
    elif profile:
        cmd += ["--profile", profile]
    if output_json:
        cmd += ["-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
            self.run_sdk_scenarios()
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

    def create_experiment(self) -> None:
        self.experiment_name = f"e2e-{self.run_id}"
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

        start_time = time.time()
        while time.time() - start_time < self.timeout:
            metrics = self._fetch_metrics()
            if metrics:
                total_participants = self._extract_total_participants(metrics)
                if total_participants >= total_expected_exposures:
                    print(f"  {Colors.GREEN}Metrics ready{Colors.RESET} ({total_participants} participants)")
                    return
                if total_participants > 0:
                    self.log(f"Participants so far: {total_participants}/{total_expected_exposures}")
            time.sleep(5)

        # Metrics never reached the expected total within the timeout. This is a
        # real failure (events did not land on the backend), not a soft note —
        # verify_metrics() will see the shortfall and fail the run.
        final = self._fetch_metrics()
        final_participants = self._extract_total_participants(final) if final else 0
        print(
            f"  {Colors.RED}Metrics incomplete{Colors.RESET}: "
            f"{final_participants}/{total_expected_exposures} participants after {self.timeout}s"
        )

    def _fetch_metrics(self) -> Optional[Dict[str, Any]]:
        if not self.experiment_id:
            return None

        rc, output = run_abs([
            "experiments", "metrics", "results", str(self.experiment_id),
        ], self.profile)

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

    def _extract_total_goals(self, metrics: Any) -> int:
        """Goal/conversion count from a goal_unique_count metric result.

        Per the metric-results schema, each VariantResult carries `unit_count`
        (participants) and `count` (the metric numerator — for a
        goal_unique_count metric this is the number of converting units). Sum
        `count` across variants, with fallbacks for older field names.
        """
        if not metrics:
            return 0

        metric_list = metrics if isinstance(metrics, list) else [metrics]
        total = 0
        for metric in metric_list:
            if not isinstance(metric, dict):
                continue
            for variant in metric.get("variants", []):
                if not isinstance(variant, dict):
                    continue
                for field in ("count", "goal_count", "numerator", "conversion_count"):
                    val = variant.get(field)
                    if isinstance(val, (int, float)):
                        total += int(val)
                        break
        return total

    def _extract_total_revenue(self, metrics: Any) -> Optional[float]:
        """Revenue/value figure from a metric result, if the backend reports one.

        The e2e metric is a goal_unique_count, which usually carries no monetary
        value, so this returns None in the common case and the caller derives
        revenue from goal count instead (labelled "derived"). If a
        revenue/value/sum field is ever present, it is summed across variants so
        revenue can be verified rather than assumed.
        """
        if not metrics:
            return None

        metric_list = metrics if isinstance(metrics, list) else [metrics]
        total = 0.0
        found = False
        for metric in metric_list:
            if not isinstance(metric, dict):
                continue
            for variant in metric.get("variants", []):
                if not isinstance(variant, dict):
                    continue
                for field in ("revenue", "value", "value_sum", "sum", "total_value"):
                    val = variant.get(field)
                    if isinstance(val, (int, float)):
                        total += float(val)
                        found = True
                        break
        return total if found else None

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

    def _fetch_metrics_for_sdk(self, sdk_name: str) -> Optional[Dict[str, Any]]:
        """Fetch metrics segmented to a single SDK via the sdk_name attribute.

        Each e2e unit is created with attribute {"sdk_name": <sdk>}, so a filter
        on that attribute gives per-SDK participant/goal counts. Returns None if
        segmentation is unavailable (older backend / no segment support), in which
        case the caller falls back to aggregate-only verification.
        """
        if not self.experiment_id:
            return None
        seg_filter = json.dumps({"attributes": {"sdk_name": sdk_name}})
        rc, output = run_abs(
            [
                "experiments", "metrics", "results", str(self.experiment_id),
                "--filter", seg_filter,
            ],
            self.profile,
        )
        if rc != 0:
            # Always surface segmentation failures (not just under --verbose):
            # a silent None here is what let fabricated per-SDK passes through.
            # `output` already carries the CLI stderr (see run_abs).
            print(
                f"  {Colors.RED}Segmentation query failed for {sdk_name}"
                f"{Colors.RESET} (abs rc={rc}): {output}"
            )
            return None
        data = parse_json_output(output)
        if data is None:
            print(
                f"  {Colors.RED}Segmentation output for {sdk_name} could not be "
                f"parsed{Colors.RESET}: {output[:300]}"
            )
            return None
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        # Parsed but empty (e.g. []): the backend has no segment support or no
        # rows for this SDK. Treat as "unavailable" so the caller falls back to
        # the aggregate gate rather than fabricating a pass.
        return None

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

        return results

    def _error_counts(self, sdk_result: Dict[str, Any]) -> Dict[str, int]:
        return {
            "context": sdk_result.get("context_errors", 0),
            "treatment": sdk_result.get("treatment_errors", 0),
            "track": sdk_result.get("track_errors", 0),
        }

    def _verify_active_sdks(self, results: Dict[str, Any], active: Dict[str, Dict[str, Any]]) -> None:
        # Aggregate backend numbers: both a cross-check and the reference for
        # detecting a backend that ignores the segmentation --filter.
        agg = self._fetch_metrics()
        agg_participants = self._extract_total_participants(agg) if agg else 0
        agg_goals = self._extract_total_goals(agg) if agg else 0

        # First pass: pull per-SDK segmented actuals. None => not independently
        # verifiable (segmentation unavailable / query failed / empty). Each
        # tuple is (participants, goals, revenue-or-None); revenue is only set
        # when the backend actually reports a monetary figure.
        segmented: Dict[str, Optional[Tuple[int, int, Optional[float]]]] = {}
        for sdk_name in active:
            m = self._fetch_metrics_for_sdk(sdk_name)
            if m is None:
                segmented[sdk_name] = None
            else:
                segmented[sdk_name] = (
                    self._extract_total_participants(m),
                    self._extract_total_goals(m),
                    self._extract_total_revenue(m),
                )

        # Filter-trust sanity checks. If the backend ignores --filter, every SDK
        # comes back with the aggregate total and per-SDK "verification" passes
        # everyone. Detect that and fall back to the aggregate gate.
        seg_values = [v for v in segmented.values() if v is not None]
        segmentation_trustworthy = True
        seg_warning: Optional[str] = None
        if len(seg_values) >= 2:
            exp_actuals = [v[0] for v in seg_values]
            # If the backend ignores --filter, every segmented query returns the
            # unsegmented aggregate, so all SDKs report an identical participant
            # count equal to the aggregate total. Planned volumes are identical
            # across SDKs here, so we cannot rely on expected variance to detect
            # this — key off the actuals collapsing onto the aggregate instead.
            if (
                len(set(exp_actuals)) == 1
                and agg_participants > 0
                and exp_actuals[0] == agg_participants
            ):
                segmentation_trustworthy = False
                seg_warning = (
                    "every SDK returned the same participant count equal to the "
                    f"aggregate total ({agg_participants}) — the backend appears "
                    "to ignore the segmentation filter"
                )
            elif sum(exp_actuals) > int(agg_participants * OVER_PUBLISH_TOLERANCE) and agg_participants > 0:
                segmentation_trustworthy = False
                seg_warning = (
                    f"sum of per-SDK participants ({sum(exp_actuals)}) exceeds the "
                    f"aggregate total ({agg_participants}) beyond tolerance — "
                    "segmentation is double-counting or the filter is ignored"
                )
        if seg_warning:
            print(f"  {Colors.RED}Segmentation unreliable{Colors.RESET}: {seg_warning}")

        # Second pass: finalize each active SDK.
        any_unverified = False
        for sdk_name, sdk_result in active.items():
            expected_exposures = sdk_result["planned_exposures"]
            expected_goals = sdk_result["planned_goals"]
            expected_revenue = expected_goals * 10
            errors = self._error_counts(sdk_result)
            has_errors = any(errors.values())

            seg = segmented.get(sdk_name)
            if segmentation_trustworthy and seg is not None:
                actual_exposures, actual_goals, actual_revenue = seg
                exposures_pass = self._within_tolerance(expected_exposures, actual_exposures)
                goals_pass = self._within_tolerance(expected_goals, actual_goals)

                # Verify revenue against the backend when it reports a real
                # figure; otherwise derive it from the goal count (10/goal) and
                # label it derived so the report doesn't imply it was verified.
                if actual_revenue is not None:
                    revenue_actual = actual_revenue
                    revenue_pass = self._within_tolerance(expected_revenue, int(round(actual_revenue)))
                    revenue_derived = False
                else:
                    revenue_actual = actual_goals * 10
                    revenue_pass = goals_pass
                    revenue_derived = True

                sdk_pass = exposures_pass and goals_pass and revenue_pass and not has_errors

                note = None
                if actual_exposures > int(expected_exposures * OVER_PUBLISH_TOLERANCE):
                    note = f"over-published exposures ({actual_exposures} > {expected_exposures})"
                elif actual_goals > int(expected_goals * OVER_PUBLISH_TOLERANCE):
                    note = f"over-published goals ({actual_goals} > {expected_goals})"
                elif has_errors:
                    note = (
                        f"wrapper errors ctx={errors['context']} "
                        f"treat={errors['treatment']} track={errors['track']}"
                    )

                results["sdks"][sdk_name] = {
                    "status": "ok" if sdk_pass else "fail",
                    "verified": "per-sdk",
                    "errors": errors,
                    "note": note,
                    "exposures": {"expected": expected_exposures, "actual": actual_exposures, "pass": exposures_pass},
                    "goals": {"expected": expected_goals, "actual": actual_goals, "pass": goals_pass},
                    "revenue": {"expected": expected_revenue, "actual": revenue_actual, "pass": revenue_pass, "derived": revenue_derived},
                }
                if not sdk_pass:
                    results["overall_pass"] = False
            else:
                # Not independently verifiable. Do NOT fabricate a pass: record
                # the SDK as unverified with unknown actuals and let the
                # aggregate cross-check below act as the gate. Wrapper errors
                # still fail the SDK outright.
                any_unverified = True
                results["sdks"][sdk_name] = {
                    "status": "fail" if has_errors else "unverified",
                    "verified": "aggregate",
                    "errors": errors,
                    "note": (
                        f"wrapper errors ctx={errors['context']} "
                        f"treat={errors['treatment']} track={errors['track']}"
                        if has_errors else None
                    ),
                    "exposures": {"expected": expected_exposures, "actual": None, "pass": False, "unverified": True},
                    "goals": {"expected": expected_goals, "actual": None, "pass": False, "unverified": True},
                    "revenue": {"expected": expected_revenue, "actual": None, "pass": False, "unverified": True, "derived": True},
                }
                if has_errors:
                    results["overall_pass"] = False

        # Aggregate cross-check. Expected is derived from PLANNED units (not
        # successes) so a wrapper that dropped calls fails rather than shrinking
        # its own target. The unsegmented aggregate query is unreliable on this
        # backend (can report 0 while per-SDK segmentation returns real counts),
        # so it only GATES when at least one SDK could not be verified per-SDK.
        total_expected = sum(r["planned_exposures"] for r in active.values())
        total_goals_expected = sum(r["planned_goals"] for r in active.values())
        agg_gates = any_unverified
        agg_pass = (
            total_expected > 0
            and self._within_tolerance(total_expected, agg_participants)
            and self._within_tolerance(total_goals_expected, agg_goals)
        )
        results["aggregate"] = {
            "expected_participants": total_expected,
            "actual_participants": agg_participants,
            "expected_goals": total_goals_expected,
            "actual_goals": agg_goals,
            "pass": agg_pass,
            "gating": agg_gates,
        }
        if agg_gates and not agg_pass:
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
            if exp.get("unverified") or exp.get("actual") is None:
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
                print(f"  {Colors.YELLOW}  ↳ {note}{Colors.RESET}")

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
            if agg.get("gating"):
                color = Colors.GREEN if agg["pass"] else Colors.RED
                label = "PASS" if agg["pass"] else "FAIL"
            else:
                # Per-SDK segmentation was the authoritative gate; the unsegmented
                # aggregate is informational only (unreliable on this backend).
                color = Colors.YELLOW
                label = "info (per-SDK segmentation authoritative)"
            print(
                f"  {color}Backend participants: "
                f"{agg['actual_participants']}/{agg['expected_participants']} {label}{Colors.RESET}"
            )
            print()

        if results.get("overall_pass"):
            print(f"  {Colors.GREEN}PASS{Colors.RESET} All metrics verified")
        else:
            print(f"  {Colors.RED}FAIL{Colors.RESET} Some metrics did not match")
        print()

    def _format_metric(self, metric: Dict[str, Any]) -> str:
        # Unverified rows are neither PASS nor FAIL: the backend never gave us a
        # trustworthy actual, so render them distinctly instead of as a green ✓.
        if metric.get("unverified") or metric.get("actual") is None:
            return f"{Colors.YELLOW}?/{metric['expected']} UNVERIFIED{Colors.RESET}"
        actual = metric["actual"]
        expected = metric["expected"]
        passed = metric.get("pass", False)
        color = Colors.GREEN if passed else Colors.RED
        label = "PASS" if passed else "FAIL"
        if metric.get("derived"):
            label += " (derived)"
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
