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
    # The exact text CLI 1.18.0 prints for an HTTP 429.
    fail = Mock(returncode=1, stdout="", stderr="Error: Rate limit exceeded.\nEndpoint: GET /experiments/1\nRetry after: 0 seconds")
    ok = Mock(returncode=0, stdout="[]", stderr="")
    with patch("e2e_runner.subprocess.run", side_effect=[fail, ok]), patch("e2e_runner.time.sleep") as sleep:
        assert run_abs(["metrics", "list"]) == (0, "[]")
    sleep.assert_called_once_with(120)


def test_numeric_ids_distinct_for_every_lane(runner):
    ids = [next(c['uid'] for c in case_specs(runner.run_id, sdk) if c['name'] == 'numeric_id')
           for sdk in runner.capability_policy['sdks']]
    assert len(set(ids)) == len(ids)
    assert all(type(uid) is int and 0 < uid < 2**53 for uid in ids)


def raw_fixture(runner, case):
    # Independent precomputed UTF-8 MD5/base64url fixture, not the runner's hash code.
    case['uid'] = 'e2e-0c11789c-go-94'
    common = dict(unit_uid='OSS6pz0j4iJ-ymv9M3DHvg', unit_type='user_id', application_id=90,
                  environment=runner.environment, agent='absmartly-javascript-sdk/1.14.0')
    exposure = dict(common, event_type='exposure', experiment_id=100,
                    unit_attributes=json.dumps(dict(sdk_name='javascript', e2e_case=case['name'])))
    return [exposure] + [dict(common, event_type='goal', goal_name='purchase', properties=json.dumps(g)) for g in case['goals']]


@pytest.mark.parametrize('name', ['strings', 'booleans', 'nested', 'null_value', 'null_properties'])
def test_raw_property_multisets_positive_and_corrupt(runner, name):
    case = next(c for c in case_specs(runner.run_id, 'javascript') if c['name'] == name)
    rows = raw_fixture(runner, case)
    with patch('e2e_runner.run_abs', return_value=(0, json.dumps(rows))):
        assert runner._verify_arrival('javascript', case)['pass']
    rows[-1]['properties'] = '{"wrong":true}'
    with patch('e2e_runner.run_abs', return_value=(0, json.dumps(rows))):
        assert not runner._verify_arrival('javascript', case)['pass']


@pytest.mark.parametrize('field,value', [('agent','other-sdk'), ('unit_type','wrong'), ('application_id',91),
                                        ('environment','wrong'), ('unit_attributes','{}')])
def test_raw_identity_mutations(runner, field, value):
    case = next(c for c in case_specs(runner.run_id, 'javascript') if c['name'] == 'strings')
    rows = raw_fixture(runner, case)
    rows[0][field] = value
    with patch('e2e_runner.run_abs', return_value=(0,json.dumps(rows))):
        assert not runner._verify_arrival('javascript',case)['pass']


def test_unicode_hash_independent_fixture(runner):
    case = next(c for c in case_specs(runner.run_id,'javascript') if c['name']=='unicode')
    rows = raw_fixture(runner,case)
    case['uid'] = '用戶-🧪-é'
    import hashlib, base64
    # Fixture uses literal UTF-8 octets, independent of UID encoding in production.
    expected = base64.urlsafe_b64encode(hashlib.md5(bytes.fromhex('e794a8e688b62df09fa7aa2dc3a9')).digest()).decode().rstrip('=')
    rows[0]['unit_uid'] = expected
    with patch('e2e_runner.run_abs',return_value=(0,json.dumps(rows))) as cli:
        assert runner._verify_arrival('javascript',case)['pass']
        assert expected in cli.call_args.args[0]


