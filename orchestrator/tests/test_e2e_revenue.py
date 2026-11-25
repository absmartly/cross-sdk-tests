"""Unit tests for e2e_runner revenue verification.

Revenue must come from the e2e_purchase_revenue (goal_property) metric, summed
as `count` across variants. It is never computed from the goal count; if it
cannot be measured the SDK is UNVERIFIED and the run fails.

Run from the repository root:  python3 -m pytest orchestrator/tests -q
"""

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "orchestrator"))

import e2e_runner  # noqa: E402
from e2e_runner import E2ERunner  # noqa: E402

EXP_ID = 4242
COUNT_METRIC_ID = "100"
REVENUE_METRIC_ID = "2914"


def _variants(units, counts):
    return [
        {"variant": i, "unit_count": u, "mean": (c / u if u else 0), "count": c}
        for i, (u, c) in enumerate(zip(units, counts))
    ]


def count_result(units, goals):
    return [{"metric_id": int(COUNT_METRIC_ID), "name": "e2e_purchase_count", "type": "primary",
             "variants": _variants(units, goals)}]


def revenue_result(units, revenue):
    return [{"metric_id": int(REVENUE_METRIC_ID), "name": "e2e_purchase_revenue", "type": "custom",
             "variants": _variants(units, revenue)}]


def sdk_from_filter(args):
    """The sdk_name a --filter arg targets, or None for an unfiltered query."""
    if "--filter" not in args:
        return None
    payload = json.loads(args[args.index("--filter") + 1])
    return payload["filter"]["and"][0]["eq"][1]["value"]


class FakeBackend:
    """Stands in for run_abs: answers `experiments metrics results` calls.

    count[sdk] / revenue[sdk] map an sdk name (None = aggregate) to either a
    parsed result list, or an int return code to simulate a failed query.
    """

    def __init__(self, count=None, revenue=None):
        self.count = count or {}
        self.revenue = revenue or {}
        self.calls = []

    def __call__(self, args, profile="", api_key="", endpoint="", output_json=True):
        self.calls.append(list(args))
        assert args[:3] == ["experiments", "metrics", "results"], args
        assert args[3] == str(EXP_ID)
        sdk = sdk_from_filter(args)
        if "--metric" in args:
            assert args[args.index("--metric") + 1] == REVENUE_METRIC_ID
            table = self.revenue
        else:
            table = self.count
        resp = table.get(sdk, [])
        if isinstance(resp, int):
            return resp, "Error: backend exploded"
        return 0, json.dumps(resp)


def make_runner(sdks=("java",), units=100, revenue_metric_id=REVENUE_METRIC_ID):
    runner = E2ERunner({s: f"http://{s}-sdk:3000" for s in sdks}, {"profile": "e2e", "units": units})
    runner.experiment_id = EXP_ID
    runner.experiment_name = "e2e-test"
    runner.metric_id = COUNT_METRIC_ID
    runner.revenue_metric_id = revenue_metric_id
    for s in sdks:
        runner.sdk_results[s] = {
            "status": "ok",
            "exposures_sent": units,
            "goals_sent": units // 2,
            "revenue_sent": units // 2 * 10,
            "planned_exposures": units,
            "planned_goals": units // 2,
            "context_errors": 0,
            "treatment_errors": 0,
            "track_errors": 0,
        }
    return runner


def healthy_backend(sdks=("java",), revenue_per_sdk=(270, 230)):
    n = len(sdks)
    count = {None: count_result([57 * n, 43 * n], [27 * n, 23 * n])}
    revenue = {None: revenue_result([57 * n, 43 * n], [r * n for r in revenue_per_sdk])}
    for s in sdks:
        count[s] = count_result([57, 43], [27, 23])
        revenue[s] = revenue_result([57, 43], list(revenue_per_sdk))
    return FakeBackend(count, revenue)


def run_verify(monkeypatch, capsys, runner, backend):
    monkeypatch.setattr(e2e_runner, "run_abs", backend)
    results = runner.verify_metrics()
    runner.print_report(results)
    out = capsys.readouterr().out
    return results, out


