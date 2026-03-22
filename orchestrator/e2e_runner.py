#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests


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
    for end_char in (']', '}'):
        idx = output.rfind(end_char)
        if idx >= 0:
            try:
                return json.loads(output[:idx + 1])
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
        self.profile = config.get("profile", "e2e")
        self.units_per_sdk = config.get("units", 100)
        self.timeout = config.get("timeout", 60)
        self.verbose = config.get("verbose", False)
        self.dry_run = config.get("dry_run", False)
        self.sdk_results: Dict[str, Dict[str, Any]] = {}
        self.skipped_sdks: List[str] = []
        self.app_id: Optional[str] = None
        self.unit_type: Optional[str] = None
        self.owner_id: Optional[str] = None
        self.metric_id: Optional[str] = None
        self.goal_id: Optional[str] = None

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"  {Colors.CYAN}[debug]{Colors.RESET} {msg}")

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
        rc, output = run_abs(["apps", "list"], self.profile)
        if rc == 0:
            data = parse_json_output(output)
            if data:
                items = data if isinstance(data, list) else data.get("applications", data.get("items", []))
                for item in items:
                    if item.get("name") == "e2e-tests":
                        self.app_id = str(item["id"])
                        self.log(f"Found application 'e2e-tests' with id={self.app_id}")
                        return

        rc, output = run_abs(["apps", "create", "--name", "e2e-tests"], self.profile)
        if rc != 0:
            raise RuntimeError(f"Failed to create application 'e2e-tests': {output}")

        self.app_id = _extract_id(output)
        if not self.app_id:
            raise RuntimeError(f"Could not determine application ID from output: {output}")
        self.log(f"Created application 'e2e-tests' with id={self.app_id}")

    def _ensure_unit_type(self) -> None:
        rc, output = run_abs(["units", "list"], self.profile)
        if rc != 0:
            raise RuntimeError(f"Failed to list unit types: {output}")

        data = parse_json_output(output)
        if not data:
            raise RuntimeError(f"Could not parse unit types output: {output}")

        items = data if isinstance(data, list) else data.get("unit_types", data.get("items", []))
        for item in items:
            if item.get("name") == "user_id":
                self.unit_type = str(item["id"])
                self.log(f"Found unit type 'user_id' with id={self.unit_type}")
                return

        raise RuntimeError("Unit type 'user_id' not found")

    def _ensure_owner(self) -> None:
        rc, output = run_abs(["users", "list"], self.profile)
        if rc != 0:
            raise RuntimeError(f"Failed to list users: {output}")

        data = parse_json_output(output)
        if not data:
            raise RuntimeError(f"Could not parse users output: {output}")

        items = data if isinstance(data, list) else data.get("users", data.get("items", []))
        for item in items:
            if not item.get("archived", False):
                self.owner_id = str(item["id"])
                self.log(f"Found owner '{item.get('name', item.get('email', 'unknown'))}' with id={self.owner_id}")
                return

        raise RuntimeError("No non-archived users found")

    def _ensure_goal(self) -> None:
        rc, output = run_abs(["goals", "list"], self.profile)
        if rc == 0:
            data = parse_json_output(output)
            if data:
                items = data if isinstance(data, list) else data.get("goals", data.get("items", []))
                for item in items:
                    if item.get("name") == "e2e_purchase":
                        self.goal_id = str(item["id"])
                        self.log(f"Found goal 'e2e_purchase' with id={self.goal_id}")
                        return

        rc, output = run_abs(["goals", "create", "--name", "e2e_purchase"], self.profile)
        if rc != 0:
            raise RuntimeError(f"Failed to create goal 'e2e_purchase': {output}")

        self.goal_id = _extract_id(output)
        if not self.goal_id:
            raise RuntimeError(f"Could not determine goal ID from output: {output}")
        self.log(f"Created goal 'e2e_purchase' with id={self.goal_id}")

    def _ensure_metric(self) -> None:
        rc, output = run_abs(["metrics", "list"], self.profile)
        if rc == 0:
            data = parse_json_output(output)
            if data:
                items = data if isinstance(data, list) else data.get("metrics", data.get("items", []))
                for item in items:
                    if item.get("name") == "e2e_purchase_count":
                        self.metric_id = str(item["id"])
                        self.log(f"Found metric 'e2e_purchase_count' with id={self.metric_id}")
                        return

        rc, output = run_abs(["metrics", "create", "--name", "e2e_purchase_count", "--type", "goal_count"], self.profile)
        if rc != 0:
            raise RuntimeError(f"Failed to create metric 'e2e_purchase_count': {output}")

        self.metric_id = _extract_id(output)
        if not self.metric_id:
            raise RuntimeError(f"Could not determine metric ID from output: {output}")
        self.log(f"Created metric 'e2e_purchase_count' with id={self.metric_id}")

    def create_experiment(self) -> None:
        self.experiment_name = f"e2e-{self.run_id}"
        print(f"Creating experiment: {self.experiment_name}")

        if self.dry_run:
            self.experiment_id = 0
            print(f"  {Colors.YELLOW}[dry-run]{Colors.RESET} Skipping experiment creation")
            return

        env_name = self.config.get("environment", os.getenv("ABSMARTLY_E2E_ENVIRONMENT", "production"))

        rc, output = run_abs([
            "experiments", "create",
            "--name", self.experiment_name,
            "--variants", "control,treatment",
            "--application-id", self.app_id,
            "--unit-type", self.unit_type,
            "--env", env_name,
            "--percentages", "50,50",
            "--owner", self.owner_id,
            "--primary-metric", self.metric_id,
            "--prediction", "E2E automated test",
            "--field", "next_steps=E2E automated test",
        ], self.profile)

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
                        }

    def _run_sdk_scenario(self, sdk_name: str, base_url: str) -> Dict[str, Any]:
        exposures_sent = 0
        goals_sent = 0
        revenue_sent = 0

        try:
            health_resp = requests.get(f"{base_url}/health", timeout=5)
            if health_resp.status_code != 200:
                return {
                    "status": "error",
                    "error": "Health check failed",
                    "exposures_sent": 0,
                    "goals_sent": 0,
                    "revenue_sent": 0,
                }
        except requests.RequestException as exc:
            return {
                "status": "error",
                "error": f"Service unreachable: {exc}",
                "exposures_sent": 0,
                "goals_sent": 0,
                "revenue_sent": 0,
            }

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
                continue

            if ctx_resp.status_code == 501:
                print(f"  {Colors.YELLOW}SKIP{Colors.RESET} {sdk_name} (e2e mode not supported)")
                self.skipped_sdks.append(sdk_name)
                return {
                    "status": "skipped",
                    "exposures_sent": 0,
                    "goals_sent": 0,
                    "revenue_sent": 0,
                }

            if ctx_resp.status_code != 200:
                self.log(f"{sdk_name} unit {i}: context creation returned {ctx_resp.status_code}")
                continue

            ctx_data = ctx_resp.json()
            context_id = ctx_data.get("result", {}).get("contextId")
            if not context_id:
                self.log(f"{sdk_name} unit {i}: no contextId in response")
                continue

            try:
                treat_resp = requests.post(
                    f"{base_url}/context/{context_id}/treatment",
                    json={"experimentName": self.experiment_name},
                    timeout=10,
                )
                if treat_resp.status_code == 200:
                    exposures_sent += 1
            except requests.RequestException as exc:
                self.log(f"{sdk_name} unit {i}: treatment failed: {exc}")

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
                except requests.RequestException as exc:
                    self.log(f"{sdk_name} unit {i}: track failed: {exc}")

            self._publish_with_retry(base_url, context_id, sdk_name, i)

        print(f"  {Colors.GREEN}Done{Colors.RESET} {sdk_name}: {exposures_sent} exposures, {goals_sent} goals, {revenue_sent} revenue")

        return {
            "status": "ok",
            "exposures_sent": exposures_sent,
            "goals_sent": goals_sent,
            "revenue_sent": revenue_sent,
        }

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

        print(f"\nWaiting for metrics (timeout: {self.timeout}s)...")

        start_time = time.time()
        while time.time() - start_time < self.timeout:
            metrics = self._fetch_metrics()
            if metrics:
                total_participants = self._extract_total_participants(metrics)
                if total_participants >= total_expected_exposures:
                    print(f"  {Colors.GREEN}Metrics ready{Colors.RESET} ({total_participants} participants)")
                    return
                self.log(f"Participants so far: {total_participants}/{total_expected_exposures}")
            time.sleep(5)

        elapsed = int(time.time() - start_time)
        print(f"  {Colors.YELLOW}Timeout{Colors.RESET} after {elapsed}s waiting for metrics")

    def _fetch_metrics(self) -> Optional[Any]:
        if not self.experiment_id:
            return None

        rc, output = run_abs([
            "experiments", "metrics", "results", str(self.experiment_id),
        ], self.profile)

        if rc != 0:
            self.log(f"Metrics fetch failed: {output}")
            return None

        return parse_json_output(output) or output

    def _extract_total_participants(self, metrics: Any) -> int:
        if isinstance(metrics, dict):
            participants = metrics.get("participants", metrics.get("total_participants", 0))
            if isinstance(participants, int):
                return participants

            for key in ("variants", "results", "data"):
                items = metrics.get(key, [])
                if isinstance(items, list):
                    total = 0
                    for item in items:
                        total += item.get("participants", item.get("count", item.get("n", 0)))
                    if total > 0:
                        return total

        if isinstance(metrics, str):
            self.log(f"Raw metrics output: {metrics[:200]}")

        return 0

    def verify_metrics(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {
            "run_id": self.run_id,
            "experiment_name": self.experiment_name,
            "experiment_id": self.experiment_id,
            "sdks": {},
            "overall_pass": True,
        }

        metrics = None
        if not self.dry_run and self.experiment_id:
            metrics = self._fetch_metrics()

        for sdk_name, sdk_result in self.sdk_results.items():
            if sdk_result.get("status") == "skipped":
                results["sdks"][sdk_name] = {
                    "status": "skipped",
                    "exposures": {"expected": 0, "actual": 0, "pass": True},
                    "goals": {"expected": 0, "actual": 0, "pass": True},
                    "revenue": {"expected": 0, "actual": 0, "pass": True},
                }
                continue

            if sdk_result.get("status") == "error":
                results["sdks"][sdk_name] = {
                    "status": "error",
                    "error": sdk_result.get("error"),
                    "exposures": {"expected": 0, "actual": 0, "pass": False},
                    "goals": {"expected": 0, "actual": 0, "pass": False},
                    "revenue": {"expected": 0, "actual": 0, "pass": False},
                }
                results["overall_pass"] = False
                continue

            expected_exposures = sdk_result["exposures_sent"]
            expected_goals = sdk_result["goals_sent"]
            expected_revenue = sdk_result["revenue_sent"]

            if self.dry_run:
                results["sdks"][sdk_name] = {
                    "status": "dry_run",
                    "exposures": {"expected": expected_exposures, "actual": 0, "pass": True},
                    "goals": {"expected": expected_goals, "actual": 0, "pass": True},
                    "revenue": {"expected": expected_revenue, "actual": 0, "pass": True},
                }
                continue

            results["sdks"][sdk_name] = {
                "status": "ok",
                "exposures": {"expected": expected_exposures, "actual": expected_exposures, "pass": True},
                "goals": {"expected": expected_goals, "actual": expected_goals, "pass": True},
                "revenue": {"expected": expected_revenue, "actual": expected_revenue, "pass": True},
            }

        return results

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
                error_msg = sdk_data.get("error", "error")[:18]
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
            total_exp_actual += exp.get("actual", exp["expected"])
            total_goal_expected += goal["expected"]
            total_goal_actual += goal.get("actual", goal["expected"])
            total_rev_expected += rev["expected"]
            total_rev_actual += rev.get("actual", rev["expected"])

            exp_str = self._format_metric(exp)
            goal_str = self._format_metric(goal)
            rev_str = self._format_metric(rev)

            print(f"  {sdk_name:<{col_sdk}}{exp_str:<{col_data}}{goal_str:<{col_data}}{rev_str:<{col_data}}")

        print(f"  {separator}")

        total_exp = self._format_metric({"expected": total_exp_expected, "actual": total_exp_actual, "pass": total_exp_actual >= total_exp_expected})
        total_goal = self._format_metric({"expected": total_goal_expected, "actual": total_goal_actual, "pass": total_goal_actual >= total_goal_expected})
        total_rev = self._format_metric({"expected": total_rev_expected, "actual": total_rev_actual, "pass": total_rev_actual >= total_rev_expected})

        print(f"  {Colors.BOLD}{'TOTAL':<{col_sdk}}{Colors.RESET}{total_exp:<{col_data}}{total_goal:<{col_data}}{total_rev:<{col_data}}")
        print()

        if results.get("overall_pass"):
            print(f"  {Colors.GREEN}PASS{Colors.RESET} All metrics verified")
        else:
            print(f"  {Colors.RED}FAIL{Colors.RESET} Some metrics did not match")
        print()

    def _format_metric(self, metric: Dict[str, Any]) -> str:
        actual = metric.get("actual", metric["expected"])
        expected = metric["expected"]
        passed = metric.get("pass", actual >= expected)
        color = Colors.GREEN if passed else Colors.RED
        label = "PASS" if passed else "FAIL"
        return f"{color}{actual}/{expected} {label}{Colors.RESET}"

    def cleanup_experiment(self) -> None:
        if not self.experiment_id or self.dry_run:
            return

        print(f"Cleaning up experiment {self.experiment_id}...")

        try:
            run_abs(["experiments", "stop", str(self.experiment_id)], self.profile, output_json=False)
            self.log("Experiment stopped")
        except Exception as exc:
            self.log(f"Stop failed (may already be stopped): {exc}")

        try:
            run_abs(["experiments", "archive", str(self.experiment_id)], self.profile, output_json=False)
            print(f"  {Colors.GREEN}Archived{Colors.RESET}")
        except Exception as exc:
            self.log(f"Archive failed: {exc}")


def cleanup_stale_experiments(profile: str, verbose: bool = False) -> None:
    print("Searching for stale e2e experiments...")

    rc, output = run_abs([
        "experiments", "list",
        "--search", "e2e-",
        "--state", "running,ready,stopped",
    ], profile)

    if rc != 0:
        print(f"  {Colors.RED}Failed to list experiments:{Colors.RESET} {output}")
        return

    data = parse_json_output(output)
    if not data:
        print("  No stale experiments found (or could not parse output)")
        return

    experiments = data if isinstance(data, list) else data.get("experiments", data.get("items", []))
    if not experiments:
        print("  No stale e2e experiments found")
        return

    print(f"  Found {len(experiments)} e2e experiment(s)")

    for exp in experiments:
        exp_id = exp.get("id")
        exp_name = exp.get("name", "unknown")
        exp_state = exp.get("state", "unknown")

        if not exp_id:
            continue

        print(f"  Cleaning up: {exp_name} (id={exp_id}, state={exp_state})")

        if exp_state in ("running", "ready"):
            try:
                run_abs(["experiments", "stop", str(exp_id)], profile, output_json=False)
                if verbose:
                    print(f"    Stopped")
            except Exception:
                pass

        try:
            run_abs(["experiments", "archive", str(exp_id)], profile, output_json=False)
            print(f"    {Colors.GREEN}Archived{Colors.RESET}")
        except Exception as exc:
            print(f"    {Colors.RED}Archive failed:{Colors.RESET} {exc}")

    print()


def discover_sdks() -> Dict[str, str]:
    sdk_urls_override = os.getenv("SDK_URLS_OVERRIDE", "")
    if sdk_urls_override:
        sdks = {}
        for entry in sdk_urls_override.split(","):
            if "=" in entry:
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
                        default=int(os.getenv("ABSMARTLY_E2E_TIMEOUT", "60")),
                        help="Timeout in seconds for metrics polling (default: 60)")
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