def test_session_requires_distinct_metric_and_raw_exposure(runner):
    case = next(c for c in case_specs(runner.run_id,'javascript') if c['name']=='multiple_units')
    rows = raw_fixture(runner,case)
    with patch('e2e_runner.run_abs', return_value=(0,json.dumps(rows))):
        assert not runner._verify_arrival('javascript',case)['pass']
    def query(mid,sdk,name,*rest):
        value = backend(runner,sdk,name,mid)
        if rest:
            assert rest == (101,)
            value['variants'][0]['unit_count'] = 0
        return value
    with patch.object(runner,'_query_metric',side_effect=query), patch.object(runner,'_verify_arrival',return_value={'pass':True}):
        assert verify(runner)['event_cases']['javascript']['multiple_units']['status']=='fail'


@pytest.mark.parametrize('key,field,wrong', [('amount','value_source_property','wrong/path'),
    ('nested','value_source_property','wrong/path'), ('eur','property_filter',{'filter':{'and':[]}}),
    ('true','property_filter',{'filter':{'and':[]}}), ('false','property_filter',{'filter':{'and':[]}})])
def test_existing_metric_validation(runner,key,field,wrong):
    specs=metric_specs()
    runner.goal_id='183'
    def cli(args,*a,**kw):
        spec=specs[next(k for k,v in runner.case_metric_ids.items() if v==args[2])]
        detail={'type':spec['type'],'goal_id':183,'outlier_limit_method':'unlimited',
                'value_source_property':spec.get('path'),'property_filter':spec.get('filter')}
        if spec is specs[key]: detail[field]=wrong
        return 0,json.dumps(detail)
    with patch.object(runner,'_find_resource',side_effect=lambda resource,name: runner.case_metric_ids[next(k for k,s in specs.items() if s['name']==name)]), patch('e2e_runner.run_abs',side_effect=cli), pytest.raises(RuntimeError,match='wrong property'):
        runner._ensure_case_metrics()


def test_metric_definitions_are_pinned_literally():
    specs = metric_specs()
    assert specs["eur"]["filter"] == {"filter": {"and": [{"eq": [{"var": {"path": "currency"}}, {"value": "EUR"}]}]}}
    assert specs["true"]["filter"] == {"filter": {"and": [{"eq": [{"var": {"path": "gift"}}, {"value": True}]}]}}
    assert specs["false"]["filter"] == {"filter": {"and": [{"eq": [{"var": {"path": "gift"}}, {"value": False}]}]}}
    assert specs["nested"]["path"] == "order/total"
    assert specs["amount"]["path"] == "amount"
    assert specs["events"]["type"] == "goal_count"


def test_unmeasurable_zero_expectation_fails(runner):
    # unicode expects 0 events and amount 0: a missing measurement must not read as 0.
    assert measured_total(None, "count") is None
    def query(mid, sdk, name, *rest):
        if name == "unicode" and mid == runner.case_metric_ids["amount"]:
            return None
        return backend(runner, sdk, name, mid)
    with patch.object(runner, "_query_metric", side_effect=query), \
         patch.object(runner, "_verify_arrival", return_value={"pass": True}):
        result = verify(runner)
    row = result["event_cases"]["javascript"]["unicode"]
    assert row["status"] == "fail" and row["metrics"]["amount"]["actual"] is None


def _sequence(runner, name):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == name)
    created = {"result": {"contextId": "ctx", "ready": True, "failed": False, "preReadyTracked": True}}
    with patch.object(runner, "_wrapper_post", return_value=created) as post, \
         patch("e2e_runner.requests.delete"):
        runner._send_event_case("javascript", "http://wrapper", case)
    return [(c.args[0], c.args[1] if len(c.args) > 1 else None) for c in post.call_args_list]


def test_multiple_units_exposes_both_experiments(runner):
    treatments = [body["experimentName"] for url, body in _sequence(runner, "multiple_units") if url.endswith("/treatment")]
    assert treatments == ["e2e-abcd1234", "e2e-abcd1234-session"]


def test_pre_ready_primes_exposure_before_goal_context(runner):
    calls = _sequence(runner, "pre_ready")
    kinds = [url.rsplit("/", 1)[-1] for url, _ in calls]
    assert kinds == ["context", "treatment", "publish", "context", "treatment", "publish"]