def test_revenue_total_sums_count_across_variants():
    assert E2ERunner._revenue_total(revenue_result([57, 43], [270, 230])[0]) == (500, None)


def test_revenue_total_without_count_is_unmeasured():
    no_count = {"metric_id": 2914, "variants": [{"variant": 0, "unit_count": 57, "mean": 4.7}]}
    assert E2ERunner._revenue_total(no_count)[0] is None
    assert E2ERunner._revenue_total({"metric_id": 2914, "variants": []})[0] is None
    assert E2ERunner._revenue_total(None)[0] is None


def test_revenue_queries_use_revenue_metric_with_and_without_filter(monkeypatch, capsys):
    runner = make_runner(sdks=("java", "go"))
    backend = healthy_backend(sdks=("java", "go"))
    run_verify(monkeypatch, capsys, runner, backend)

    revenue_calls = [c for c in backend.calls if "--metric" in c]
    assert sorted(str(sdk_from_filter(c)) for c in revenue_calls) == ["None", "go", "java"]
    # The plain count queries never pass --metric, so they keep returning the
    # experiment's primary (count) metric.
    count_calls = [c for c in backend.calls if "--metric" not in c]
    assert count_calls and all("--metric" not in c for c in count_calls)


def test_measured_revenue_passes(monkeypatch, capsys):
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, healthy_backend())

    java = results["sdks"]["java"]
    assert java["status"] == "ok"
    assert java["revenue"] == {"expected": 500, "actual": 500, "pass": True}
    assert results["aggregate"]["actual_revenue"] == 500
    assert results["overall_pass"] is True
    assert "PASS" in out and "All metrics verified" in out
    assert "500/500 PASS" in out


def test_wrong_revenue_fails(monkeypatch, capsys):
    runner = make_runner()
    # Goals are right (50) but only 400 of revenue landed: must fail, not be
    # papered over by the goal count.
    results, out = run_verify(monkeypatch, capsys, runner, healthy_backend(revenue_per_sdk=(220, 180)))

    java = results["sdks"]["java"]
    assert java["goals"]["pass"] is True
    assert java["revenue"]["actual"] == 400
    assert java["revenue"]["pass"] is False
    assert java["status"] == "fail"
    assert results["overall_pass"] is False
    assert "All metrics verified" not in out
    assert "java revenue: 400/500 FAIL" in out


def test_over_published_revenue_fails(monkeypatch, capsys):
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, healthy_backend(revenue_per_sdk=(540, 460)))
    assert results["sdks"]["java"]["revenue"]["pass"] is False
    assert "over-published revenue" in (results["sdks"]["java"]["note"] or "")
    assert results["overall_pass"] is False


@pytest.mark.parametrize(
    "case",
    ["query_failed", "empty", "no_count_field", "wrong_metric", "metric_missing"],
)
def test_unmeasurable_revenue_is_unverified_and_fails(monkeypatch, capsys, case):
    revenue_metric_id = REVENUE_METRIC_ID
    backend = healthy_backend()
    if case == "query_failed":
        backend.revenue["java"] = 1
    elif case == "empty":
        backend.revenue["java"] = []
    elif case == "no_count_field":
        backend.revenue["java"] = [{"metric_id": 2914, "variants": [{"variant": 0, "unit_count": 57}]}]
    elif case == "wrong_metric":
        backend.revenue["java"] = count_result([57, 43], [27, 23])
    elif case == "metric_missing":
        revenue_metric_id = None

    runner = make_runner(revenue_metric_id=revenue_metric_id)
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    java = results["sdks"]["java"]
    assert java["exposures"]["pass"] is True
    assert java["goals"]["pass"] is True
    assert java["revenue"]["actual"] is None
    assert java["revenue"]["unverified"] is True
    assert java["revenue"]["pass"] is False
    assert java["status"] == "fail"
    assert java["note"].startswith("unverified: revenue ")
    assert results["overall_pass"] is False
    assert E2ERunner.report_passes(results) is False
    assert "All metrics verified" not in out
    assert "UNVERIFIED" in out
    assert "FAIL" in out
    if case == "metric_missing":
        assert not [c for c in backend.calls if "--metric" in c]


