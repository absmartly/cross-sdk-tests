#!/usr/bin/env python3
import json
import re
import sys
import os

UNIT_TEST_PARSERS = {
    "javascript": {
        "framework": "jest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(3)))),
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(1)), 0, int(m.group(2)))),
            (r"(\d+)\s+passing", lambda m: (int(m.group(1)), 0, None)),
        ],
    },
    "python": {
        "framework": "pytest",
        "custom_parser": "pytest",
    },
    "ruby": {
        "framework": "rspec",
        "patterns": [
            (r"(\d+)\s+examples?,\s+(\d+)\s+failures?(?:,\s+(\d+)\s+pending)?",
             lambda m: (int(m.group(1)) - int(m.group(2)) - int(m.group(3) or 0), int(m.group(2)), int(m.group(1)))),
        ],
    },
    "java": {
        "framework": "gradle/junit",
        "custom_parser": "gradle_junit",
    },
    "php": {
        "framework": "phpunit",
        "patterns": [
            (r"OK\s+\((\d+)\s+tests?,\s+(\d+)\s+assertions?\)", lambda m: (int(m.group(1)), 0, None)),
            (r"OK\s+\((\d+)\s+tests?", lambda m: (int(m.group(1)), 0, None)),
            (r"Tests:\s+(\d+).*?Failures:\s+(\d+)", lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)), None)),
        ],
    },
    "go": {
        "framework": "go test",
        "patterns": [
            (r"^--- PASS:", lambda m: (1, 0, None)),
            (r"^--- FAIL:", lambda m: (0, 1, None)),
            (r"^--- SKIP:", lambda m: (0, 0, None)),
        ],
        "aggregate": True,
        "skip_pattern": r"^--- SKIP:",
    },
    "dart": {
        "framework": "dart test",
        "line_search": True,
        "patterns": [
            (r"\+(\d+)(?:\s+~(\d+))?\s+-(\d+):\s+Some tests failed",
             lambda m: (int(m.group(1)), int(m.group(3)),
                        int(m.group(1)) + int(m.group(3)) + int(m.group(2) or 0))),
            (r"\+(\d+)(?:\s+~(\d+))?:\s+All tests passed",
             lambda m: (int(m.group(1)), 0, int(m.group(1)) + int(m.group(2) or 0))),
        ],
    },
    "flutter": {
        "framework": "flutter test",
        "line_search": True,
        "patterns": [
            (r"\+(\d+)(?:\s+~(\d+))?\s+-(\d+):\s+Some tests failed",
             lambda m: (int(m.group(1)), int(m.group(3)),
                        int(m.group(1)) + int(m.group(3)) + int(m.group(2) or 0))),
            (r"\+(\d+)(?:\s+~(\d+))?:\s+All tests passed",
             lambda m: (int(m.group(1)), 0, int(m.group(1)) + int(m.group(2) or 0))),
        ],
    },
    "swift": {
        "framework": "swift test",
        "patterns": [
            (r"Test Case '.*?' passed", lambda m: (1, 0, None)),
            (r"Test Case '.*?' failed", lambda m: (0, 1, None)),
        ],
        "aggregate": True,
    },
    "rust": {
        "framework": "cargo test",
        "patterns": [
            (r"test result: \w+\.\s+(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored",
             lambda m: (int(m.group(1)), int(m.group(2)),
                        int(m.group(1)) + int(m.group(2)) + int(m.group(3)))),
            (r"test result: \w+\.\s+(\d+)\s+passed;\s+(\d+)\s+failed",
             lambda m: (int(m.group(1)), int(m.group(2)), None)),
        ],
        "aggregate": True,
    },
    "dotnet": {
        "framework": "dotnet test",
        "patterns": [
            (r"Failed:\s+(\d+),\s+Passed:\s+(\d+),\s+Skipped:\s+(\d+),\s+Total:\s+(\d+)",
             lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(4)))),
            (r"Total tests:\s+(\d+)\s+Passed:\s+(\d+)\s+Failed:\s+(\d+)",
             lambda m: (int(m.group(2)), int(m.group(3)), int(m.group(1)))),
        ],
    },
    "elixir": {
        "framework": "mix test",
        "patterns": [
            (r"(\d+)\s+tests?,\s+(\d+)\s+failures?(?:,\s+(\d+)\s+(?:excluded|skipped))?",
             lambda m: (int(m.group(1)) - int(m.group(2)), int(m.group(2)),
                        int(m.group(1)) + int(m.group(3) or 0))),
        ],
    },
    "scala": {
        "framework": "sbt test",
        "patterns": [
            (r"Tests:\s+succeeded\s+(\d+),\s+failed\s+(\d+),\s+canceled\s+(\d+),\s+ignored\s+(\d+),\s+pending\s+(\d+)",
             lambda m: (int(m.group(1)), int(m.group(2)),
                        sum(int(m.group(i)) for i in range(1, 6)))),
            (r"Total number of tests run:\s+(\d+)", lambda m: (int(m.group(1)), 0, None)),
        ],
    },
    "kotlin": {
        "framework": "gradle/junit",
        "custom_parser": "gradle_junit",
    },
    "cpp": {
        "framework": "catch2",
        "custom_parser": "catch2_junit",
    },
    "liquid": {
        "framework": "rspec",
        "patterns": [
            (r"(\d+)\s+examples?,\s+(\d+)\s+failures?(?:,\s+(\d+)\s+pending)?",
             lambda m: (int(m.group(1)) - int(m.group(2)) - int(m.group(3) or 0), int(m.group(2)), int(m.group(1)))),
        ],
    },
    "react": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(3)))),
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(1)), 0, int(m.group(2)))),
            (r"Tests\s+(\d+)\s+passed\s+\|?\s*(\d+)\s+skipped\s+\((\d+)\)",
             lambda m: (int(m.group(1)), 0, int(m.group(3)))),
            (r"Tests\s+(\d+)\s+passed\s+\((\d+)\)",
             lambda m: (int(m.group(1)), 0, int(m.group(2)))),
            (r"Tests\s+(\d+)\s+passed", lambda m: (int(m.group(1)), 0, None)),
        ],
    },
    "vue2": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(3)))),
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(1)), 0, int(m.group(2)))),
            (r"Tests\s+(\d+)\s+passed", lambda m: (int(m.group(1)), 0, None)),
        ],
    },
    "vue3": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(3)))),
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(1)), 0, int(m.group(2)))),
            (r"Tests\s+(\d+)\s+passed", lambda m: (int(m.group(1)), 0, None)),
        ],
    },
    "angular": {
        "framework": "jest/vitest",
        "patterns": [
            (r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(3)))),
            (r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total",
             lambda m: (int(m.group(1)), 0, int(m.group(2)))),
            (r"Tests\s+(\d+)\s+passed", lambda m: (int(m.group(1)), 0, None)),
        ],
    },
}


