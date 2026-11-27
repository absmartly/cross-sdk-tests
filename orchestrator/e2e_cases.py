"""Collector-arrival cases. Expectations describe calls planned, never wrapper successes."""
from decimal import Decimal
import hashlib
import json


# The SDK language in a lane's X-Agent (absmartly-<language>-sdk/...) where it is not the lane name:
# flutter uses the dart client, the -v1 lanes are the 1.x SDKs, liquid wraps ruby-sdk, and the
# framework wrappers wrap javascript-sdk.
AGENT_LANGUAGE = {
    "swift-v1": "swift", "flutter": "dart", "java-v1": "java", "liquid": "ruby",
    "react": "javascript", "vue2": "javascript", "vue3": "javascript", "angular": "javascript",
}

NUMERIC_VALUES = (10, 10.5, 0.1, -4, 0, 1000000)


def exact_number(value) -> str:
    """A JSON number as an exact, comparable string: equal values (10, 10.0, 1e1) give the same
    string and distinct values never do. No arithmetic, so no rounding and no exponent overflow."""
    if isinstance(value, bool):
        raise ValueError(f"not a number: {value!r}")
    if isinstance(value, float):
        value = Decimal(repr(value))
    if isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise ValueError(f"not a number: {value!r}")
    if not value.is_finite():
        raise ValueError("non-finite number")
    sign, digits, exponent = value.as_tuple()
    digits = "".join(map(str, digits)).lstrip("0")
    if not digits:
        return "0"
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    return f"{'-' if sign else ''}{stripped}e{exponent}"

BATCH_SIZE = 300


def eq_filter(**values):
    return {"filter": {"and": [
        {"eq": [{"var": {"path": path}}, {"value": value}]}
        for path, value in values.items()
    ]}}


def metric_specs():
    return {
        "events": {"name": "e2e_purchase_events", "type": "goal_count"},
        "amount": {"name": "e2e_purchase_revenue", "type": "goal_property", "path": "amount"},
        "eur": {"name": "e2e_purchase_eur_events", "type": "goal_count",
                "filter": eq_filter(currency="EUR")},
        "true": {"name": "e2e_purchase_true_events", "type": "goal_count",
                 "filter": eq_filter(gift=True)},
        "false": {"name": "e2e_purchase_false_events", "type": "goal_count",
                  "filter": eq_filter(gift=False)},
        # Backend JsonPathParser uses '/', not '.', to descend into objects.
        "nested": {"name": "e2e_purchase_order_total", "type": "goal_property", "path": "order/total"},
    }


def case_specs(run_id, sdk):
    prefix = f"e2e-{run_id}-{sdk}"
    cases = [
        {"name": "close", "goals": [{"amount": 3}], "close": True},
        {"name": "pre_ready", "goals": [{"amount": 7}], "pre_ready": True,
         "capability": "preReadyGoals"},
        {"name": "repeated", "goals": [{"amount": 1}] * 7},
        {"name": "batch", "goals": [{"amount": 1}] * BATCH_SIZE},
        {"name": "unicode", "uid": prefix + "-用戶-🧪-é", "goals": []},
        {"name": "long_id", "uid": prefix + "-" + "x" * 1024, "goals": []},
        {"name": "numeric_id", "uid": int(run_id, 16) * 1000000 + int.from_bytes(hashlib.sha256(sdk.encode()).digest()[:2], "big"), "goals": [],
         "capability": "numericUnitIds"},
        {"name": "multiple_units", "goals": [], "session": prefix + "-session"},
        {"name": "strings", "goals": [{"currency": v} for v in ("EUR", "USD", "EUR")]},
        {"name": "nested", "goals": [{"order": {"total": v}} for v in (10.5, 0.1, -4)]},
        {"name": "booleans", "goals": [{"gift": v} for v in (True, False, True)]},
        {"name": "no_properties", "goals": [None], "omit_properties": True},
        {"name": "null_properties", "goals": [None]},
        {"name": "null_value", "goals": [{"amount": None}], "capability": "nullPropertyValues"},
        {"name": "numeric_total", "goals": [{"amount": v} for v in NUMERIC_VALUES]},
    ]
    for i, value in enumerate(NUMERIC_VALUES):
        cases.append({"name": f"numeric_{i}", "goals": [{"amount": value}]})
    for case in cases:
        case.setdefault("uid", prefix + "-" + case["name"])
        goals = case["goals"]
        case["expected"] = {
            "exposures": 1,
            "events": len(goals),
            "amount": sum((Decimal(str(g["amount"])) for g in goals
                           if g and g.get("amount") is not None), Decimal(0)),
        }
        if case["name"] == "strings":
            case["expected"]["eur"] = 2
        if case["name"] == "booleans":
            case["expected"].update({"true": 2, "false": 1})
        if case["name"] == "nested":
            case["expected"]["nested"] = Decimal("6.6")
    return cases


def measured_total(metric, field):
    """A missing, malformed or non-finite backend number is unmeasurable."""
    if not isinstance(metric, dict):
        return None
    variants = metric.get("variants")
    if not isinstance(variants, list) or not variants:
        return None
    total = Decimal(0)
    seen = set()
    for variant in variants:
        if not isinstance(variant, dict):
            return None
        index = variant.get("variant")
        if type(index) is not int or index not in (0, 1) or index in seen:
            return None
        seen.add(index)
        value = variant.get(field)
        if type(value) not in (int, float):
            return None
        number = Decimal(str(value))
        if not number.is_finite():
            return None
        if field == "unit_count" and (number < 0 or number != number.to_integral_value()):
            return None
        total += number
    return total


def matches(expected, actual):
    if actual is None:
        return False
    expected, actual = Decimal(str(expected)), Decimal(str(actual))
    # Absolute arithmetic noise only: no relative tolerance that would hide 0.1
    # being rounded away when combined with the large value.
    return actual.is_finite() and abs(expected - actual) <= Decimal("0.000000001")



def exact_json(value) -> str:
    """A type-tagged JSON value with exact numbers, as a comparable string."""
    def walk(v):
        if isinstance(v, dict):
            return ["object", [[k, walk(v[k])] for k in sorted(v)]]
        if isinstance(v, list):
            return ["array", [walk(x) for x in v]]
        if isinstance(v, bool):
            return ["boolean", v]
        if v is None:
            return ["null"]
        if isinstance(v, str):
            return ["string", v]
        return ["number", exact_number(v)]
    return json.dumps(walk(value), ensure_ascii=False, separators=(",", ":"))