def test_unverified_segmentation_fails_even_when_aggregate_passes(monkeypatch, capsys):
    backend = healthy_backend()
    backend.count["java"] = 1
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    java = results["sdks"]["java"]
    assert java["status"] == "fail"
    assert java["note"].startswith("unverified: ")
    assert java["exposures"]["unverified"] is True
    assert results["aggregate"]["pass"] is True
    assert results["overall_pass"] is False
    assert "All metrics verified" not in out


def test_one_unverified_sdk_fails_whole_run(monkeypatch, capsys):
    sdks = ("java", "go")
    backend = healthy_backend(sdks=sdks)
    backend.revenue["go"] = 2
    runner = make_runner(sdks=sdks)
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    assert results["sdks"]["java"]["status"] == "ok"
    assert results["sdks"]["go"]["status"] == "fail"
    assert results["sdks"]["go"]["revenue"]["unverified"] is True
    assert results["overall_pass"] is False
    assert "go revenue: UNVERIFIED" in out
    assert "All metrics verified" not in out


def test_revenue_never_computed_from_goals(monkeypatch, capsys):
    """Goals are perfect, revenue is missing: revenue must stay unmeasured."""
    backend = healthy_backend()
    backend.revenue = {}
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    assert results["sdks"]["java"]["goals"]["actual"] == 50
    assert results["sdks"]["java"]["revenue"]["actual"] is None
    assert results["aggregate"]["actual_revenue"] is None
    assert "500/500" not in out


def test_unmeasured_revenue_report_has_no_computed_figure(monkeypatch, capsys):
    # Built from parts so this guard does not itself match a grep for the word.
    banned = "der" + "ived"
    backend = healthy_backend()
    backend.revenue["java"] = []
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    assert banned not in json.dumps(results).lower()
    assert banned not in out.lower()
    assert results["sdks"]["java"]["revenue"]["actual"] is None
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    java_row = next(line for line in plain.splitlines() if line.strip().startswith("java "))
    assert "?/500 FAIL (unverified)" in java_row
    assert "500/500 PASS" not in java_row
    assert results["aggregate"]["actual_revenue"] == 500
    assert "Backend aggregate (info): participants 100/100, goals 50/50, revenue 500/500" in plain


def test_main_exits_nonzero_when_unverified(monkeypatch, capsys):
    backend = healthy_backend()
    backend.revenue["java"] = 1

    def fake_run(self):
        self.experiment_id = EXP_ID
        self.metric_id = COUNT_METRIC_ID
        self.revenue_metric_id = REVENUE_METRIC_ID
        self.sdk_results = make_runner().sdk_results
        results = self.verify_metrics()
        self.print_report(results)
        return results

    monkeypatch.setattr(e2e_runner, "run_abs", backend)
    monkeypatch.setattr(E2ERunner, "run", fake_run)
    monkeypatch.setenv("SDK_URLS_OVERRIDE", "java=http://java-sdk:3000")
    monkeypatch.setattr(sys, "argv", ["e2e_runner.py"])
    with pytest.raises(SystemExit) as exc:
        e2e_runner.main()
    assert exc.value.code != 0
    assert "All metrics verified" not in capsys.readouterr().out


class _ProvisioningBackend:
    def __init__(self, existing=None, create_output='{"id": 2914}'):
        self.existing = existing or []
        self.create_output = create_output
        self.calls = []

    def __call__(self, args, profile="", api_key="", endpoint="", output_json=True):
        self.calls.append(list(args))
        if args[:2] == ["metrics", "list"]:
            return 0, json.dumps(self.existing)
        if args[:2] == ["metrics", "get"]:
            raw = {"id": int(args[2]), "name": "e2e_purchase_revenue", "type": "goal_property",
                   "value_source_property": "amount", "goal": {"name": "purchase"}}
            if "--raw" not in args:
                # The CLI summary: no value_source_property, goal as a string.
                raw = {"id": raw["id"], "name": raw["name"], "type": raw["type"], "goal": "purchase"}
            return 0, json.dumps(raw)
        if args[:2] == ["metrics", "create"]:
            return 0, self.create_output
        return 0, ""


