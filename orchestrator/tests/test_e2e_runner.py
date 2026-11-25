"""e2e_runner metric verification, with run_abs mocked (no network, no `abs`).

Responses mirror `abs experiments metrics results <id> -o json`: a list of
MetricResult {metric_id, name, type, variants: [VariantResult]} where each
VariantResult carries unit_count (exposed units) and count (metric numerator;
converting units for goal_unique_count; summed `amount` for the goal_property
revenue metric). Source: absmartly/cli src/api-client/metric-results.ts
(parseMetricData / MetricResult). Revenue is queried with `--metric <id>`.
"""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import e2e_runner
from e2e_runner import E2ERunner

EXPERIMENT_ID = 6143
METRIC_ID = 77
REVENUE_METRIC_ID = 2914
SDKS = ("java", "php", "dotnet")


def metric_result(variants, metric_id=METRIC_ID, type_="primary"):
    """One MetricResult; variants = [(unit_count, count), ...] by variant index."""
    return {
        "metric_id": metric_id,
        "name": "e2e_purchase_count",
        "type": type_,
        "effect": "positive",
        "variants": [
            {
                "variant": i,
                "unit_count": units,
                "impact": None if i == 0 else 0.01,
                "impact_lower": None,
                "impact_upper": None,
                "pvalue": None if i == 0 else 0.5,
                "mean": count / units if units else None,
                "count": count,
                "variance": None,
                "abs_impact": None,
                "abs_impact_lower": None,
                "abs_impact_upper": None,
            }
            for i, (units, count) in enumerate(variants)
        ],
    }


def ok(variants):
    return 0, json.dumps([metric_result(variants)])


def revenue_result(counts, metric_id=REVENUE_METRIC_ID):
    """MetricResult for the goal_property revenue metric; counts per variant."""
    return {
        "metric_id": metric_id,
        "name": "e2e_purchase_revenue",
        "type": "custom",
        "variants": [
            {"variant": i, "unit_count": 50, "count": c, "mean": None}
            for i, c in enumerate(counts)
        ],
    }


def rev(counts):
    return 0, json.dumps([revenue_result(counts)])


# java on experiment 6144: revenue counts 270 + 230 = 500 (expected 500).
REV_JAVA_6144 = [270, 230]
REV_OK = rev([250, 250])


class FakeAbs:
    """Stands in for e2e_runner.run_abs, answering per --metric id and --filter."""

    def __init__(self, aggregate, per_sdk, revenue_aggregate=None, revenue_per_sdk=None):
        self.aggregate = aggregate
        self.per_sdk = per_sdk
        self.revenue_aggregate = revenue_aggregate or rev([750, 750])
        self.revenue_per_sdk = revenue_per_sdk or {}
        self.calls = []

    def __call__(self, args, profile="", api_key="", endpoint="", output_json=True):
        self.calls.append(list(args))
        assert args[:4] == ["experiments", "metrics", "results", str(EXPERIMENT_ID)]
        is_revenue = "--metric" in args
        if is_revenue:
            assert args[args.index("--metric") + 1] == str(REVENUE_METRIC_ID)
        if "--filter" not in args:
            return self.revenue_aggregate if is_revenue else self.aggregate
        payload = json.loads(args[args.index("--filter") + 1])
        sdk = payload["filter"]["and"][0]["eq"][1]["value"]
        if is_revenue:
            return self.revenue_per_sdk.get(sdk, REV_OK)
        return self.per_sdk[sdk]