def test_foreign_unit_rows_are_ignored(runner):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "strings")
    rows = raw_fixture(runner, case)
    rows.append(dict(rows[-1], unit_uid="someone-else", properties='{"currency":"GBP"}', agent="other-sdk"))
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert runner._verify_arrival("javascript", case)["pass"]


@pytest.mark.parametrize("sdk,agent,ok", [
    ("python", "absmartly-python-sdk/0.4.0", True),
    ("python", "absmartly-python-sdk", False),
    ("javascript", "absmartly-javascript-sdk", True),
])
def test_agent_version_required_unless_policy_excepts(runner, sdk, agent, ok):
    case = next(c for c in case_specs(runner.run_id, sdk) if c["name"] == "strings")
    rows = raw_fixture(runner, case)
    for row in rows:
        row["agent"] = agent
    rows[0]["unit_attributes"] = json.dumps({"sdk_name": sdk, "e2e_case": "strings"})
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert runner._verify_arrival(sdk, case)["pass"] is ok


def test_numbers_compare_by_value_not_spelling(runner):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "numeric_0")
    rows = raw_fixture(runner, case)
    rows[-1]["properties"] = '{"amount":10.0}'
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert runner._verify_arrival("javascript", case)["pass"]
    rows[-1]["properties"] = '{"amount":10.000001}'
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert not runner._verify_arrival("javascript", case)["pass"]


def test_python_null_value_is_reviewed_exception(runner):
    case = next(c for c in case_specs(runner.run_id, "python") if c["name"] == "null_value")
    assert "strip_nulls" in runner._case_skip("python", case)
    assert runner._case_skip("go", next(c for c in case_specs(runner.run_id, "go") if c["name"] == "null_value")) is None


def test_poll_respects_deadline(runner):
    clock = [0.0]
    def slow_query(mid, sdk, name, *rest, **kw):
        # Every SDK's batch lands except the last, so the loop must walk all of them.
        clock[0] += 100
        count = BATCH_SIZE if sdk != "sdk21" else 0
        return {"metric_id": mid, "variants": [{"variant": 0, "unit_count": 1, "count": count}]}
    runner.timeout = 150
    runner.sdks = {f"sdk{i}": "http://x" for i in range(22)}
    runner.sdk_results = {s: {"status": "ok", "exposures_sent": 1} for s in runner.sdks}
    runner.case_results = {s: {"batch": {"status": "sent"}} for s in runner.sdks}
    with patch("e2e_runner.time.time", side_effect=lambda: clock[0]), \
         patch("e2e_runner.time.sleep", side_effect=lambda t: clock.__setitem__(0, clock[0] + t)), \
         patch("e2e_runner.run_abs", return_value=(0, "")), \
         patch.object(runner, "_fetch_metrics", return_value={"variants": [{"unit_count": 99}]}), \
         patch.object(runner, "_query_metric", side_effect=slow_query) as q:
        runner.wait_for_metrics()
    assert q.call_count <= 3


# --- Review round 3: one killing test per surviving false-pass mutant. ---

def _all_backend(runner):
    return lambda mid, sdk, name, *rest, **kw: backend(runner, sdk, name, mid)


def test_verify_metrics_checks_event_cases(runner):
    # M2: verify_metrics must run the case check, not just the baseline.
    runner.sdk_results = {"javascript": {"status": "ok", "planned_exposures": 2, "planned_goals": 1}}
    def query(mid, sdk, name="baseline", *rest, **kw):
        if name == "baseline":
            count = 1 if mid == runner.metric_id else 10
            return {"metric_id": mid, "variants": [{"variant": 0, "unit_count": 2, "count": count}]}
        if name == "batch":
            return None
        return backend(runner, sdk, name, mid)
    with patch.object(runner, "_query_metric", side_effect=query), \
         patch.object(runner, "_verify_arrival", return_value={"pass": True}):
        result = runner.verify_metrics()
    assert result["sdks"]["javascript"]["status"] == "ok"
    assert result["event_cases"]["javascript"]["batch"]["status"] == "fail"
    assert not result["overall_pass"]