def test_ensure_revenue_metric_finds_existing(monkeypatch):
    backend = _ProvisioningBackend(existing=[{"id": 100, "name": "e2e_purchase_count"},
                                             {"id": 2914, "name": "e2e_purchase_revenue"}])
    monkeypatch.setattr(e2e_runner, "run_abs", backend)
    runner = make_runner(revenue_metric_id=None)
    runner._ensure_revenue_metric()
    assert runner.revenue_metric_id == "2914"
    assert not [c for c in backend.calls if c[:2] == ["metrics", "create"]]


def test_ensure_revenue_metric_creates_reviews_and_activates(monkeypatch):
    backend = _ProvisioningBackend(existing=[{"id": 100, "name": "e2e_purchase_count"}])
    monkeypatch.setattr(e2e_runner, "run_abs", backend)
    runner = make_runner(revenue_metric_id=None)
    runner.owner_id = "7"
    runner._ensure_revenue_metric()

    assert runner.revenue_metric_id == "2914"
    create = next(c for c in backend.calls if c[:2] == ["metrics", "create"])
    opts = dict(zip(create[2::2], create[3::2]))
    assert opts["--name"] == "e2e_purchase_revenue"
    assert opts["--type"] == "goal_property"
    assert opts["--goal"] == "purchase"
    assert opts["--value-source-property"] == "amount"
    assert opts["--owner"] == "7"
    assert opts["--description"]
    tail = [c for c in backend.calls if c[0] == "metrics" and c[1] in ("review", "activate")]
    assert tail == [
        ["metrics", "review", "request", "2914"],
        ["metrics", "review", "approve", "2914"],
        ["metrics", "activate", "2914", "--reason", "E2E testing"],
    ]


def test_ensure_resources_calls_revenue_metric(monkeypatch):
    runner = make_runner(revenue_metric_id=None)
    order = []
    for name in ("_ensure_application", "_ensure_unit_type", "_ensure_owner", "_ensure_goal",
                 "_ensure_metric", "_ensure_revenue_metric"):
        monkeypatch.setattr(runner, name, (lambda n: lambda: order.append(n))(name))
    runner.ensure_resources()
    assert order.index("_ensure_revenue_metric") == order.index("_ensure_metric") + 1


def test_experiment_creation_does_not_attach_revenue_metric(monkeypatch):
    calls = []

    def fake(args, profile="", api_key="", endpoint="", output_json=True):
        calls.append(list(args))
        return 0, '{"id": 4242}'

    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner()
    runner.app_id, runner.unit_type, runner.owner_id = "1", "2", "3"
    runner.experiment_id = None
    runner.create_experiment()
    create = calls[0]
    assert create[create.index("--primary-metric") + 1] == COUNT_METRIC_ID
    assert REVENUE_METRIC_ID not in create


@pytest.mark.parametrize("action", ["create", "request", "approve", "activate"])
def test_revenue_provisioning_stops_on_failed_action(monkeypatch, capsys, action):
    backend = _ProvisioningBackend()
    calls = []
    failed_command = []
    output = "original backend error\nwith diagnostic details"

    def fail_action(args, *a, **kw):
        calls.append(list(args))
        current = args[2] if args[:2] == ["metrics", "review"] else args[1]
        if current == action:
            failed_command.extend(args)
            return 17, output
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", fail_action)
    runner = make_runner(revenue_metric_id=None)
    runner.verbose = True
    with pytest.raises(RuntimeError) as exc:
        runner._ensure_revenue_metric()
    assert action in str(exc.value)
    assert output in str(exc.value)
    if action != "create":
        assert " ".join(failed_command) in str(exc.value)
        assert "rc=17" in str(exc.value)
    assert calls[-1] == failed_command
    assert "Created and activated" not in capsys.readouterr().out


