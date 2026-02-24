#!/usr/bin/env python3
import json
import re
import sys
import os

UNIT_TEST_PARSERS = {
    "javascript": {
        "framework": "jest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total", lambda m: (int(m.group(1)), int(m.group(2)) - int(m.group(1)))),
            (r"(\d+)\s+passing", lambda m: (int(m.group(1)), 0)),
        ],
    },
    "python": {
        "framework": "pytest",
        "patterns": [
            (r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?", lambda m: (int(m.group(1)), int(m.group(2) or 0))),
        ],
    },
    "ruby": {
        "framework": "rspec",
        "patterns": [
            (r"(\d+)\s+examples?,\s+(\d+)\s+failures?", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
        ],
    },
    "java": {
        "framework": "gradle/junit",
        "patterns": [
            (r"(\d+)\s+tests?\s+completed,\s+(\d+)\s+failed", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
            (r"BUILD SUCCESSFUL", lambda m: (None, 0)),
        ],
    },
    "php": {
        "framework": "phpunit",
        "patterns": [
            (r"OK\s+\((\d+)\s+tests?", lambda m: (int(m.group(1)), 0)),
            (r"Tests:\s+(\d+).*?Failures:\s+(\d+)", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
            (r"There\s+(?:were|was)\s+(\d+)\s+errors?:", lambda m: (0, int(m.group(1)))),
        ],
    },
    "go": {
        "framework": "go test",
        "patterns": [
            (r"^ok\s+", lambda m: (1, 0)),
            (r"^FAIL\s+", lambda m: (0, 1)),
        ],
        "aggregate": True,
    },
    "dart": {
        "framework": "dart test",
        "patterns": [
            (r"\+(\d+)(?:\s+-(\d+))?.*?All tests passed", lambda m: (int(m.group(1)), 0)),
            (r"\+(\d+)\s+-(\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
            (r"(\d+)\s+tests?\s+passed", lambda m: (int(m.group(1)), 0)),
        ],
    },
    "flutter": {
        "framework": "flutter test",
        "patterns": [
            (r"\+(\d+)(?:\s+-(\d+))?.*?All tests passed", lambda m: (int(m.group(1)), 0)),
            (r"(\d+)\s+tests?\s+passed", lambda m: (int(m.group(1)), 0)),
            (r"\+(\d+)\s+-(\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
        ],
    },
    "swift": {
        "framework": "swift test",
        "patterns": [
            (r"Executed\s+(\d+)\s+tests?\s+with\s+(\d+)\s+failures?", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
            (r"Test Suite.*?Executed\s+(\d+)\s+tests?.*?(\d+)\s+failures?", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
        ],
    },
    "rust": {
        "framework": "cargo test",
        "patterns": [
            (r"test result: \w+\.\s+(\d+)\s+passed;\s+(\d+)\s+failed", lambda m: (int(m.group(1)), int(m.group(2)))),
        ],
    },
    "dotnet": {
        "framework": "dotnet test",
        "patterns": [
            (r"Passed!\s+-\s+Failed:\s+(\d+),\s+Passed:\s+(\d+)", lambda m: (int(m.group(2)), int(m.group(1)))),
            (r"Failed!\s+-\s+Failed:\s+(\d+),\s+Passed:\s+(\d+)", lambda m: (int(m.group(2)), int(m.group(1)))),
            (r"Total tests:\s+(\d+)\s+Passed:\s+(\d+)\s+Failed:\s+(\d+)", lambda m: (int(m.group(2)), int(m.group(3)))),
        ],
    },
    "elixir": {
        "framework": "mix test",
        "patterns": [
            (r"(\d+)\s+tests?,\s+(\d+)\s+failures?", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
        ],
    },
    "scala": {
        "framework": "sbt test",
        "patterns": [
            (r"All tests passed", lambda m: (None, 0)),
            (r"(\d+)\s+tests?.*?(\d+)\s+failed", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
            (r"Tests:\s+succeeded\s+(\d+),\s+failed\s+(\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
        ],
    },
    "kotlin": {
        "framework": "gradle/junit",
        "patterns": [
            (r"(\d+)\s+tests?\s+completed,\s+(\d+)\s+failed", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
            (r"BUILD SUCCESSFUL", lambda m: (None, 0)),
        ],
    },
    "cpp": {
        "framework": "catch2/ctest",
        "patterns": [
            (r"(\d+)\s+tests?\s+from.*?(\d+)\s+test.*?passed", lambda m: (int(m.group(2)), int(m.group(1)) - int(m.group(2)))),
            (r"(\d+)\s+test.*?passed.*?(\d+)\s+test.*?failed", lambda m: (int(m.group(1)), int(m.group(2)))),
            (r"100% tests passed,\s+0\s+tests failed out of\s+(\d+)", lambda m: (int(m.group(1)), 0)),
            (r"(\d+)% tests passed,\s+(\d+)\s+tests failed out of\s+(\d+)", lambda m: (int(m.group(3)) - int(m.group(2)), int(m.group(2)))),
        ],
    },
    "liquid": {
        "framework": "rspec",
        "patterns": [
            (r"(\d+)\s+examples?,\s+(\d+)\s+failures?", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)))),
        ],
    },
    "react": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total", lambda m: (int(m.group(1)), int(m.group(2)) - int(m.group(1)))),
            (r"(\d+)\s+passed", lambda m: (int(m.group(1)), 0)),
        ],
    },
    "vue2": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total", lambda m: (int(m.group(1)), int(m.group(2)) - int(m.group(1)))),
            (r"(\d+)\s+passed", lambda m: (int(m.group(1)), 0)),
        ],
    },
    "vue3": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total", lambda m: (int(m.group(1)), int(m.group(2)) - int(m.group(1)))),
            (r"(\d+)\s+passed", lambda m: (int(m.group(1)), 0)),
        ],
    },
    "angular": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total", lambda m: (int(m.group(1)), int(m.group(2)) - int(m.group(1)))),
            (r"(\d+)\s+passed", lambda m: (int(m.group(1)), 0)),
        ],
    },
}

