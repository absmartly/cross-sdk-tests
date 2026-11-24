"""Unit tests for e2e_runner revenue verification.

Revenue must come from the e2e_purchase_revenue (goal_property) metric, summed
as `count` across variants. It is never computed from the goal count; if it
cannot be measured the SDK is UNVERIFIED and the run fails.

Run from the repository root:  python3 -m pytest orchestrator/tests -q
"""

import json
import os
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


def test_extract_total_revenue_sums_count_across_variants():
    runner = make_runner()
    assert runner._extract_total_revenue(revenue_result([57, 43], [270, 230])[0]) == 500.0


def test_extract_total_revenue_without_count_is_unmeasured():
    runner = make_runner()
    no_count = {"metric_id": 2914, "variants": [{"variant": 0, "unit_count": 57, "mean": 4.7}]}
    assert runner._extract_total_revenue(no_count) is None
    assert runner._extract_total_revenue({"metric_id": 2914, "variants": []}) is None
    assert runner._extract_total_revenue(None) is None


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
    assert java["revenue"] == {"expected": 500, "actual": 500.0, "pass": True}
    assert results["aggregate"]["actual_revenue"] == 500.0
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
    assert java["revenue"]["actual"] == 400.0
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
    # Exposures/goals measured fine; revenue alone is unmeasurable.
    assert java["exposures"]["pass"] is True
    assert java["goals"]["pass"] is True
    assert java["revenue"]["actual"] is None
    assert java["revenue"]["unverified"] is True
    assert java["revenue"]["pass"] is False
    assert java["status"] == "unverified"
    assert results["overall_pass"] is False
    assert "All metrics verified" not in out
    assert "UNVERIFIED" in out
    assert "FAIL" in out
    if case == "metric_missing":
        assert not [c for c in backend.calls if "--metric" in c]


def test_unverified_segmentation_fails_even_when_aggregate_passes(monkeypatch, capsys):
    backend = healthy_backend()
    backend.count["java"] = 1  # segmented count query fails
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    java = results["sdks"]["java"]
    assert java["status"] == "unverified"
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
    assert results["sdks"]["go"]["status"] == "unverified"
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


def test_no_derived_anywhere(monkeypatch, capsys):
    backend = healthy_backend()
    backend.revenue["java"] = []
    runner = make_runner()
    results, out = run_verify(monkeypatch, capsys, runner, backend)

    assert "derived" not in json.dumps(results).lower()
    assert "derived" not in out.lower()
    with open(e2e_runner.__file__) as f:
        assert "derived" not in f.read().lower()


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