@pytest.mark.parametrize("definition", ["valid", "wrong_type", "wrong_property", "wrong_goal", "unreadable"])
def test_revenue_creation_race_checks_existing_definition(monkeypatch, definition):
    backend = _ProvisioningBackend()
    calls = []

    def raced(args, *a, **kw):
        calls.append(list(args))
        if args[:2] == ["metrics", "list"]:
            existing = [] if len(calls) == 1 else [{"id": 2914, "name": "e2e_purchase_revenue"}]
            return 0, json.dumps(existing)
        if args[:2] == ["metrics", "create"]:
            return 1, "metric already exists"
        if args[:2] == ["metrics", "get"]:
            if definition == "unreadable":
                return 1, "read failed"
            rc, output = backend(args, *a, **kw)
            metric = json.loads(output)
            metric.update({
                "wrong_type": {"type": "goal_unique_count"},
                "wrong_property": {"value_source_property": "price"},
                "wrong_goal": {"goal": {"name": "checkout"}},
            }.get(definition, {}))
            return rc, json.dumps(metric)
        pytest.fail(f"Unexpected provisioning action after race: {args}")

    monkeypatch.setattr(e2e_runner, "run_abs", raced)
    runner = make_runner(revenue_metric_id=None)
    if definition == "valid":
        runner._ensure_revenue_metric()
        assert runner.revenue_metric_id == REVENUE_METRIC_ID
    else:
        with pytest.raises(RuntimeError, match="cannot measure revenue|check its definition"):
            runner._ensure_revenue_metric()
    assert calls[-1] == ["metrics", "get", REVENUE_METRIC_ID, "--raw"]


def test_aggregate_is_read_after_segments_advance(monkeypatch, capsys):
    sdks = ("java", "go")
    backend = healthy_backend(sdks=sdks)
    segmented_reads = set()

    def advancing(args, *a, **kw):
        sdk = sdk_from_filter(args)
        if sdk is not None:
            segmented_reads.add((sdk, "revenue" if "--metric" in args else "count"))
        elif len(segmented_reads) < 2 * len(sdks):
            if "--metric" in args:
                return 0, json.dumps(revenue_result([57, 43], [270, 230]))
            return 0, json.dumps(count_result([57, 43], [27, 23]))
        return backend(args, *a, **kw)

    assert json.loads(advancing(["--metric", REVENUE_METRIC_ID])[1])[0]["variants"][0]["count"] == 270
    runner = make_runner(sdks=sdks)
    results, _ = run_verify(monkeypatch, capsys, runner, advancing)
    assert results["aggregate"]["actual_revenue"] == 1000
    assert results["aggregate"]["actual_participants"] == 200
    assert results["overall_pass"] is True
    assert E2ERunner.report_passes(results) is True
    assert all(sdk_from_filter(c) is None for c in backend.calls[-2:])


