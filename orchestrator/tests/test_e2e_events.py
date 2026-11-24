"""Mutation-style tests: the oracle uses planned calls, not acknowledged sends."""
import copy
import json
import os
import sys
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from e2e_cases import BATCH_SIZE, case_specs, eq_filter, matches, measured_total, metric_specs
from e2e_runner import E2ERunner, run_abs


@pytest.fixture
def runner():
    r = E2ERunner({"javascript": "http://wrapper"}, {"units": 2})
    r.run_id = "abcd1234"
    r.experiment_id = 100
    r.experiment_name = "e2e-abcd1234"
    r.session_experiment_id = 101
    r.session_experiment_name = "e2e-abcd1234-session"
    r.app_id = "90"
    r.case_metric_ids = {key: str(i) for i, key in enumerate(metric_specs(), 1)}
    r.metric_id = "20"
    r.capability_policy["sdks"]["javascript"]["e2e"] = {"required": ["preReadyGoals"]}
    r.case_results = {"javascript": {c["name"]: {"status": "sent"} for c in case_specs(r.run_id, "javascript")}}
    return r


def backend(r, sdk, case_name, metric_id, experiment_id=None):
    case = next(c for c in case_specs(r.run_id, sdk) if c["name"] == case_name)
    key = next(k for k, v in r.case_metric_ids.items() if v == metric_id)
    return {"metric_id": metric_id, "variants": [{"variant": 0, "unit_count": 1,
            "count": float(case["expected"].get(key, 0))}]}


def verify(r):
    result = {"overall_pass": True}
    r.verify_event_cases(result)
    return result


def test_all_planned_cases_pass_with_independent_measurements(runner):
    with patch.object(runner, "_query_metric", side_effect=lambda mid, sdk, case, *rest: backend(runner, sdk, case, mid)), \
         patch.object(runner, "_verify_arrival", return_value={"pass": True}):
        result = verify(runner)
    assert result["overall_pass"]
    assert len(result["event_cases"]["javascript"]) == 21


@pytest.mark.parametrize("case", [c["name"] for c in case_specs("12345678", "javascript")])
@pytest.mark.parametrize("mutation", ["missing_event", "wrong_value", "unmeasurable"])
def test_every_new_check_fails_closed(runner, case, mutation):
    def query(mid, sdk, name, *rest):
        data = backend(runner, sdk, name, mid)
        if name == case:
            if mutation == "unmeasurable":
                return None
            field = "unit_count" if not next(c for c in case_specs(runner.run_id, sdk) if c["name"] == name)["goals"] else "count"
            data["variants"][0][field] += -1 if mutation == "missing_event" else 0.1
        return data
    with patch.object(runner, "_query_metric", side_effect=query), \
         patch.object(runner, "_verify_arrival", return_value={"pass": True}):
        result = verify(runner)
    assert not result["overall_pass"]
    assert result["event_cases"]["javascript"][case]["status"] == "fail"


@pytest.mark.parametrize("value", [None, True, "1", float("nan"), float("inf"), {}, []])
def test_bad_backend_numbers_are_not_zero_or_counts(value):
    assert measured_total({"variants": [{"variant": 0, "count": value}]}, "count") is None
    assert not matches(0, None)


def test_missing_variant_field_or_partial_row_is_unmeasurable():
    for variants in ([], [{}], [{"variant": 0, "count": 1}, {"variant": 1}],
                     [{"variant": 0, "count": 1}, {"variant": 0, "count": 1}]):
        assert measured_total({"variants": variants}, "count") is None
    assert matches("1000016.6", Decimal("1000016.6000000001"))
    assert not matches("1000016.6", Decimal("1000016.5"))
    assert not matches("0.1", 0)
    assert not matches("10.5", 10)
    assert matches(-4, -4)


def test_query_pins_metric_and_both_segment_attributes(runner):
    with patch("e2e_runner.run_abs", return_value=(0, '[{"metric_id": 3,"variants": []}]')) as cli:
        runner._query_metric("3", "javascript", "numeric_2")
    args = cli.call_args.args[0]
    assert args[args.index("--metric") + 1] == "3"
    assert json.loads(args[args.index("--filter") + 1]) == eq_filter(sdk_name="javascript", e2e_case="numeric_2")
    for output in ('[]', '{}', '[{"metric_id": 4}]', 'garbage'):
        with patch("e2e_runner.run_abs", return_value=(0, output)):
            assert runner._query_metric("3", "javascript") is None


@pytest.mark.parametrize("case_name,tracks,publishes,finalizes", [
    ("close", 1, 0, 1), ("batch", BATCH_SIZE, 1, 0), ("repeated", 7, 1, 0),
    ("pre_ready", 0, 2, 0), ("no_properties", 1, 1, 0), ("null_properties", 1, 1, 0),
])
def test_wrapper_call_sequence(runner, case_name, tracks, publishes, finalizes):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == case_name)
    created = {"result": {"contextId": "ctx", "ready": True, "failed": False, "preReadyTracked": True}}
    with patch.object(runner, "_wrapper_post", return_value=created) as post, \
         patch("e2e_runner.requests.delete"):
        runner._send_event_case("javascript", "http://wrapper", case)
    calls = post.call_args_list
    assert sum(c.args[0].endswith("/track") for c in calls) == tracks
    assert sum(c.args[0].endswith("/publish") for c in calls) == publishes
    assert sum(c.args[0].endswith("/finalize") for c in calls) == finalizes
    bodies = [c.args[1] for c in calls if c.args[0].endswith("/track")]
    if case_name == "no_properties":
        assert "properties" not in bodies[0]
    if case_name == "null_properties":
        assert bodies[0]["properties"] is None
    if case_name == "pre_ready":
        assert calls[0].args[0].endswith("/context")
        assert "preReadyGoals" not in calls[0].args[1]
        assert any("preReadyGoals" in c.args[1] for c in calls if c.args[0].endswith("/context"))


def test_pre_ready_claim_mandatory(runner):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "pre_ready")
    with patch.object(runner, "_wrapper_post", return_value={"result": {"contextId": "c", "ready": True}}), \
         patch("e2e_runner.requests.delete"), pytest.raises(RuntimeError, match="not-ready"):
        runner._send_event_case("javascript", "http://wrapper", case)


def test_policy_unsupported_and_unclassified(runner):
    numeric = next(c for c in case_specs(runner.run_id, "php") if c["name"] == "numeric_id")
    assert "strings" in runner._case_skip("php", numeric)
    pre = next(c for c in case_specs(runner.run_id, "java") if c["name"] == "pre_ready")
    runner.capability_policy["sdks"]["java"].pop("e2e", None)
    with pytest.raises(RuntimeError, match="Unclassified"):
        runner._case_skip("java", pre)


def test_raw_collector_query_failure_cannot_pass_zero(runner):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "unicode")
    for rc, output in ((1, "permission denied"), (0, "{}"), (0, "[]")):
        with patch("e2e_runner.run_abs", return_value=(rc, output)):
            assert not runner._verify_arrival("javascript", case)["pass"]


def test_429_sleeps_120_then_retries():
    fail = Mock(returncode=1, stdout="", stderr="HTTP 429")
    ok = Mock(returncode=0, stdout="[]", stderr="")
    with patch("e2e_runner.subprocess.run", side_effect=[fail, ok]), patch("e2e_runner.time.sleep") as sleep:
        assert run_abs(["metrics", "list"]) == (0, "[]")
    sleep.assert_called_once_with(120)