def discover_all_sdks():
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--services"],
            capture_output=True, text=True, check=True, timeout=10
        )
        return sorted(
            svc.replace("-sdk", "")
            for svc in result.stdout.strip().split("\n")
            if svc.endswith("-sdk")
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return sorted(UNIT_TEST_PARSERS.keys())


def parse_unit_test_output(sdk, output):
    parser = UNIT_TEST_PARSERS.get(sdk)
    if not parser:
        return None, None

    if parser.get("aggregate"):
        passed = 0
        failed = 0
        for line in output.split("\n"):
            for pattern, extractor in parser["patterns"]:
                m = re.search(pattern, line)
                if m:
                    p, f = extractor(m)
                    passed += p
                    failed += f
                    break
        if passed > 0 or failed > 0:
            return passed, failed
        return None, None

    for pattern, extractor in parser["patterns"]:
        m = re.search(pattern, output, re.DOTALL)
        if m:
            p, f = extractor(m)
            return p, f

    return None, None


def load_cross_sdk_report(report_path):
    if not report_path or not os.path.exists(report_path):
        return {}

    with open(report_path) as f:
        content = f.read().strip()
        if not content:
            return {}
        report = json.loads(content)

    results = {}

    if "sdk_stats" in report:
        for sdk, stats in report["sdk_stats"].items():
            passed = stats.get("passed", 0)
            failed = stats.get("failed", 0) + stats.get("errors", 0)
            tested = stats.get("tested", passed + failed)
            results[sdk] = {"passed": passed, "failed": failed, "total": tested}
        return results

    sdk_results = report.get("sdk_results", report.get("results", {}))
    for sdk, data in sdk_results.items():
        sdk_name = sdk.replace("-sdk", "")
        if isinstance(data, dict):
            passed = data.get("passed", 0)
            failed = data.get("failed", 0)
            total = data.get("total", passed + failed)
            results[sdk_name] = {"passed": passed, "failed": failed, "total": total}
        elif isinstance(data, list):
            passed = sum(1 for t in data if t.get("status") == "pass")
            failed = sum(1 for t in data if t.get("status") == "fail")
            results[sdk_name] = {"passed": passed, "failed": failed, "total": passed + failed}

    return results


GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def format_result(passed, failed, total):
    if passed is None:
        return "N/A"
    status = "PASS" if failed == 0 else "FAIL"
    return f"{passed}/{total} {status}"


def colorize_result(text):
    if "PASS" in text:
        return text.replace("PASS", f"{GREEN}PASS{RESET}")
    if "FAIL" in text:
        return text.replace("FAIL", f"{RED}FAIL{RESET}")
    return text


def visible_len(text):
    return len(re.sub(r"\033\[\d+m", "", text))


def pad_colored(text, width):
    padding = width - visible_len(text)
    return text + " " * max(padding, 0)


def print_results_table(unit_results, cross_sdk_results, all_sdks, has_unit, has_cross):
    COL_SDK = 17
    COL_DATA = 18

    columns = []
    if has_unit:
        columns.append("Unit Tests")
    if has_cross:
        columns.append("Cross-SDK Tests")

    top = f"┌{'─' * COL_SDK}{''.join('┬' + '─' * COL_DATA for _ in columns)}┐"
    mid = f"├{'─' * COL_SDK}{''.join('┼' + '─' * COL_DATA for _ in columns)}┤"
    bot = f"└{'─' * COL_SDK}{''.join('┴' + '─' * COL_DATA for _ in columns)}┘"

    def make_row(cells):
        parts = []
        for i, cell in enumerate(cells):
            width = COL_SDK if i == 0 else COL_DATA
            parts.append(f" {pad_colored(cell, width - 1)}")
        return f"│{'│'.join(parts)}│"

    print()
    print(top)
    print(make_row([f"{BOLD}SDK{RESET}"] + [f"{BOLD}{c}{RESET}" for c in columns]))
    print(mid)

    any_failure = False
    unit_sdk_pass = 0
    unit_sdk_total = 0
    cross_sdk_pass = 0
    cross_sdk_total = 0

    for sdk in all_sdks:
        cells = [sdk]

        if has_unit:
            unit = unit_results.get(sdk, {})
            u_passed = unit.get("passed")
            u_failed = unit.get("failed") or 0
            u_total = unit.get("total") or ((u_passed or 0) + u_failed)
            unit_str = format_result(u_passed, u_failed, u_total)
            cells.append(colorize_result(unit_str))
            if u_passed is not None:
                unit_sdk_total += 1
                if u_failed == 0:
                    unit_sdk_pass += 1
            if u_failed and u_failed > 0:
                any_failure = True

        if has_cross:
            cross = cross_sdk_results.get(sdk, {})
            c_passed = cross.get("passed")
            c_failed = cross.get("failed") or 0
            c_total = cross.get("total") or ((c_passed or 0) + c_failed)
            cross_str = format_result(c_passed, c_failed, c_total)
            cells.append(colorize_result(cross_str))
            if c_passed is not None:
                cross_sdk_total += 1
                if c_failed == 0:
                    cross_sdk_pass += 1
            if c_failed and c_failed > 0:
                any_failure = True

        print(make_row(cells))

    print(mid)

    total_cells = [f"{BOLD}TOTAL{RESET}"]
    if has_unit:
        total_str = format_result(unit_sdk_pass, unit_sdk_total - unit_sdk_pass, unit_sdk_total)
        total_cells.append(colorize_result(total_str))
    if has_cross:
        total_str = format_result(cross_sdk_pass, cross_sdk_total - cross_sdk_pass, cross_sdk_total)
        total_cells.append(colorize_result(total_str))
    print(make_row(total_cells))

    print(bot)
    return any_failure


def main():
    unit_results_file = sys.argv[1] if len(sys.argv) > 1 else None
    cross_sdk_report = sys.argv[2] if len(sys.argv) > 2 else "test-results/report.json"

    has_unit = unit_results_file and unit_results_file != "/dev/null"
    has_cross = cross_sdk_report and cross_sdk_report != "/dev/null"

    unit_results = {}
    if has_unit and os.path.exists(unit_results_file):
        with open(unit_results_file) as f:
            content = f.read().strip()
        if not content:
            raw_results = {}
        else:
            raw_results = json.loads(content)
        for sdk, data in raw_results.items():
            output = data.get("output", "")
            exit_code = data.get("exit_code", -1)
            passed, failed = parse_unit_test_output(sdk, output)
            if passed is None and exit_code == 0:
                passed = 0
                failed = 0
            unit_results[sdk] = {
                "passed": passed,
                "failed": failed,
                "total": (passed or 0) + (failed or 0),
                "exit_code": exit_code,
            }

    cross_sdk_results = load_cross_sdk_report(cross_sdk_report) if has_cross else {}

    all_sdks = sorted(set(list(unit_results.keys()) + list(cross_sdk_results.keys())))
    if not all_sdks:
        all_sdks = discover_all_sdks()
    any_failure = print_results_table(unit_results, cross_sdk_results, all_sdks, has_unit, has_cross)

    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()