def parse_catch2_junit(output):
    output = strip_ansi(output)
    total = len(re.findall(r'<testcase\b', output))
    if total > 0:
        failed = len(re.findall(r'<failure\b', output))
        return total - failed, failed, total
    m = re.search(r'(\d+)% tests passed,\s+(\d+)\s+tests failed out of\s+(\d+)', output)
    if m:
        return int(m.group(3)) - int(m.group(2)), int(m.group(2)), int(m.group(3))
    m = re.search(r'100% tests passed,\s+0\s+tests failed out of\s+(\d+)', output)
    if m:
        return int(m.group(1)), 0, int(m.group(1))
    return None, None, None


def parse_gradle_junit(output):
    """Parse Gradle/JUnit test output.

    On failure Gradle prints a summary line ("N tests completed, M failed");
    on success it prints no summary at all, only per-test lines
    ("ClassName > testName() PASSED/FAILED/SKIPPED") followed by BUILD SUCCESSFUL.
    Prefer the summary when present (authoritative); otherwise tally the
    per-test result lines.
    """
    output = strip_ansi(output)

    m = re.search(r"(\d+)\s+tests?\s+completed,\s+(\d+)\s+failed(?:,\s+(\d+)\s+skipped)?", output)
    if m:
        completed = int(m.group(1))
        failed = int(m.group(2))
        skipped = int(m.group(3) or 0)
        return completed - failed, failed, completed + skipped

    passed = len(re.findall(r"\bPASSED\s*$", output, re.MULTILINE))
    failed = len(re.findall(r"\bFAILED\s*$", output, re.MULTILINE))
    skipped = len(re.findall(r"\bSKIPPED\s*$", output, re.MULTILINE))
    if passed or failed or skipped:
        return passed, failed, passed + failed + skipped

    return None, None, None


