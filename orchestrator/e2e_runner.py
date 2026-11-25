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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


# Pass iff expected <= actual <= expected * OVER_PUBLISH_TOLERANCE. The small
# allowance absorbs publish retries; anything above it is treated as an
# over-publish (duplicate exposures/goals) and fails.
OVER_PUBLISH_TOLERANCE = 1.05

# Revenue metric on the `purchase` goal: goal_property summing the `amount`
# property each tracked purchase carries. Its per-variant `count` is the summed
# amount, so revenue is MEASURED by the backend, never computed from goals.
REVENUE_METRIC_NAME = "e2e_purchase_revenue"
REVENUE_PROPERTY = "amount"
# `metrics list` omits drafts and archived metrics unless asked (absmartly/cli
# src/commands/metrics/index.ts --include-drafts; src/lib/utils/list-command.ts
# --archived), and a metric left inactive by a failed run is exactly what a
# retry must find, or it would fail to re-create it ("already exists"). The
# search keeps the metric on the first page however many metrics exist.
# A metric's lifecycle_status is "draft" or "activated" (absmartly api-mocks
# schema Metric.lifecycle_status); "active" is kept for older payloads.
ACTIVE_LIFECYCLE_STATUSES = ("activated", "active")
REVENUE_LOOKUP_ARGS = ("--include-drafts", "--archived", "--search", REVENUE_METRIC_NAME)
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
        # SDKs the user named explicitly (--sdk). A requested SDK that is skipped
        # (e.g. its wrapper answers 501 for e2e mode) fails the run; only SDKs
        # that were not requested (the default "all") may be skipped.
        self.requested_sdks = set(config.get("requested_sdks") or ())
        self.experiment_context_ready = True
        self.app_id: Optional[str] = None
        self.unit_type: Optional[str] = None
        self.owner_id: Optional[str] = None
        self.metric_id: Optional[str] = None
        self.goal_id: Optional[str] = None
        self.revenue_metric_id: Optional[str] = None

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"  {Colors.CYAN}[debug]{Colors.RESET} {msg}")

    def _find_resource(self, resource_type: str, name: str, extra_args: Sequence[str] = (),
                       unique: bool = False) -> Optional[str]:
        """Id of the resource named `name`, or None.

        With `unique`, more than one match is an error rather than a guess: a
        listing that includes drafts and archived items could otherwise pick a
        stale row with the same name.
        """
        rc, output = run_abs([resource_type, "list", "--items", "500", *extra_args], self.profile)
        if rc != 0:
            self.log(f"_find_resource({resource_type}, {name}) failed: {output}")
            return None
        data = parse_json_output(output)
        if not (data and isinstance(data, list)):
            return None
        matches = [str(item["id"]) for item in data if isinstance(item, dict) and item.get("name") == name]
        if unique and len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} {resource_type} named {name!r} (ids {', '.join(matches)}); "
                f"rename the extra ones (`abs {resource_type} update <id> --name <new>`); archiving "
                "does not help, since the lookup includes archived items"
            )
        return matches[0] if matches else None

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
        if self._find_and_reuse_revenue_metric():
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

        if rc != 0 and "already exists" in output and self._find_and_reuse_revenue_metric():
            return
        if rc != 0:
            raise RuntimeError(f"Failed to create revenue metric: {output}")

        self.revenue_metric_id = _extract_id(output)
        if not self.revenue_metric_id:
            raise RuntimeError(f"Could not determine revenue metric ID from output: {output}")

        self._review_and_activate_revenue_metric(self.revenue_metric_id)
        self.log(f"Created and activated metric '{REVENUE_METRIC_NAME}' with id={self.revenue_metric_id}")

    def _find_and_reuse_revenue_metric(self) -> bool:
        """Find the existing revenue metric (refusing duplicates) and make it usable."""
        self.revenue_metric_id = self._find_resource("metrics", REVENUE_METRIC_NAME, REVENUE_LOOKUP_ARGS, unique=True)
        if not self.revenue_metric_id:
            return False
        self._reuse_revenue_metric(self.revenue_metric_id)
        self.log(f"Found metric '{REVENUE_METRIC_NAME}' with id={self.revenue_metric_id}")
        return True

    def _review_and_activate_revenue_metric(self, metric_id: str, review_status: Optional[str] = None) -> None:
        """Request review, approve and activate, skipping steps a failed run already did.

        A retry after a run that got past `review request` (the metric's
        `review.status` is "pending") or past `approve` ("approved") must not
        repeat those steps, which the API may reject (absmartly api-mocks
        schema MetricReview.status).
        """
        actions = []
        if review_status not in ("pending", "approved"):
            actions.append(["metrics", "review", "request", metric_id])
        if review_status != "approved":
            actions.append(["metrics", "review", "approve", metric_id])
        actions.append(["metrics", "activate", metric_id, "--reason", "E2E testing"])
        for action in actions:
            rc, output = run_abs(action, self.profile, output_json=False)
            if rc != 0:
                raise RuntimeError(f"Failed to run {' '.join(action)} (abs rc={rc}): {output}")

    def _reuse_revenue_metric(self, metric_id: str) -> None:
        """Check an existing revenue metric and activate it if a run left it inactive.

        A run whose review/approve/activate step failed leaves the metric in
        draft; later runs find it by name, so they re-run the activation instead
        of accepting a metric that measures nothing.
        """
        status, review_status, archived = self._check_revenue_metric_definition(metric_id)
        if archived:
            # Archiving is a separate flag (absmartly/cli `metrics archive
            # [--unarchive]`), not a lifecycle status.
            self.log(f"Metric '{REVENUE_METRIC_NAME}' is archived; unarchiving it")
            action = ["metrics", "archive", metric_id, "--unarchive"]
            rc, output = run_abs(action, self.profile, output_json=False)
            if rc != 0:
                raise RuntimeError(f"Failed to run {' '.join(action)} (abs rc={rc}): {output}")
        if status is not None and status not in ACTIVE_LIFECYCLE_STATUSES:
            self.log(f"Metric '{REVENUE_METRIC_NAME}' is {status!r} (review {review_status!r}); activating it")
            self._review_and_activate_revenue_metric(metric_id, review_status)

    def _check_revenue_metric_definition(self, metric_id: str) -> Tuple[Optional[str], Optional[str], bool]:
        """Refuse an existing e2e_purchase_revenue that does not sum purchase.amount.

        A metric found by name alone could be a different type or property, and
        its result would then be "measured" wrongly. `abs metrics get <id> --raw`
        returns the metric's `type` and `value_source_property` (absmartly/cli
        src/core/metrics/payload.ts). The goal is checked when the payload
        carries it as a nested goal or `goal_id`. Returns the metric's
        (`lifecycle_status`, `review.status`, `archived`); the first two are
        None if absent.
        """
        # `--raw`: without it the CLI prints a summary that has no
        # value_source_property and a plain-string goal (absmartly/cli
        # src/core/metrics/get.ts -> summarizeMetric; --raw in api-helper.ts).
        rc, output = run_abs(["metrics", "get", str(metric_id), "--raw"], self.profile)
        metric = parse_json_output(output) if rc == 0 else None
        if not isinstance(metric, dict):
            raise RuntimeError(
                f"Could not read metric '{REVENUE_METRIC_NAME}' (id={metric_id}) to check its definition: {output}"
            )
        problems = []
        if metric.get("type") != "goal_property":
            problems.append(f"type={metric.get('type')!r}, expected 'goal_property'")
        if metric.get("value_source_property") != REVENUE_PROPERTY:
            problems.append(
                f"value_source_property={metric.get('value_source_property')!r}, expected {REVENUE_PROPERTY!r}"
            )
        goal = metric.get("goal")
        goal_name = goal.get("name") if isinstance(goal, dict) else None
        if goal_name is not None and goal_name != "purchase":
            problems.append(f"goal={goal_name!r}, expected 'purchase'")
        goal_id = metric.get("goal_id")
        if goal_id is not None and self.goal_id is not None and str(goal_id) != str(self.goal_id):
            problems.append(f"goal_id={goal_id!r}, expected {self.goal_id!r}")
        if problems:
            raise RuntimeError(
                f"Existing metric '{REVENUE_METRIC_NAME}' (id={metric_id}) cannot measure revenue: "
                + "; ".join(problems)
            )
        status = metric.get("lifecycle_status")
        # The review is a nested object, `review: {status: "pending" | "approved"}`
        # or null (absmartly api-mocks schema MetricReview). There is no top-level
        # review_status on a metric; that name is only a `metrics list` filter.
        review = metric.get("review")
        review_status = review.get("status") if isinstance(review, dict) else None
        return (status if isinstance(status, str) else None,
                review_status if isinstance(review_status, str) else None,
                metric.get("archived") is True)

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

        # An SDK with wrapper errors already fails verification, so waiting for its
        # planned counts would only spend the whole timeout on a decided result.
        active_sdks = {
            k: v for k, v in self.sdk_results.items()
            if v.get("status") == "ok" and not any(self._error_counts(v).values())
        }
        if not active_sdks:
            print("\nNo active SDKs to verify metrics for")
            return

        rc, _ = run_abs(["experiments", "request-update", str(self.experiment_id)], self.profile, output_json=False)
        if rc == 0:
            self.log("Requested analysis update")

        print(f"\nWaiting for metrics (timeout: {self.timeout}s)...")

        # Participants, goals and revenue need not become available together.
        # Readiness uses the same per-SDK measurements as the final strict gate;
        # the unsegmented aggregate cannot establish readiness for an SDK.
        deadline = time.monotonic() + self.timeout
        pending = [f"{sdk}: not polled" for sdk in active_sdks]
        decided: set = set()
        while time.monotonic() < deadline:
            pending = []
            for sdk_name, sdk_result in active_sdks.items():
                if sdk_name in decided:
                    continue
                # Each query can take up to run_abs's timeout, so check the
                # deadline per SDK rather than only between rounds.
                if time.monotonic() >= deadline:
                    pending.append(f"{sdk_name}: not polled before the deadline")
                    continue
                metric, reason = self._fetch_metrics_for_sdk(sdk_name)
                # The count read may have used up the budget: never start another
                # query after the deadline. The unread revenue stays unresolved (strict
                # verification still reads it), while the counts just read are still
                # reported and still checked for over-publishing.
                if time.monotonic() >= deadline:
                    revenue, revenue_reason = None, "not polled before the deadline"
                else:
                    revenue, revenue_reason = self._fetch_revenue(sdk_name)
                expected_goals = sdk_result["planned_goals"]
                participants, goals, _ = self._measured_counts(metric)
                measurements = (
                    ("participants", sdk_result["planned_exposures"], participants, reason),
                    ("goals", expected_goals, goals, reason),
                    ("revenue", expected_goals * PURCHASE_AMOUNT, revenue, revenue_reason),
                )
                # Counts only grow, so a value already past the over-publish bound can
                # never pass: that SDK's FAIL is decided and it no longer gates readiness.
                if any(failure is None and actual is not None
                       and actual > self._upper_bound(expected)
                       for _, expected, actual, failure in measurements):
                    decided.add(sdk_name)
                    continue
                for name, expected, actual, failure in measurements:
                    if failure or not self._within_tolerance(expected, actual):
                        pending.append(f"{sdk_name} {name}: {actual}/{expected}" +
                                       (f" ({failure})" if failure else ""))
            if not pending:
                if decided:
                    print(
                        f"  {Colors.RED}Metrics over-published{Colors.RESET} for "
                        f"{', '.join(sorted(decided))}; stopped waiting (verification fails them)"
                    )
                else:
                    print(f"  {Colors.GREEN}Metrics ready{Colors.RESET} (all per-SDK metrics)")
                return
            self.log("Waiting for " + "; ".join(pending))
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(5, remaining))

        print(
            f"  {Colors.RED}Metrics incomplete{Colors.RESET} after {self.timeout}s: "
            + "; ".join(pending)
        )

    def _fetch_metrics(self) -> Optional[Dict[str, Any]]:
        """Unsegmented metric result for the e2e metric (aggregate across SDKs)."""
        if not self.experiment_id:
            return None

        rc, output = run_abs([
            "experiments", "metrics", "results", str(self.experiment_id),
        ], self.profile)

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
        resultsCommand). Select ours by metric_id. Only when no metric_id is
        known fall back to the "primary" entry; a known id that is missing from
        the output never selects some other row.
        """
        if self.metric_id is not None:
            return self._select_metric_by_id(data, self.metric_id)
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

    def _measured_counts(self, metric: Any) -> Tuple[Optional[int], Optional[int], Optional[List[Dict[str, int]]]]:
        """(exposures, goals, per-variant rows) from one parse of a MetricResult; Nones if unmeasured."""
        variants = self._variant_counts(metric) if metric is not None else None
        if not variants:
            return None, None, variants
        return (sum(v["unit_count"] for v in variants), sum(v["count"] for v in variants), variants)

    def _extract_total_participants(self, metrics: Any) -> int:
        """Sum of variants[].unit_count (exposed units); 0 if unavailable."""
        counts = self._variant_counts(metrics)
        return sum(v["unit_count"] for v in counts) if counts else 0

    def _extract_total_goals(self, metrics: Any) -> int:
        """Sum of variants[].count (goal_unique_count numerator); 0 if unavailable."""
        counts = self._variant_counts(metrics)
        return sum(v["count"] for v in counts) if counts else 0

    @staticmethod
    def _upper_bound(expected: int) -> int:
        """Largest count that still passes for `expected` (the over-publish bound)."""
        return int(expected * OVER_PUBLISH_TOLERANCE)

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
        return expected <= actual <= self._upper_bound(expected)

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
            # `output` already carries the CLI stderr (see run_abs). Readiness polls
            # repeatedly, so each distinct failure is printed once per run.
            self._print_once(
                f"  {Colors.RED}Segmentation query failed for {sdk_name}"
                f"{Colors.RESET} (abs rc={rc}): {output}"
            )
            first_line = (output.strip().splitlines() or [""])[0][:200]
            reason = f"segmentation query failed (abs rc={rc})"
            return None, f"{reason}: {first_line}" if first_line else reason
        data = parse_json_output(output)
        if data is None:
            self._print_once(
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
            if status == "skipped" and sdk_name in self.requested_sdks:
                results["sdks"][sdk_name] = {
                    "status": "fail",
                    "note": "requested with --sdk but skipped (wrapper reports no e2e mode)",
                    "exposures": {"expected": sdk_result.get("planned_exposures", 0), "actual": None,
                                  "pass": False, "unverified": True},
                    "goals": {"expected": sdk_result.get("planned_goals", 0), "actual": None,
                              "pass": False, "unverified": True},
                    "revenue": {"expected": sdk_result.get("planned_goals", 0) * PURCHASE_AMOUNT, "actual": None,
                                "pass": False, "unverified": True},
                }
                results["overall_pass"] = False
                continue

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

    def _print_once(self, message: str) -> None:
        printed = self.__dict__.setdefault("_printed_messages", set())
        if message not in printed:
            printed.add(message)
            print(message)

    def _error_counts(self, sdk_result: Dict[str, Any]) -> Dict[str, int]:
        return {
            "context": sdk_result.get("context_errors", 0),
            "treatment": sdk_result.get("treatment_errors", 0),
            "track": sdk_result.get("track_errors", 0),
        }

    def _verify_active_sdks(self, results: Dict[str, Any], active: Dict[str, Dict[str, Any]]) -> None:
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

        # Read aggregates last so a live update during segmented reads does not
        # compare newer segment sums against an older aggregate snapshot.
        agg = self._fetch_metrics()
        agg_participants = self._extract_total_participants(agg) if agg else 0
        agg_goals = self._extract_total_goals(agg) if agg else 0
        agg_revenue, agg_revenue_reason = self._fetch_revenue()

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
            elif sum(exp_actuals) > self._upper_bound(agg_participants):
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

            actual_exposures, actual_goals, variants = self._measured_counts(segmented.get(sdk_name))

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
            elif actual_exposures > self._upper_bound(expected_exposures):
                note = f"over-published exposures ({actual_exposures} > {expected_exposures})"
            elif actual_goals > self._upper_bound(expected_goals):
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
        partially_unverified = {"exposures": False, "goals": False, "revenue": False}

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
            for name, metric in (("exposures", exp), ("goals", goal), ("revenue", rev)):
                if metric.get("unverified") or metric.get("actual") is None:
                    partially_unverified[name] = True
                elif name == "exposures":
                    total_exp_actual += metric["actual"]
                elif name == "goals":
                    total_goal_actual += metric["actual"]
                else:
                    total_rev_actual += metric["actual"]

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

        total_exp = self._format_total(total_exp_expected, total_exp_actual, partially_unverified["exposures"])
        total_goal = self._format_total(total_goal_expected, total_goal_actual, partially_unverified["goals"])
        total_rev = self._format_total(total_rev_expected, total_rev_actual, partially_unverified["revenue"])

        print(f"  {Colors.BOLD}{'TOTAL':<{col_sdk}}{Colors.RESET}{total_exp:<{col_data}}{total_goal:<{col_data}}{total_rev:<{col_data}}")
        if any(partially_unverified.values()):
            print(f"  {Colors.YELLOW}(Partial TOTAL actuals exclude unverified measurements — lower bounds){Colors.RESET}")
        print()

        skipped = sorted(name for name, row in sdks.items() if row.get("status") == "skipped")
        if skipped:
            # Only SDKs that were not requested with --sdk can be skipped.
            print(f"  {Colors.YELLOW}Skipped (not requested, no e2e mode): {', '.join(skipped)}{Colors.RESET}")
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
        """PASS only if the run passed AND every non-skipped SDK row was measured and passed.

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
        if partial:
            return f"{Colors.YELLOW}{actual}/{expected} FAIL (partial/unverified){Colors.RESET}"
        passed = self._within_tolerance(expected, actual)
        color = Colors.GREEN if passed else Colors.RED
        label = "PASS" if passed else "FAIL"
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
        sdk_names = [s.strip() for s in args.sdk.split(",") if s.strip()]
        sdks = {name: all_sdks[name] for name in sdk_names if name in all_sdks}
        invalid_sdks = [name for name in sdk_names if name not in all_sdks]
        if invalid_sdks:
            # A requested SDK that cannot be tested must fail, never be dropped.
            print(f"Error: Unknown SDK(s): {', '.join(invalid_sdks)}. Available: {', '.join(all_sdks.keys())}")
            sys.exit(1)
        if not sdks:
            print(f"No valid SDKs specified. Available: {', '.join(all_sdks.keys())}")
            sys.exit(1)
        requested_sdks = list(sdks)
    else:
        sdks = all_sdks
        requested_sdks = []

    config = {
        "profile": args.profile,
        "units": args.units,
        "timeout": args.timeout,
        "verbose": args.verbose,
        "dry_run": args.dry_run,
        "requested_sdks": requested_sdks,
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