def make_runner(sdks=SDKS, units=100, errors=None):
    runner = E2ERunner({s: f"http://{s}" for s in sdks}, {"profile": "e2e", "units": units})
    runner.experiment_id = EXPERIMENT_ID
    runner.experiment_name = "e2e-test"
    runner.metric_id = str(METRIC_ID)
    runner.revenue_metric_id = str(REVENUE_METRIC_ID)
    for s in sdks:
        runner.sdk_results[s] = {
            "status": "ok",
            "exposures_sent": units,
            "goals_sent": (units + 1) // 2,
            "revenue_sent": ((units + 1) // 2) * 10,
            "planned_exposures": units,
            "planned_goals": (units + 1) // 2,
            "context_errors": 0,
            "treatment_errors": 0,
            "track_errors": 0,
            **((errors or {}).get(s, {})),
        }
    return runner


def verify(monkeypatch, aggregate, per_sdk, revenue_aggregate=None, revenue_per_sdk=None,
           revenue_metric_id=str(REVENUE_METRIC_ID), **kw):
    fake = FakeAbs(aggregate, per_sdk, revenue_aggregate, revenue_per_sdk)
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(**kw)
    runner.revenue_metric_id = revenue_metric_id
    results = runner.verify_metrics()
    runner.print_report(results)
    return results, fake


# 6143 shape: java 100 units split 44/56, 50 converting units split 27/23.
JAVA_6143 = [(44, 27), (56, 23)]
PHP_OK = [(51, 24), (49, 26)]
DOTNET_OK = [(48, 25), (52, 25)]
AGG_OK = ok([(143, 76), (157, 74)])


def test_filter_payload_is_audience_expression_built_with_json(monkeypatch):
    weird = 'ja"va\\}'
    results, fake = verify(
        monkeypatch, AGG_OK, {weird: ok(JAVA_6143)}, sdks=(weird,),
    )
    seg_calls = [c for c in fake.calls if "--filter" in c and "--metric" not in c]
    assert len(seg_calls) == 1
    sent = seg_calls[0][seg_calls[0].index("--filter") + 1]
    assert json.loads(sent) == {
        "filter": {"and": [{"eq": [{"var": {"path": "sdk_name"}}, {"value": weird}]}]}
    }
    assert sent == json.dumps(json.loads(sent))


def test_exact_filter_for_java():
    assert E2ERunner.sdk_segment_filter("java") == (
        '{"filter": {"and": [{"eq": [{"var": {"path": "sdk_name"}}, {"value": "java"}]}]}}'
    )


def test_matching_counts_pass(monkeypatch):
    results, _ = verify(
        monkeypatch, AGG_OK, {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["overall_pass"] is True
    for s in SDKS:
        assert results["sdks"][s]["status"] == "ok", results["sdks"][s]
        assert results["sdks"][s]["verified"] == "per-sdk"


def test_6143_shape_compares_unit_count_sum_and_count_sum(monkeypatch, capsys):
    """The CLI table's "samples 56" is the treatment unit_count alone, and
    "control 27 / treatment 23" are per-variant goal numerators (count). The
    runner compares sum(unit_count)=100 to exposures, sum(count)=50 to goals."""
    results, _ = verify(
        monkeypatch, AGG_OK, {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    java = results["sdks"]["java"]
    assert java["exposures"] == {"expected": 100, "actual": 100, "pass": True}
    assert java["goals"] == {"expected": 50, "actual": 50, "pass": True}
    assert java["revenue"] == {"expected": 500, "actual": 500, "pass": True}
    assert java["variants"] == [
        {"variant": 0, "unit_count": 44, "count": 27},
        {"variant": 1, "unit_count": 56, "count": 23},
    ]
    out = capsys.readouterr().out
    assert "v0 participants=44 goals=27, v1 participants=56 goals=23" in out


def test_short_counts_fail(monkeypatch):
    results, _ = verify(
        monkeypatch,
        ok([(140, 74), (157, 74)]),
        {"java": ok([(41, 27), (56, 21)]), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["overall_pass"] is False
    java = results["sdks"]["java"]
    assert java["status"] == "fail"
    assert java["exposures"]["actual"] == 97 and java["exposures"]["pass"] is False
    assert java["goals"]["actual"] == 48 and java["goals"]["pass"] is False
    assert "short counts" in java["note"]
    assert results["sdks"]["php"]["status"] == "ok"


def test_over_publish_fails(monkeypatch):
    results, _ = verify(
        monkeypatch,
        ok([(160, 76), (160, 74)]),
        {"java": ok([(54, 27), (56, 23)]), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["overall_pass"] is False
    assert results["sdks"]["java"]["status"] == "fail"
    assert "over-published exposures (110 > 100)" in results["sdks"]["java"]["note"]


def test_within_tolerance_retry_passes(monkeypatch):
    results, _ = verify(
        monkeypatch, AGG_OK, {"java": ok([(46, 27), (56, 23)]), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["sdks"]["java"]["status"] == "ok"
    assert results["overall_pass"] is True


@pytest.mark.parametrize(
    "response, reason",
    [
        ((1, "Error: Invalid filter"), "segmentation query failed (abs rc=1): Error: Invalid filter"),
        ((0, "not json at all"), "segmentation output could not be parsed"),
        ((0, "[]"), "segmentation returned no result for the e2e metric"),
        ((0, json.dumps([metric_result([])])), "segmented result has no per-variant unit_count/count"),
        ((0, json.dumps([metric_result([(50, 25)], metric_id=999, type_="secondary")])),
         "segmentation returned no result for the e2e metric"),
    ],
)
def test_unverified_sdk_fails(monkeypatch, response, reason):
    # Aggregate matches exactly, which must NOT rescue the unverified SDK.
    results, _ = verify(
        monkeypatch, AGG_OK, {"java": response, "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    java = results["sdks"]["java"]
    assert results["overall_pass"] is False
    assert java["status"] == "fail"
    assert java["note"] == f"unverified: {reason}"
    assert java["exposures"]["pass"] is False and java["exposures"]["unverified"] is True
    assert results["aggregate"]["pass"] is True
    assert results["sdks"]["php"]["status"] == "ok"


def test_filter_ignored_all_equal_aggregate_fails(monkeypatch):
    agg = ok([(150, 75), (150, 75)])
    results, _ = verify(
        monkeypatch, agg, {s: agg for s in SDKS},
    )
    assert results["overall_pass"] is False
    for s in SDKS:
        assert results["sdks"][s]["status"] == "fail"
        assert "ignore the segmentation filter" in results["sdks"][s]["note"]
        assert results["sdks"][s]["note"].startswith("unverified: ")


def test_sum_exceeds_aggregate_fails(monkeypatch):
    results, _ = verify(
        monkeypatch,
        ok([(100, 50), (100, 50)]),
        {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["overall_pass"] is False
    for s in SDKS:
        assert results["sdks"][s]["status"] == "fail"
        assert "exceeds the aggregate total" in results["sdks"][s]["note"]


def test_wrapper_errors_fail_even_with_matching_counts(monkeypatch):
    results, _ = verify(
        monkeypatch, AGG_OK,
        {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
        errors={"php": {"track_errors": 2}},
    )
    assert results["overall_pass"] is False
    assert results["sdks"]["php"]["status"] == "fail"
    assert "track=2" in results["sdks"]["php"]["note"]


def test_aggregate_zero_is_informational_when_segments_verify(monkeypatch):
    results, _ = verify(
        monkeypatch, ok([(0, 0), (0, 0)]),
        {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["aggregate"]["pass"] is False
    assert results["overall_pass"] is True


def test_live_6143_segment_and_aggregate_verify(monkeypatch, capsys):
    # Live test-1 numbers for experiment 6143 (3 SDKs x 100 units, 50 goals each).
    results, _ = verify(
        monkeypatch, ok([(138, 69), (162, 81)]),
        {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    java = results["sdks"]["java"]
    assert (java["exposures"]["actual"], java["goals"]["actual"]) == (100, 50)
    assert results["aggregate"]["actual_participants"] == 300
    assert results["aggregate"]["actual_goals"] == 150
    assert E2ERunner.report_passes(results) is True
    assert "PASS" in capsys.readouterr().out


def test_wrong_expectation_fails(monkeypatch, capsys):
    # The same live numbers against a deliberately wrong plan (120 units) must FAIL.
    results, _ = verify(
        monkeypatch, ok([(138, 69), (162, 81)]),
        {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)}, units=120,
    )
    assert results["overall_pass"] is False
    assert results["sdks"]["java"]["status"] == "fail"
    assert "All metrics verified" not in capsys.readouterr().out


def banner_row(status="ok", exposures=None, goals=None):
    full = lambda **kw: {"expected": 100, "actual": 100, "pass": True, **kw}
    return {"status": status, "exposures": exposures or full(), "goals": goals or full(expected=50, actual=50),
            "revenue": full(expected=500, actual=500)}


@pytest.mark.parametrize("row", [
    banner_row(status="unverified"),
    banner_row(exposures={"expected": 100, "actual": None, "pass": True, "unverified": True}),
    banner_row(goals={"expected": 50, "actual": 40, "pass": False}),
    banner_row(status="fail"),
])
def test_pass_banner_never_prints_over_an_unverified_or_failing_sdk(row, capsys):
    # Even if overall_pass were left True by mistake, the report and exit code must FAIL.
    results = {"overall_pass": True, "sdks": {"java": row}}
    assert E2ERunner.report_passes(results) is False
    runner = make_runner(sdks=("java",))
    runner.print_report(results)
    out = capsys.readouterr().out
    assert "All metrics verified" not in out
    assert "FAIL" in out


# --- measured revenue -------------------------------------------------------

SEGMENTS_OK = {"java": ok(JAVA_6143), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)}


def test_measured_revenue_passes_java_6144_shape(monkeypatch, capsys):
    results, fake = verify(
        monkeypatch, AGG_OK, SEGMENTS_OK, revenue_per_sdk={"java": rev(REV_JAVA_6144)},
    )
    java = results["sdks"]["java"]
    assert java["revenue"] == {"expected": 500, "actual": 500, "pass": True}
    assert java["status"] == "ok"
    assert results["overall_pass"] is True
    assert results["aggregate"]["actual_revenue"] == 1500
    assert results["aggregate"]["expected_revenue"] == 1500
    rev_calls = [c for c in fake.calls if "--metric" in c]
    # One aggregate query plus one per SDK, each segmented with the SDK filter.
    assert len(rev_calls) == 4
    java_call = [c for c in rev_calls if "--filter" in c and '"java"' in c[c.index("--filter") + 1]]
    assert java_call and java_call[0][java_call[0].index("--metric") + 1] == str(REVENUE_METRIC_ID)
    out = capsys.readouterr().out
    assert "revenue 1500/1500" in out
    assert "All metrics verified" in out


def test_revenue_is_measured_not_computed_from_goals(monkeypatch):
    # Goals match exactly (50) but measured revenue is 400: must fail. A runner
    # computing revenue as goals*10 would report 500 and pass.
    results, _ = verify(
        monkeypatch, AGG_OK, SEGMENTS_OK, revenue_per_sdk={"java": rev([200, 200])},
    )
    java = results["sdks"]["java"]
    assert java["goals"]["pass"] is True
    assert java["revenue"] == {"expected": 500, "actual": 400, "pass": False}
    assert java["status"] == "fail"
    assert "short revenue (400/500)" in java["note"]
    assert results["overall_pass"] is False
    assert E2ERunner.report_passes(results) is False


def test_revenue_over_tolerance_fails(monkeypatch):
    results, _ = verify(
        monkeypatch, AGG_OK, SEGMENTS_OK, revenue_per_sdk={"java": rev([300, 300])},
        revenue_aggregate=rev([850, 850]),
    )
    java = results["sdks"]["java"]
    assert java["revenue"]["pass"] is False and java["revenue"]["actual"] == 600
    assert "over-published revenue (600 > 500)" in java["note"]
    assert results["overall_pass"] is False


@pytest.mark.parametrize(
    "response, reason",
    [
        ((1, "Error: metric not found"), "query failed (abs rc=1): Error: metric not found"),
        ((0, "not json"), "output could not be parsed"),
        ((0, "[]"), f"no result for metric {REVENUE_METRIC_ID}"),
        ((0, json.dumps([revenue_result([250, 250], metric_id=999)])), f"no result for metric {REVENUE_METRIC_ID}"),
        ((0, json.dumps([revenue_result([None, None])])), "variant count is not numeric (None)"),
        ((0, json.dumps([revenue_result([250, "250"])])), "variant count is not numeric ('250')"),
        ((0, json.dumps([revenue_result([])])), "result has no variants"),
    ],
)
def test_unmeasurable_revenue_fails(monkeypatch, response, reason, capsys):
    results, _ = verify(monkeypatch, AGG_OK, SEGMENTS_OK, revenue_per_sdk={"java": response})
    java = results["sdks"]["java"]
    assert java["status"] == "fail"
    assert java["note"].startswith("unverified: revenue")
    assert java["note"] == f"unverified: revenue {reason}"
    assert java["revenue"]["pass"] is False and java["revenue"]["unverified"] is True
    assert java["revenue"]["actual"] is None
    assert results["overall_pass"] is False
    assert E2ERunner.report_passes(results) is False
    assert results["sdks"]["php"]["status"] == "ok"
    assert "All metrics verified" not in capsys.readouterr().out


def test_no_revenue_metric_id_fails(monkeypatch):
    results, fake = verify(monkeypatch, AGG_OK, SEGMENTS_OK, revenue_metric_id=None)
    assert not [c for c in fake.calls if "--metric" in c]
    for s in SDKS:
        assert results["sdks"][s]["status"] == "fail"
        assert results["sdks"][s]["note"] == "unverified: revenue no revenue metric id"
    assert results["overall_pass"] is False


def test_untrusted_segmentation_fails_revenue_too(monkeypatch):
    agg = ok([(150, 75), (150, 75)])
    results, _ = verify(monkeypatch, agg, {s: agg for s in SDKS})
    for s in SDKS:
        assert results["sdks"][s]["revenue"]["pass"] is False
        assert results["sdks"][s]["revenue"]["unverified"] is True


def test_revenue_filter_ignored_fails(monkeypatch):
    same = rev([250, 250])
    results, _ = verify(
        monkeypatch, AGG_OK, SEGMENTS_OK,
        revenue_aggregate=same, revenue_per_sdk={s: same for s in SDKS},
    )
    for s in SDKS:
        assert results["sdks"][s]["status"] == "fail"
        assert results["sdks"][s]["note"].startswith("unverified: revenue every SDK returned the same revenue")
    assert results["overall_pass"] is False


def test_revenue_sum_exceeds_aggregate_fails(monkeypatch):
    results, _ = verify(monkeypatch, AGG_OK, SEGMENTS_OK, revenue_aggregate=rev([500, 500]))
    for s in SDKS:
        assert results["sdks"][s]["status"] == "fail"
        assert "sum of per-SDK revenue (1500) exceeds" in results["sdks"][s]["note"]


def test_dry_run_revenue_row_is_planned_amount():
    runner = make_runner(sdks=("java",))
    runner.dry_run = True
    results = runner.verify_metrics()
    assert results["sdks"]["java"]["revenue"] == {"expected": 500, "actual": 0, "pass": True}


# Built from parts so this guard does not itself match a grep for the word.
BANNED_WORD = "der" + "ived"


def test_revenue_wording_banned_in_source_and_report(monkeypatch, capsys):
    source = Path(e2e_runner.__file__).read_text()
    assert BANNED_WORD not in source.lower()
    results, _ = verify(monkeypatch, AGG_OK, SEGMENTS_OK, revenue_per_sdk={"java": rev([200, 200])})
    out = capsys.readouterr().out
    assert "Revenue" in out and "400/500" in out
    assert BANNED_WORD not in out.lower()
    assert BANNED_WORD not in json.dumps(results).lower()


REVENUE_DEFINITION = {"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue", "type": "goal_property",
                      "value_source_property": "amount", "goal": {"name": "purchase"}}


def metric_get_output(definition, args):
    """What `abs metrics get <id> -o json` prints: the raw metric with --raw,
    else the CLI summary (no value_source_property, goal as a plain string)."""
    if "--raw" in args:
        return definition
    goal = definition.get("goal")
    return {"id": definition.get("id"), "name": definition.get("name"), "type": definition.get("type"),
            "goal": goal.get("name") if isinstance(goal, dict) else goal}


class ResourceAbs:
    def __init__(self, metrics_list, definition=REVENUE_DEFINITION):
        self.metrics_list = metrics_list
        self.definition = definition
        self.calls = []

    def __call__(self, args, profile="", api_key="", endpoint="", output_json=True):
        self.calls.append(list(args))
        if args[:2] == ["metrics", "list"]:
            # Like the CLI: drafts and archived metrics are listed only on request.
            listed = []
            for item in self.metrics_list:
                state = self.definition if item.get("id") == self.definition.get("id") else item
                if state.get("lifecycle_status") == "draft" and "--include-drafts" not in args:
                    continue
                if state.get("archived") is True and "--archived" not in args:
                    continue
                listed.append(item)
            return 0, json.dumps(listed)
        if args[:2] == ["metrics", "get"]:
            return 0, json.dumps(metric_get_output(self.definition, args))
        if args[:2] == ["metrics", "create"]:
            return 0, json.dumps({"id": REVENUE_METRIC_ID})
        return 0, ""


def test_ensure_revenue_metric_creates_when_missing(monkeypatch):
    fake = ResourceAbs([{"id": 77, "name": "e2e_purchase_count"}])
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner.goal_id, runner.owner_id = "12", "3"
    runner._ensure_revenue_metric()
    assert runner.revenue_metric_id == str(REVENUE_METRIC_ID)
    create = [c for c in fake.calls if c[:2] == ["metrics", "create"]]
    assert len(create) == 1
    args = create[0]
    flag = lambda f: args[args.index(f) + 1]
    assert flag("--name") == "e2e_purchase_revenue"
    assert flag("--type") == "goal_property"
    assert flag("--goal") == "purchase"
    assert "--goal-id" not in args
    assert flag("--value-source-property") == "amount"
    assert flag("--owner") == "3"
    assert "--description" in args
    rid = str(REVENUE_METRIC_ID)
    assert ["metrics", "review", "request", rid] in fake.calls
    assert ["metrics", "review", "approve", rid] in fake.calls
    assert ["metrics", "activate", rid, "--reason", "E2E testing"] in fake.calls


def test_ensure_revenue_metric_reuses_existing(monkeypatch):
    fake = ResourceAbs([{"id": 77, "name": "e2e_purchase_count"},
                        {"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}])
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner._ensure_revenue_metric()
    assert runner.revenue_metric_id == str(REVENUE_METRIC_ID)
    assert [c for c in fake.calls if c[0] == "metrics" and c[1] != "list"] == [
        ["metrics", "get", str(REVENUE_METRIC_ID), "--raw"]
    ]


@pytest.mark.parametrize("change, needle", [
    ({"type": "goal_unique_count"}, "type="),
    ({"value_source_property": "price"}, "value_source_property="),
    ({"goal": {"name": "checkout"}}, "goal="),
])
def test_existing_revenue_metric_with_wrong_definition_is_refused(monkeypatch, change, needle):
    fake = ResourceAbs([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}],
                       definition={**REVENUE_DEFINITION, **change})
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    with pytest.raises(RuntimeError, match=needle):
        runner._ensure_revenue_metric()


def test_unreadable_existing_revenue_metric_is_refused(monkeypatch):
    def fake(args, profile="", api_key="", endpoint="", output_json=True):
        if args[:2] == ["metrics", "list"]:
            return 0, json.dumps([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}])
        return 1, "not found"
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    with pytest.raises(RuntimeError, match="check its definition"):
        runner._ensure_revenue_metric()


def test_select_metric_never_falls_back_when_metric_id_is_known():
    runner = make_runner(sdks=("java",))
    other_primary = metric_result(JAVA_6143, metric_id=999, type_="primary")
    assert runner._select_metric([other_primary]) is None
    runner.metric_id = None
    assert runner._select_metric([other_primary]) is other_primary


def test_track_body_carries_the_purchase_amount(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, status=200, body=None):
            self.status_code = status
            self._body = body or {}

        def json(self):
            return self._body

    def post(url, json=None, timeout=None):
        sent.append((url.rsplit("/", 1)[-1], json))
        if url.endswith("/context"):
            return Resp(body={"result": {"contextId": "c1"}})
        return Resp()

    monkeypatch.setattr(e2e_runner.requests, "get", lambda url, timeout=None: Resp())
    monkeypatch.setattr(e2e_runner.requests, "post", post)
    runner = make_runner(sdks=("java",), units=2)
    result = runner._run_sdk_scenario("java", "http://java")
    assert result["status"] == "ok"
    tracks = [body for name, body in sent if name == "track"]
    assert tracks == [{"goalName": "purchase", "properties": {"amount": 10}}]
    assert result["revenue_sent"] == 10


def test_main_exits_nonzero_when_overall_pass_was_left_true(monkeypatch, capsys):
    # A hand-built result whose overall_pass is wrongly True over an unverified row:
    # main() must still exit non-zero (exit gate is report_passes, not overall_pass).
    unverified = {"status": "fail", "note": "unverified: revenue no result",
                  "exposures": {"expected": 100, "actual": 100, "pass": True},
                  "goals": {"expected": 50, "actual": 50, "pass": True},
                  "revenue": {"expected": 500, "actual": None, "pass": False, "unverified": True}}
    monkeypatch.setattr(E2ERunner, "run", lambda self: {"overall_pass": True, "sdks": {"java": unverified}})
    monkeypatch.setenv("SDK_URLS_OVERRIDE", "java=http://java-sdk:3000")
    monkeypatch.setattr(sys, "argv", ["e2e_runner.py"])
    with pytest.raises(SystemExit) as exc:
        e2e_runner.main()
    assert exc.value.code != 0


def test_dry_run_report_never_prints_pass(capsys):
    runner = make_runner(sdks=("java",))
    runner.dry_run = True
    results = runner.verify_metrics()
    runner.print_report(results)
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "All metrics verified" not in out
    assert E2ERunner.report_passes(results) is False


def test_report_passes_ignores_overall_pass_alone():
    # overall_pass True with no measured SDK row at all is not a pass.
    assert E2ERunner.report_passes({"overall_pass": True, "sdks": {}}) is False
    skipped = {"status": "skipped", **{k: {"expected": 0, "actual": 0, "pass": True}
                                       for k in ("exposures", "goals", "revenue")}}
    assert E2ERunner.report_passes({"overall_pass": True, "sdks": {"java": skipped}}) is False

def test_select_metric_matches_metric_id_over_other_primary(monkeypatch):
    # A different "primary" metric comes first with counts that would fail
    # java; ours (metric_id 77) is second and must be selected by metric_id.
    java = 0, json.dumps([
        metric_result([(10, 1), (10, 1)], metric_id=5, type_="primary"),
        metric_result(JAVA_6143, metric_id=METRIC_ID, type_="secondary"),
    ])
    results, _ = verify(
        monkeypatch, AGG_OK, {"java": java, "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    assert results["sdks"]["java"]["status"] == "ok", results["sdks"]["java"]
    assert results["sdks"]["java"]["exposures"]["actual"] == 100


def test_select_metric_falls_back_to_primary_without_metric_id(monkeypatch):
    java = 0, json.dumps([
        metric_result([(10, 1), (10, 1)], metric_id=5, type_="secondary"),
        metric_result(JAVA_6143, metric_id=METRIC_ID, type_="primary"),
    ])
    monkeypatch.setattr(e2e_runner, "run_abs", FakeAbs(AGG_OK, {"java": java}))
    runner = make_runner(sdks=("java",))
    runner.metric_id = None
    metric, reason = runner._fetch_metrics_for_sdk("java")
    assert reason is None
    assert metric["metric_id"] == METRIC_ID and metric["type"] == "primary"


def test_null_variant_counts_are_unverified(monkeypatch):
    # The CLI emits count: null when the metric_<id> column is absent.
    metric = metric_result(JAVA_6143)
    for v in metric["variants"]:
        v["count"] = None
        v["mean"] = None
    results, _ = verify(
        monkeypatch, AGG_OK,
        {"java": (0, json.dumps([metric])), "php": ok(PHP_OK), "dotnet": ok(DOTNET_OK)},
    )
    java = results["sdks"]["java"]
    assert results["overall_pass"] is False
    assert java["status"] == "fail"
    assert java["note"].startswith("unverified: "), java["note"]


def test_filter_ignored_all_equal_aggregate_fails_with_two_sdks(monkeypatch):
    agg = ok([(50, 25), (50, 25)])
    two = ("java", "php")
    results, _ = verify(monkeypatch, agg, {s: agg for s in two}, sdks=two)
    assert results["overall_pass"] is False
    for s in two:
        assert results["sdks"][s]["status"] == "fail"
        assert "ignore the segmentation filter" in results["sdks"][s]["note"]
        assert results["sdks"][s]["note"].startswith("unverified: ")


def test_requested_sdk_that_skips_fails_the_run(monkeypatch, capsys):
    # Owner rule: an SDK named with --sdk that returns 501 (skipped) fails, never skips silently.
    fake = FakeAbs(AGG_OK, {"php": ok(PHP_OK)})
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java", "php"))
    runner.requested_sdks = {"java", "php"}
    runner.sdk_results["java"] = {"status": "skipped", "planned_exposures": 100, "planned_goals": 50}
    results = runner.verify_metrics()
    runner.print_report(results)
    java = results["sdks"]["java"]
    assert java["status"] == "fail" and "requested with --sdk" in java["note"]
    assert results["overall_pass"] is False
    assert E2ERunner.report_passes(results) is False
    assert "All metrics verified" not in capsys.readouterr().out


def test_unrequested_sdk_may_skip_and_is_listed(monkeypatch, capsys):
    fake = FakeAbs(AGG_OK, {"php": ok(PHP_OK)})
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java", "php"))
    runner.requested_sdks = set()
    runner.sdk_results["java"] = {"status": "skipped", "planned_exposures": 100, "planned_goals": 50}
    results = runner.verify_metrics()
    runner.print_report(results)
    out = capsys.readouterr().out
    assert results["sdks"]["java"]["status"] == "skipped"
    assert "Skipped (not requested, no e2e mode): java" in out


def test_unknown_requested_sdk_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("SDK_URLS_OVERRIDE", "java=http://java-sdk:3000")
    monkeypatch.setattr(sys, "argv", ["e2e_runner.py", "--sdk", "java,rubyy"])
    monkeypatch.setattr(E2ERunner, "run", lambda self: pytest.fail("must not run with an unknown SDK"))
    with pytest.raises(SystemExit) as exc:
        e2e_runner.main()
    assert exc.value.code != 0
    assert "Unknown SDK(s): rubyy" in capsys.readouterr().out


def test_main_passes_requested_sdks_to_the_runner(monkeypatch):
    seen = {}

    def fake_run(self):
        seen["requested"] = set(self.requested_sdks)
        return {"overall_pass": False, "sdks": {}}

    monkeypatch.setenv("SDK_URLS_OVERRIDE", "java=http://java-sdk:3000,php=http://php-sdk:3000")
    monkeypatch.setattr(E2ERunner, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["e2e_runner.py", "--sdk", "java"])
    with pytest.raises(SystemExit):
        e2e_runner.main()
    assert seen["requested"] == {"java"}

    monkeypatch.setattr(sys, "argv", ["e2e_runner.py"])
    with pytest.raises(SystemExit):
        e2e_runner.main()
    assert seen["requested"] == set()


STUB_DOCKER = """#!/bin/bash
# docker stub for running run-e2e-tests.sh without containers.
if [ "$1" = run ]; then exit 0; fi
if [ "$1" = compose ]; then
  case "$2" in
    config) printf '%s\\n' java-sdk php-sdk ruby-sdk context-server ;;
    port) echo "0.0.0.0:3000" ;;
    ps) echo "Up 5 seconds" ;;
  esac
  exit 0
fi
exit 0
"""

STUB_PYTHON = """#!/bin/bash
# python3 stub: records the e2e runner's argv and environment.
if [ "$1" = "-c" ]; then exit 0; fi
if [ "$1" = "orchestrator/e2e_runner.py" ]; then
  shift
  printf '%s\\n' "$@" > "$ARGV_FILE"
  printf '%s\\n' "$SDK_SERVICES" > "$ARGV_FILE.services"
fi
exit 0
"""


def run_e2e_script(tmp_path, *script_args):
    import shutil
    import subprocess
    repo = Path(__file__).resolve().parents[2]
    work = tmp_path / "repo"
    work.mkdir()
    shutil.copy(repo / "run-e2e-tests.sh", work / "run-e2e-tests.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("python3", STUB_PYTHON), ("sleep", "#!/bin/bash\nexit 0\n")):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    argv_file = tmp_path / "argv"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ARGV_FILE": str(argv_file), "HOME": str(tmp_path)}
    proc = subprocess.run(["bash", str(work / "run-e2e-tests.sh"), "--skip-build", *script_args],
                          cwd=work, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return argv_file.read_text().splitlines(), (tmp_path / "argv.services").read_text().strip()


def test_run_e2e_script_forwards_requested_sdks_to_the_runner(tmp_path):
    argv, services = run_e2e_script(tmp_path, "--sdk", "java,php")
    assert argv[:2] == ["--sdk", "java,php"]
    assert services == "java,php"


def test_run_e2e_script_requests_nothing_without_sdk(tmp_path):
    argv, services = run_e2e_script(tmp_path)
    assert "--sdk" not in argv
    assert services == "java,php,ruby"


def test_existing_revenue_metric_with_wrong_goal_id_is_refused(monkeypatch):
    fake = ResourceAbs([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}],
                       definition={**REVENUE_DEFINITION, "goal": None, "goal_id": 999})
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner.goal_id = "12"
    with pytest.raises(RuntimeError, match="goal_id="):
        runner._ensure_revenue_metric()


def test_definition_check_refuses_the_cli_summary_shape(monkeypatch):
    # What `metrics get` prints without --raw must not pass the definition check.
    summary = metric_get_output(REVENUE_DEFINITION, ["metrics", "get", "2914"])
    monkeypatch.setattr(e2e_runner, "run_abs", lambda args, *a, **kw: (0, json.dumps(summary)))
    runner = make_runner(sdks=("java",))
    with pytest.raises(RuntimeError, match="value_source_property=None"):
        runner._check_revenue_metric_definition(REVENUE_METRIC_ID)


REQUEST = ["metrics", "review", "request", str(REVENUE_METRIC_ID)]
APPROVE = ["metrics", "review", "approve", str(REVENUE_METRIC_ID)]
ACTIVATE = ["metrics", "activate", str(REVENUE_METRIC_ID), "--reason", "E2E testing"]


UNARCHIVE = ["metrics", "archive", str(REVENUE_METRIC_ID), "--unarchive"]


@pytest.mark.parametrize("status, review, archived, expected", [
    ("draft", None, False, [REQUEST, APPROVE, ACTIVATE]),
    ("draft", "none", False, [REQUEST, APPROVE, ACTIVATE]),
    ("inactive", None, False, [REQUEST, APPROVE, ACTIVATE]),
    # A run that failed after `review request` or after `approve` is resumed, not repeated.
    ("draft", "pending", False, [APPROVE, ACTIVATE]),
    ("draft", "approved", False, [ACTIVATE]),
    # Archived is a separate flag: unarchive, then activate if it is not active.
    ("activated", None, True, [UNARCHIVE]),
    ("draft", "approved", True, [UNARCHIVE, ACTIVATE]),
    # The API reports an active metric as "activated"; "active" is accepted too.
    ("activated", None, False, []),
    ("activated", "approved", False, []),
    ("active", None, False, []),
    (None, None, False, []),
])
def test_existing_inactive_revenue_metric_is_reviewed_and_activated(monkeypatch, status, review, archived, expected):
    definition = dict(REVENUE_DEFINITION)
    if status is not None:
        definition["lifecycle_status"] = status
    if review is not None:
        # The API's shape: a nested review object (api-mocks MetricReview).
        definition["review"] = None if review == "none" else {"id": 1, "metric_id": REVENUE_METRIC_ID,
                                                              "status": review}
    if archived:
        definition["archived"] = True
    fake = ResourceAbs([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}], definition=definition)
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner._ensure_revenue_metric()
    tail = [c for c in fake.calls if c[0] == "metrics" and c[1] in ("review", "activate", "archive")]
    assert tail == expected
    lookups = [c for c in fake.calls if c[:2] == ["metrics", "list"]]
    assert lookups and all("--include-drafts" in c and "--archived" in c for c in lookups)
    assert not [c for c in fake.calls if c[:2] == ["metrics", "create"]]


def test_reactivation_failure_of_existing_metric_stops_the_run(monkeypatch):
    definition = {**REVENUE_DEFINITION, "lifecycle_status": "draft"}

    def fake(args, profile="", api_key="", endpoint="", output_json=True):
        if args[:2] == ["metrics", "list"]:
            return 0, json.dumps([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}])
        if args[:2] == ["metrics", "get"]:
            return 0, json.dumps(metric_get_output(definition, args))
        if args[:3] == ["metrics", "review", "approve"]:
            return 1, "approval denied"
        return 0, ""

    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    with pytest.raises(RuntimeError, match="approval denied"):
        runner._ensure_revenue_metric()


def test_trailing_comma_in_sdk_list_is_ignored(monkeypatch):
    seen = {}

    def fake_run(self):
        seen["sdks"] = list(self.sdks)
        return {"overall_pass": False, "sdks": {}}

    monkeypatch.setenv("SDK_URLS_OVERRIDE", "java=http://java-sdk:3000")
    monkeypatch.setattr(E2ERunner, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["e2e_runner.py", "--sdk", "java,"])
    with pytest.raises(SystemExit):
        e2e_runner.main()
    assert seen["sdks"] == ["java"]


@pytest.mark.parametrize("names, expected", [("", []), ("java,php", ["--sdk", "java,php"])])
def test_run_e2e_script_sdk_opt_args(names, expected):
    script = Path(__file__).resolve().parents[2] / "run-e2e-tests.sh"
    text = script.read_text()
    start = text.index("sdk_opt_args() {")
    func = text[start:text.index("\n}\n", start) + 3]
    import subprocess
    out = subprocess.run(["bash", "-c", func + 'sdk_opt_args "$1"', "_", names],
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == expected


@pytest.mark.parametrize("value", [",", " ", " , "])
def test_empty_sdk_list_exits_before_any_api_call(monkeypatch, capsys, value):
    monkeypatch.setenv("SDK_URLS_OVERRIDE", "java=http://java-sdk:3000")
    monkeypatch.setattr(sys, "argv", ["e2e_runner.py", "--sdk", value])
    monkeypatch.setattr(E2ERunner, "run", lambda self: pytest.fail("must not run with no SDKs"))
    with pytest.raises(SystemExit) as exc:
        e2e_runner.main()
    assert exc.value.code != 0
    assert "No valid SDKs specified" in capsys.readouterr().out


def test_race_relookup_finds_a_draft_with_the_lookup_flags(monkeypatch):
    # The first lookup misses (created concurrently), create says "already exists",
    # and the re-lookup must still pass --include-drafts --archived to find the draft.
    definition = {**REVENUE_DEFINITION, "lifecycle_status": "draft"}
    fake = ResourceAbs([], definition=definition)
    state = {"lists": 0}

    def raced(args, *a, **kw):
        if args[:2] == ["metrics", "list"]:
            state["lists"] += 1
            if state["lists"] == 2:
                fake.metrics_list = [{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}]
        if args[:2] == ["metrics", "create"]:
            fake.calls.append(list(args))
            return 1, "metric already exists"
        return fake(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", raced)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner._ensure_revenue_metric()
    assert runner.revenue_metric_id == str(REVENUE_METRIC_ID)
    assert ACTIVATE in fake.calls


def test_unarchive_failure_stops_the_run(monkeypatch):
    definition = {**REVENUE_DEFINITION, "lifecycle_status": "activated", "archived": True}
    fake = ResourceAbs([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}], definition=definition)

    def failing(args, *a, **kw):
        if args[:2] == ["metrics", "archive"]:
            return 17, "unarchive denied"
        return fake(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", failing)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    with pytest.raises(RuntimeError, match="unarchive denied"):
        runner._ensure_revenue_metric()


def test_duplicate_revenue_metric_names_are_refused(monkeypatch):
    fake = ResourceAbs([{"id": 1001, "name": "e2e_purchase_revenue"},
                        {"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}])
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    with pytest.raises(RuntimeError, match="2 metrics named 'e2e_purchase_revenue'"):
        runner._ensure_revenue_metric()


def test_revenue_lookup_searches_by_name(monkeypatch):
    fake = ResourceAbs([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}])
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner._ensure_revenue_metric()
    lookup = next(c for c in fake.calls if c[:2] == ["metrics", "list"])
    assert lookup[lookup.index("--search") + 1] == "e2e_purchase_revenue"


def test_top_level_review_status_is_not_read(monkeypatch):
    # The API has no top-level review_status (it is only a list filter): only the
    # nested review.status may skip review steps.
    definition = {**REVENUE_DEFINITION, "lifecycle_status": "draft", "review_status": "approved"}
    fake = ResourceAbs([{"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}], definition=definition)
    monkeypatch.setattr(e2e_runner, "run_abs", fake)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    runner._ensure_revenue_metric()
    tail = [c for c in fake.calls if c[0] == "metrics" and c[1] in ("review", "activate")]
    assert tail == [REQUEST, APPROVE, ACTIVATE]


def test_race_relookup_refuses_duplicate_names(monkeypatch):
    fake = ResourceAbs([])
    state = {"lists": 0}

    def raced(args, *a, **kw):
        if args[:2] == ["metrics", "list"]:
            state["lists"] += 1
            if state["lists"] == 2:
                fake.metrics_list = [{"id": 1001, "name": "e2e_purchase_revenue"},
                                     {"id": REVENUE_METRIC_ID, "name": "e2e_purchase_revenue"}]
        if args[:2] == ["metrics", "create"]:
            return 1, "metric already exists"
        return fake(args, *a, **kw)

    monkeypatch.setattr(e2e_runner, "run_abs", raced)
    runner = make_runner(sdks=("java",))
    runner.revenue_metric_id = None
    with pytest.raises(RuntimeError, match="2 metrics named 'e2e_purchase_revenue'"):
        runner._ensure_revenue_metric()
