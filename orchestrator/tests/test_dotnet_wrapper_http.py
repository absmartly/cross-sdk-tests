"""HTTP regressions for the dotnet wrapper's input and report boundaries.

Run against a live wrapper:  DOTNET_WRAPPER_URL=http://127.0.0.1:3000 python3 -m pytest orchestrator/tests/test_dotnet_wrapper_http.py -q
Skipped when DOTNET_WRAPPER_URL is unset (the lanes cover the wrapper end to end in CI).
"""

import json
import os

import pytest
import requests

URL = os.environ.get("DOTNET_WRAPPER_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DOTNET_WRAPPER_URL not set")

ANCHOR = 1_704_067_200_000
PERIOD_MS = 400_000 * 3_600_000


def _experiment(exp_id, name, extra=None):
    exp = {
        "id": exp_id, "name": name, "unitType": "store_id", "iteration": 1, "seedHi": 0, "seedLo": 42,
        "split": [0.5, 0.5], "trafficSeedHi": 1, "trafficSeedLo": 2, "trafficSplit": [0, 1], "fullOnVariant": 0,
        "applications": [{"name": "website"}], "variants": [{"name": "A", "config": None}, {"name": "B", "config": None}],
        "audienceStrict": False,
    }
    exp.update(extra or {})
    return exp


def _context(experiments, units=None):
    body = {"data": {"experiments": experiments}, "units": units or {"store_id": "s-1"},
            "options": {"publishDelay": -1, "refreshPeriod": 0}}
    response = requests.post(f"{URL}/context", json=body, timeout=10)
    response.raise_for_status()
    return response.json()["result"]["contextId"]


def _post(context_id, action, body=None):
    response = requests.post(f"{URL}/context/{context_id}/{action}", json=body or {}, timeout=10)
    response.raise_for_status()
    return response


def _publish_data(context_id):
    raw = _post(context_id, "publish").text
    event = next(e for e in json.loads(raw)["events"] if e["type"] == "publish")
    return raw, event["data"]


def test_case_distinct_goal_properties_survive_publish():
    context_id = _context([_experiment(1, "exp_unit")])
    _post(context_id, "track", {"goalName": "purchase", "properties": {"OrderID": 1, "orderID": 2, "SKU": 3}})
    raw, data = _publish_data(context_id)
    assert data["goals"][0]["properties"] == {"OrderID": 1, "orderID": 2, "SKU": 3}
    assert raw.count('"OrderID"') == 1 and raw.count('"orderID"') == 1


def test_null_attribute_value_is_reported_without_a_value_member():
    context_id = _context([_experiment(1, "exp_unit")])
    _post(context_id, "attribute", {"name": "v_null", "value": None})
    _post(context_id, "attribute", {"name": "v_one", "value": 1})
    _post(context_id, "track", {"goalName": "g"})
    _, data = _publish_data(context_id)
    attributes = {a["name"]: a for a in data["attributes"]}
    assert "value" not in attributes["v_null"]
    assert attributes["v_one"]["value"] == 1


def test_integer_attribute_beyond_int64_keys_its_cluster():
    sb = _experiment(2, "exp_sb", {
        "randomizationType": "switchback", "switchbackClusters": {"store_id": "store_number"},
        "switchbackStartAt": ANCHOR, "switchbackPeriodMs": PERIOD_MS, "split": [0.5, 0.5],
    })
    for raw_value, exposed in (("9223372036854775808", 1), ("3.0", 0)):
        context_id = _context([sb])
        # Sent as a raw JSON number token, not through a Python int/float.
        body = '{"name":"store_number","value":' + raw_value + '}'
        requests.post(f"{URL}/context/{context_id}/attribute", data=body,
                      headers={"Content-Type": "application/json"}, timeout=10).raise_for_status()
        events = _post(context_id, "treatment", {"experimentName": "exp_sb"}).json()["events"]
        assert len([e for e in events if e["type"] == "exposure"]) == exposed, raw_value


@pytest.mark.parametrize("route", ["assign", "permutation"])
def test_switchback_routes_decode_strings_as_java_does(route):
    index = "periodIndex" if route == "assign" else "cycleIndex"
    base = {"clusterUnit": "market_1", index: "0", "seed": "42", "armCount": 3}
    ok = requests.post(f"{URL}/switchback/{route}", json=dict(base, seed="+42"), timeout=10)
    assert ok.status_code == 200
    assert ok.json()["result"] == requests.post(f"{URL}/switchback/{route}", json=base, timeout=10).json()["result"]
    for bad in ("", "+", "-", "4 2", "4.2", "1e3", "٤٢"):
        response = requests.post(f"{URL}/switchback/{route}", json=dict(base, seed=bad), timeout=10)
        assert response.status_code == 400, bad
    # A lone surrogate escape cannot be a decimal: JSON 400, not an empty 500.
    response = requests.post(f"{URL}/switchback/{route}",
                             data='{"clusterUnit":"market_1","%s":"0","seed":"\\uD800","armCount":3}' % index,
                             headers={"Content-Type": "application/json"}, timeout=10)
    assert response.status_code == 400
    assert "error" in response.json()