@pytest.fixture
def poll_clock(monkeypatch):
    class Clock:
        now = 0
        reads = 0

        def monotonic(self):
            self.reads += 1
            assert self.reads <= 100, "polling did not terminate at its deadline"
            return self.now

        def sleep(self, seconds):
            assert 0 < seconds <= 5
            self.now += seconds
            assert self.now <= 12, "polling exceeded its timeout"

    clock = Clock()
    monkeypatch.setattr(e2e_runner.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(e2e_runner.time, "sleep", clock.sleep)
    return clock


@pytest.mark.parametrize("delayed", ["participants", "goals", "revenue"])
def test_wait_polls_each_segmented_metric_until_ready(monkeypatch, capsys, poll_clock, delayed):
    sdks = ("java", "go")
    backend = healthy_backend(sdks=sdks)
    runner = make_runner(sdks=sdks)
    runner.timeout = 12
    runner.sdk_results["skipped"] = {"status": "skipped"}
    runner.sdk_results["errored"] = {"status": "error"}

    def delayed_results(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        sdk = sdk_from_filter(args)
        assert sdk in sdks, "aggregate/inactive SDK must not gate readiness"
        if sdk == "go" and poll_clock.now == 0:
            if delayed == "revenue" and "--metric" in args:
                backend.calls.append(list(args))
                return 0, "[]"
            if delayed != "revenue" and "--metric" not in args:
                backend.calls.append(list(args))
                units = [57, 42] if delayed == "participants" else [57, 43]
                goals = [27, 22] if delayed == "goals" else [27, 23]
                return 0, json.dumps(count_result(units, goals))
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", delayed_results)
    runner.wait_for_metrics()
    assert poll_clock.now == 5
    assert len(backend.calls) == 8
    out = capsys.readouterr().out
    assert "Metrics ready" in out
    assert "Metrics incomplete" not in out
    monkeypatch.setattr(e2e_runner, "run_abs", backend)
    results = runner.verify_metrics()
    assert all(results["sdks"][sdk]["status"] == "ok" for sdk in sdks)
    assert E2ERunner.report_passes(results) is False  # The errored SDK still fails the run.


@pytest.mark.parametrize("missing", ["empty", "query_failed", "short", "no_metric_id"])
def test_wait_revenue_timeout_is_bounded_and_verification_fails(monkeypatch, capsys, poll_clock, missing):
    backend = healthy_backend()
    backend.revenue["java"] = {
        "empty": [], "query_failed": 1,
        "short": revenue_result([57, 43], [270, 220]),
    }.get(missing, [])
    runner = make_runner(revenue_metric_id=None if missing == "no_metric_id" else REVENUE_METRIC_ID)
    runner.timeout = 12

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    assert poll_clock.now == 12
    count_calls = [c for c in backend.calls if "--metric" not in c]
    assert len(count_calls) == 3
    assert all(sdk_from_filter(c) == "java" for c in backend.calls)
    out = capsys.readouterr().out
    assert "Metrics incomplete" in out and "java revenue" in out
    assert "Metrics ready" not in out
    results = runner.verify_metrics()
    assert results["overall_pass"] is False
    assert E2ERunner.report_passes(results) is False


@pytest.mark.parametrize("sdks", [("java",), ("java", "go")])
def test_totals_keep_measured_columns_when_only_revenue_missing(monkeypatch, capsys, sdks):
    backend = healthy_backend(sdks=sdks)
    backend.revenue["java"] = []
    runner = make_runner(sdks=sdks)
    results, out = run_verify(monkeypatch, capsys, runner, backend)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    total = next(line for line in plain.splitlines() if line.strip().startswith("TOTAL "))
    n = len(sdks)
    assert f"{100 * n}/{100 * n} PASS" in total
    assert f"{50 * n}/{50 * n} PASS" in total
    assert f"{500 * (n - 1)}/{500 * n} FAIL (partial/unverified)" in total
    assert "lower bounds" in plain
    assert "All metrics verified" not in plain
    assert results["overall_pass"] is False


def test_partial_total_never_passes_even_if_lower_bound_reaches_expected():
    runner = make_runner()
    total = runner._format_total(500, 500, partial=True)
    assert "FAIL (partial/unverified)" in total
    assert "PASS" not in total


def test_wait_does_not_start_queries_after_the_deadline(monkeypatch, capsys, poll_clock):
    # Each real query can take up to run_abs's timeout; queries that advance the clock
    # past the deadline mid-round must stop further SDKs being polled in that round.
    sdks = ("java", "go", "php", "dotnet")
    backend = healthy_backend(sdks=sdks)
    runner = make_runner(sdks=sdks)
    runner.timeout = 6
    started_after_deadline = []

    def slow(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        if poll_clock.now >= 6:
            started_after_deadline.append(list(args))
        poll_clock.now += 4  # a slow query
        return 0, "[]"  # never ready

    monkeypatch.setattr(e2e_runner, "run_abs", slow)
    monkeypatch.setattr(e2e_runner.time, "sleep", lambda s: None)
    runner.wait_for_metrics()
    assert started_after_deadline == []
    assert "Metrics incomplete" in capsys.readouterr().out


def test_wait_skips_sdks_whose_wrapper_errors_already_fail_them(monkeypatch, capsys, poll_clock):
    backend = healthy_backend(sdks=("java",))
    runner = make_runner(sdks=("java",))
    runner.timeout = 12
    runner.sdk_results["java"]["treatment_errors"] = 100
    calls = []

    def record(args, *a, **kw):
        calls.append(list(args))
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", record)
    runner.wait_for_metrics()
    assert poll_clock.now == 0
    assert not [c for c in calls if c[:3] == ["experiments", "metrics", "results"]]
    assert "No active SDKs" in capsys.readouterr().out


def test_wait_prints_a_repeated_segmentation_failure_once(monkeypatch, capsys, poll_clock):
    runner = make_runner(sdks=("java",))
    runner.timeout = 12

    def rejected(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        return 1, "filter rejected"

    monkeypatch.setattr(e2e_runner, "run_abs", rejected)
    runner.wait_for_metrics()
    out = capsys.readouterr().out
    assert out.count("Segmentation query failed for java") == 1
    assert "Metrics incomplete" in out


@pytest.mark.parametrize("column", ["participants", "revenue"])
def test_wait_stops_early_on_an_over_published_sdk(monkeypatch, capsys, poll_clock, column):
    # Counts only grow, so an SDK already past the over-publish bound can never pass:
    # readiness stops waiting on it instead of spending the whole timeout.
    backend = healthy_backend()
    if column == "revenue":
        backend.revenue["java"] = revenue_result([57, 43], [540, 460])
    runner = make_runner()
    runner.timeout = 12

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        if column == "participants" and "--metric" not in args and sdk_from_filter(args) == "java":
            backend.calls.append(list(args))
            return 0, json.dumps(count_result([114, 86], [27, 23]))
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    assert poll_clock.now == 0
    out = capsys.readouterr().out
    assert "Metrics over-published" in out and "Metrics ready" not in out
    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    results = runner.verify_metrics()
    assert results["sdks"]["java"]["status"] == "fail"
    assert E2ERunner.report_passes(results) is False


def test_wait_keeps_polling_a_pending_sdk_when_another_is_over_published(monkeypatch, capsys, poll_clock):
    # One decided (over-published) SDK must not end the wait while another is still
    # pending, and the decided SDK is not re-queried.
    sdks = ("java", "go")
    backend = healthy_backend(sdks=sdks)
    backend.revenue["java"] = revenue_result([57, 43], [540, 460])
    runner = make_runner(sdks=sdks)
    runner.timeout = 12
    java_calls = []

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        sdk = sdk_from_filter(args)
        if sdk == "java":
            java_calls.append(list(args))
        if sdk == "go" and "--metric" in args and poll_clock.now == 0:
            backend.calls.append(list(args))
            return 0, "[]"
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    assert poll_clock.now == 5
    assert len(java_calls) == 2  # one round only: count and revenue
    assert "Metrics over-published" in capsys.readouterr().out


def test_wait_stops_early_when_goals_are_over_published(monkeypatch, capsys, poll_clock):
    backend = healthy_backend()
    runner = make_runner()
    runner.timeout = 12

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        if "--metric" not in args and sdk_from_filter(args) == "java":
            backend.calls.append(list(args))
            return 0, json.dumps(count_result([57, 43], [30, 26]))  # 56 goals > int(50 * 1.05)
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    assert poll_clock.now == 0
    assert "Metrics over-published" in capsys.readouterr().out


def test_wait_treats_a_count_exactly_at_the_bound_as_ready(monkeypatch, capsys, poll_clock):
    backend = healthy_backend()
    runner = make_runner()
    runner.timeout = 12

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        if "--metric" not in args and sdk_from_filter(args) == "java":
            backend.calls.append(list(args))
            return 0, json.dumps(count_result([60, 45], [27, 23]))  # 105 == int(100 * 1.05)
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    out = capsys.readouterr().out
    assert "Metrics ready" in out and "over-published" not in out


def test_wait_stops_early_one_past_the_bound(monkeypatch, capsys, poll_clock):
    backend = healthy_backend()
    runner = make_runner()
    runner.timeout = 12

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        if "--metric" not in args and sdk_from_filter(args) == "java":
            backend.calls.append(list(args))
            return 0, json.dumps(count_result([61, 45], [27, 23]))  # 106 == int(100 * 1.05) + 1
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    assert poll_clock.now == 0
    assert "Metrics over-published" in capsys.readouterr().out


def test_wait_stops_early_when_nothing_was_planned_but_something_was_measured(monkeypatch, capsys, poll_clock):
    # units=0: expected 0 with actual 3 can never pass (_within_tolerance needs actual == 0).
    backend = healthy_backend()
    runner = make_runner(units=0)
    runner.timeout = 12

    def waiting(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        if sdk_from_filter(args) == "java":
            if "--metric" in args:
                return 0, json.dumps([{"metric_id": int(REVENUE_METRIC_ID), "variants": [
                    {"variant": 0, "unit_count": 0, "count": 0}]}])
            return 0, json.dumps(count_result([2, 1], [0, 0]))
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", waiting)
    runner.wait_for_metrics()
    assert poll_clock.now == 0
    assert "Metrics over-published" in capsys.readouterr().out


def test_wait_does_not_start_the_revenue_query_after_the_count_read_uses_the_budget(
        monkeypatch, capsys, poll_clock):
    # --timeout 6: the count read starts at 0 and returns at 7. The revenue query
    # must not start at 7, and the run still reaches verification.
    backend = healthy_backend()
    runner = make_runner()
    runner.timeout = 6
    started = []

    def slow(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        started.append((poll_clock.now, "--metric" in args))
        poll_clock.now += 7
        return 0, "[]"

    monkeypatch.setattr(e2e_runner, "run_abs", slow)
    monkeypatch.setattr(e2e_runner.time, "sleep", lambda s: None)
    runner.wait_for_metrics()
    assert started == [(0, False)]
    out = capsys.readouterr().out
    assert "Metrics incomplete" in out and "java revenue: None/500 (not polled before the deadline)" in out
    monkeypatch.setattr(e2e_runner, "run_abs", backend)
    results = runner.verify_metrics()
    assert results["sdks"]["java"]["status"] == "ok"


def test_revenue_query_not_started_when_count_read_ends_exactly_at_deadline(monkeypatch, capsys, poll_clock):
    runner = make_runner()
    runner.timeout = 6
    started = []

    def slow(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        started.append((poll_clock.now, "--metric" in args))
        poll_clock.now += 6
        return 0, "[]"

    monkeypatch.setattr(e2e_runner, "run_abs", slow)
    monkeypatch.setattr(e2e_runner.time, "sleep", lambda s: None)
    runner.wait_for_metrics()
    assert started == [(0, False)]


@pytest.mark.parametrize("units, expected_line", [
    ([30, 20], "java participants: 50/100; java goals: 15/50; java revenue: None/500"),
])
def test_wait_reports_the_counts_read_when_the_deadline_skips_revenue(
        monkeypatch, capsys, poll_clock, units, expected_line):
    runner = make_runner()
    runner.timeout = 6

    def slow(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        poll_clock.now += 7
        return 0, json.dumps(count_result(units, [8, 7]))

    monkeypatch.setattr(e2e_runner, "run_abs", slow)
    monkeypatch.setattr(e2e_runner.time, "sleep", lambda s: None)
    runner.wait_for_metrics()
    assert expected_line in capsys.readouterr().out


def test_wait_detects_over_publishing_even_when_the_deadline_skips_revenue(monkeypatch, capsys, poll_clock):
    runner = make_runner()
    runner.timeout = 6

    def slow(args, *a, **kw):
        if args[:2] == ["experiments", "request-update"]:
            return 0, ""
        poll_clock.now += 7
        return 0, json.dumps(count_result([114, 86], [27, 23]))  # 200 exposures > int(100 * 1.05)

    monkeypatch.setattr(e2e_runner, "run_abs", slow)
    monkeypatch.setattr(e2e_runner.time, "sleep", lambda s: None)
    runner.wait_for_metrics()
    assert "Metrics over-published" in capsys.readouterr().out


def test_unverified_sdk_reports_unknown_not_zero_counts(monkeypatch):
    backend = healthy_backend()

    def no_segment(args, *a, **kw):
        if "--filter" in args and "--metric" not in args:
            return 1, "segmentation rejected"
        return backend(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", no_segment)
    runner = make_runner()
    java = runner.verify_metrics()["sdks"]["java"]
    assert java["status"] == "fail"
    assert java["exposures"]["actual"] is None and java["goals"]["actual"] is None