def test_raw_arrival_failure_fails_case(runner):
    # M4: a failing raw collector check fails the case even when metrics match.
    with patch.object(runner, "_query_metric", side_effect=_all_backend(runner)), \
         patch.object(runner, "_verify_arrival", side_effect=lambda sdk, case: {"pass": case["name"] != "strings", "errors": ["x"]}):
        result = verify(runner)
    assert result["event_cases"]["javascript"]["strings"]["status"] == "fail"
    assert result["event_cases"]["javascript"]["booleans"]["status"] == "pass"
    assert not result["overall_pass"]


def test_unsent_case_fails_run(runner):
    # M11: a case whose send failed fails the run.
    runner.case_results["javascript"]["batch"] = {"status": "fail", "reason": "wrapper 500"}
    with patch.object(runner, "_query_metric", side_effect=_all_backend(runner)), \
         patch.object(runner, "_verify_arrival", return_value={"pass": True}):
        result = verify(runner)
    assert result["event_cases"]["javascript"]["batch"]["status"] == "fail"
    assert not result["overall_pass"]


def _baseline(runner, exposures, goals, revenue):
    runner.sdk_results = {"javascript": {"status": "ok", "planned_exposures": 4, "planned_goals": 2}}
    def query(mid, sdk, name="baseline", *rest, **kw):
        if mid == runner.metric_id:
            return {"metric_id": mid, "variants": [{"variant": 0, "unit_count": exposures, "count": goals}]}
        assert mid == runner.case_metric_ids["amount"]
        return {"metric_id": mid, "variants": [{"variant": 0, "unit_count": exposures, "count": revenue}]}
    result = {"sdks": {}, "overall_pass": True}
    with patch.object(runner, "_query_metric", side_effect=query):
        runner._verify_active_sdks(result, runner.sdk_results)
    return result


def test_baseline_measures_each_number(runner):
    # M7/M8: exposures gate, and revenue comes from the amount metric, never the count.
    assert _baseline(runner, 4, 2, 20)["overall_pass"]
    assert not _baseline(runner, 3, 2, 20)["overall_pass"]
    assert not _baseline(runner, 4, 2, 2)["overall_pass"]
    assert not _baseline(runner, 4, 1, 20)["overall_pass"]


def test_exposure_to_another_experiment_does_not_count(runner):
    # M6
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "strings")
    rows = raw_fixture(runner, case)
    rows[0]["experiment_id"] = 999
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert not runner._verify_arrival("javascript", case)["pass"]


def test_repeated_goals_are_a_multiset(runner):
    # M5: one of seven identical goals arriving must fail.
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "repeated")
    rows = raw_fixture(runner, case)
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert runner._verify_arrival("javascript", case)["pass"]
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows[:2]))):
        assert not runner._verify_arrival("javascript", case)["pass"]


def test_poll_waits_for_sentinels(runner):
    # M1: baseline participants alone must not end the poll; the batch sentinel must land.
    clock = [0.0]
    runner.timeout = 60
    runner.sdk_results = {"javascript": {"status": "ok", "exposures_sent": 1}}
    runner.case_results = {"javascript": {"batch": {"status": "sent"}}}
    landed = [False, False, True]
    def query(mid, sdk, name, *rest, **kw):
        assert (sdk, name) == ("javascript", "batch")
        count = BATCH_SIZE if landed.pop(0) else 0
        return {"metric_id": mid, "variants": [{"variant": 0, "unit_count": 1, "count": count}]}
    with patch("e2e_runner.time.time", side_effect=lambda: clock[0]), \
         patch("e2e_runner.time.sleep", side_effect=lambda t: clock.__setitem__(0, clock[0] + t)), \
         patch("e2e_runner.run_abs", return_value=(0, "")), \
         patch.object(runner, "_fetch_metrics", return_value={"variants": [{"unit_count": 99}]}), \
         patch.object(runner, "_query_metric", side_effect=query) as q:
        runner.wait_for_metrics()
    assert q.call_count == 3 and not landed
    assert clock[0] == 10