def parse_pytest_summary(output):
    output = strip_ansi(output)
    matches = re.findall(r"=+\s+([\d\w,. ]+)\s+=+\s*$", output, re.MULTILINE)
    if matches:
        summary = matches[-1]
    else:
        m = re.search(r"(\d+\s+passed.*)", output)
        if not m:
            return None, None, None
        summary = m.group(1)
    passed = int(g.group(1)) if (g := re.search(r"(\d+)\s+passed", summary)) else 0
    failed = int(g.group(1)) if (g := re.search(r"(\d+)\s+failed", summary)) else 0
    skipped = int(g.group(1)) if (g := re.search(r"(\d+)\s+skipped", summary)) else 0
    errors = int(g.group(1)) if (g := re.search(r"(\d+)\s+error", summary)) else 0
    total = passed + failed + skipped + errors
    return passed, failed + errors, total


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


def strip_ansi(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


def normalize_result(result):
    if len(result) == 3:
        p, f, t = result
        if t is None:
            t = p + f
        return p, f, t
    p, f = result
    return p, f, p + f


def parse_unit_test_output(sdk, output):
    parser = UNIT_TEST_PARSERS.get(sdk)
    if not parser:
        return None, None, None

    output = strip_ansi(output)

    if parser.get("custom_parser") == "pytest":
        return parse_pytest_summary(output)

    if parser.get("custom_parser") == "catch2_junit":
        return parse_catch2_junit(output)

    if parser.get("custom_parser") == "gradle_junit":
        return parse_gradle_junit(output)

    if parser.get("aggregate"):
        passed = 0
        failed = 0
        total_from_patterns = 0
        has_explicit_total = False
        skip_pattern = parser.get("skip_pattern")
        skipped = 0
        for line in output.split("\n"):
            if skip_pattern and re.search(skip_pattern, line):
                skipped += 1
                continue
            for pattern, extractor in parser["patterns"]:
                m = re.search(pattern, line)
                if m:
                    result = normalize_result(extractor(m))
                    p, f, t = result
                    passed += p
                    failed += f
                    if t != p + f:
                        has_explicit_total = True
                        total_from_patterns += t
                    else:
                        total_from_patterns += p + f
                    break
        if passed > 0 or failed > 0 or skipped > 0:
            total = total_from_patterns + skipped if not has_explicit_total else total_from_patterns
            return passed, failed, total
        return None, None, None

    if parser.get("line_search"):
        lines = re.split(r"[\r\n]+", output)
        for line in reversed(lines):
            for pattern, extractor in parser["patterns"]:
                m = re.search(pattern, line)
                if m:
                    return normalize_result(extractor(m))
        return None, None, None

    for pattern, extractor in parser["patterns"]:
        m = re.search(pattern, output, re.DOTALL)
        if m:
            return normalize_result(extractor(m))

    return None, None, None


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
            skipped = stats.get("skipped", 0)
            results[sdk] = {"passed": passed, "failed": failed, "total": tested, "skipped": skipped}
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
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def format_result(passed, failed, total, skipped=0):
    if passed is None:
        return "N/A"
    implicit_skipped = total - passed - failed
    status = "PASS" if failed == 0 else "FAIL"
    base = f"{passed}/{total} {status}"
    if implicit_skipped > 0:
        return f"{base} ({implicit_skipped}s)"
    if skipped > 0:
        return f"{base} ({skipped}s)"
    return base


def colorize_result(text):
    if "PASS" in text:
        text = text.replace("PASS", f"{GREEN}PASS{RESET}")
    if "FAIL" in text:
        text = text.replace("FAIL", f"{RED}FAIL{RESET}")
    if "s)" in text:
        text = re.sub(r"\((\d+s)\)", f"({YELLOW}\\1{RESET})", text)
    return text


def visible_len(text):
    return len(re.sub(r"\033\[\d+m", "", text))


def pad_colored(text, width):
    padding = width - visible_len(text)
    return text + " " * max(padding, 0)


def print_results_table(unit_results, cross_sdk_results, all_sdks, has_unit, has_cross):
    COL_SDK = 17
    COL_DATA = 22

    columns = []
    if has_unit:
        columns.append("Unit Tests")
    if has_cross:
        columns.append("Cross-SDK Tests")

    top = f"\u250c{'─' * COL_SDK}{''.join('┬' + '─' * COL_DATA for _ in columns)}┐"
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
            c_skipped = cross.get("skipped") or 0
            cross_str = format_result(c_passed, c_failed, c_total, c_skipped)
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
            passed, failed, total = parse_unit_test_output(sdk, output)
            if passed is None and exit_code == 0:
                passed = 0
                failed = 0
                total = 0
            unit_results[sdk] = {
                "passed": passed,
                "failed": failed,
                "total": total if total is not None else (passed or 0) + (failed or 0),
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