def test_case_expectations_are_literal(runner):
    # M19/M21/M22: expected values pinned, not read back from the code under test.
    cases = {c["name"]: c["expected"] for c in case_specs(runner.run_id, "javascript")}
    assert cases["nested"]["nested"] == Decimal("6.6")
    assert cases["strings"]["eur"] == 2
    assert (cases["booleans"]["true"], cases["booleans"]["false"]) == (2, 1)
    assert cases["no_properties"]["events"] == 1 and cases["null_properties"]["events"] == 1
    assert cases["null_properties"]["amount"] == 0
    assert cases["repeated"]["events"] == 7 and cases["batch"]["events"] == 300
    assert cases["numeric_total"]["amount"] == Decimal("1000016.6")
    assert [cases[f"numeric_{i}"]["amount"] for i in range(6)] == \
        [10, Decimal("10.5"), Decimal("0.1"), -4, 0, 1000000]


def test_429_in_an_id_does_not_retry():
    fail = Mock(returncode=1, stdout="", stderr="Error: metric 429 not found")
    with patch("e2e_runner.subprocess.run", return_value=fail) as run, patch("e2e_runner.time.sleep") as sleep:
        assert run_abs(["metrics", "get", "429"])[0] == 1
    assert run.call_count == 1 and not sleep.called


def test_429_retry_stops_at_deadline():
    clock = [0.0]
    fail = Mock(returncode=1, stdout="", stderr="API error: Request failed with status code 429")
    with patch("e2e_runner.subprocess.run", return_value=fail) as run, \
         patch("e2e_runner.time.time", side_effect=lambda: clock[0]), \
         patch("e2e_runner.time.sleep", side_effect=lambda t: clock.__setitem__(0, clock[0] + t)):
        run_abs(["metrics", "list"], deadline=150)
    assert run.call_count == 2 and clock[0] == 120


def test_deadline_reaches_every_poll_cli_call(runner):
    # Every CLI call made while polling, including the final fetch, carries the deadline.
    clock = [1000.0]
    runner.timeout = 30
    runner.sdk_results = {"javascript": {"status": "ok", "exposures_sent": 1}}
    runner.case_results = {"javascript": {"batch": {"status": "sent"}}}
    deadlines = []
    def cli(args, *a, **kw):
        if args[:3] == ["experiments", "metrics", "results"]:
            deadlines.append(kw.get("deadline"))
            return 0, json.dumps([{"metric_id": 1, "variants": [{"variant": 0, "unit_count": 0, "count": 0}]}])
        return 0, ""
    with patch("e2e_runner.time.time", side_effect=lambda: clock[0]), \
         patch("e2e_runner.time.sleep", side_effect=lambda t: clock.__setitem__(0, clock[0] + t)), \
         patch("e2e_runner.run_abs", side_effect=cli):
        runner.wait_for_metrics()
    assert deadlines and set(deadlines) == {1030.0}


def test_sentinel_query_carries_deadline(runner):
    runner.case_results = {"javascript": {"batch": {"status": "sent"}}}
    seen = []
    def cli(args, *a, **kw):
        seen.append(kw.get("deadline"))
        return 0, json.dumps([{"metric_id": runner.case_metric_ids["events"],
                               "variants": [{"variant": 0, "unit_count": 1, "count": BATCH_SIZE}]}])
    with patch("e2e_runner.run_abs", side_effect=cli), patch("e2e_runner.time.time", return_value=0):
        assert runner._sentinels_landed(42.0)
    assert seen == [42.0]


def test_other_goal_names_do_not_count(runner):
    case = next(c for c in case_specs(runner.run_id, "javascript") if c["name"] == "strings")
    rows = raw_fixture(runner, case)
    rows.append(dict(rows[-1], goal_name="refund", properties='{"currency":"GBP"}'))
    with patch("e2e_runner.run_abs", return_value=(0, json.dumps(rows))):
        assert runner._verify_arrival("javascript", case)["pass"]
