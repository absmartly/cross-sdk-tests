#!/usr/bin/env python3
"""Generate the Context Fetch Compression group of test_scenarios_complete.json.

Every scenario in the group serves the same context through a different path:

  * Gzip When Advertised (ungated, every SDK): the SDK's own GET {endpoint}/context
    goes to the negotiating context server (context-server/server.py), which
    answers gzip only when the request advertised gzip and identity otherwise.
    The SDK must load it, refresh through the same path, and produce exactly the
    treatment, variable, exposure and publish of the direct-data twin.
  * Direct Data Twin (ungated): the same context built from data handed to the
    SDK (createContextWith with `directData`), no HTTP at all. Its shared steps
    are emitted by the same builder, so the expectations are identical by
    construction.
  * Request Advertises Gzip (gated on contextFetchCompression): the recorded
    Accept-Encoding of the create AND the refresh fetch must advertise gzip and
    the gzip body must decode to the same context.

The treatment and variable expectations are the ones the suite already pins for
exp_test_ab and this uid (scenarios 32 and 138); the filler experiments are
never queried and only give the payload a realistic, compressible size.

Usage:
    python3 generate_context_compression_scenarios.py          # rewrite the group in place
    python3 generate_context_compression_scenarios.py --check  # exit 1 if the file is stale
"""

import argparse
import copy
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")

GROUP_MARKER = " - Context Fetch Compression - "
CAPABILITY = "contextFetchCompression"
# The retained scenarios this group was generated against (origin/main 30d23e9).
BASELINE_COUNT = 222
BASELINE_SHA256 = "2d43943c77694d696c36e4f32defec4cbb3be20ec6f71196102672fb686a6a59"

UID = "e791e240fcd3df7d238cfc285f475e8152fcc0ec"
FILLER_EXPERIMENTS = 24


def baseline_digest(scenarios):
    return hashlib.sha256(json.dumps(scenarios, sort_keys=True).encode()).hexdigest()


def tested_experiment():
    return {
        "id": 1, "name": "exp_test_ab", "iteration": 1, "unitType": "session_id",
        "seedHi": 3603515, "seedLo": 233373850, "split": [0.5, 0.5],
        "trafficSeedHi": 449867249, "trafficSeedLo": 455443629, "trafficSplit": [0, 1],
        "fullOnVariant": 0, "applications": [{"name": "website"}],
        "variants": [
            {"name": "A", "config": None},
            {"name": "B", "config": "{\"banner.border\":1,\"banner.size\":\"large\"}"},
        ],
        "audience": "", "audienceStrict": False, "customFieldValues": None,
    }


def filler_experiment(n):
    return {
        "id": 100 + n, "name": f"exp_compression_filler_{n:02d}", "iteration": 1, "unitType": "user_id",
        "seedHi": 1000 + n, "seedLo": 2000 + n, "split": [0.34, 0.33, 0.33],
        "trafficSeedHi": 3000 + n, "trafficSeedLo": 4000 + n, "trafficSplit": [0, 1],
        "fullOnVariant": 0, "applications": [{"name": "website"}],
        "variants": [
            {"name": "A", "config": json.dumps({f"filler.{n:02d}.color": "red", f"filler.{n:02d}.size": 1})},
            {"name": "B", "config": json.dumps({f"filler.{n:02d}.color": "green", f"filler.{n:02d}.size": 2})},
            {"name": "C", "config": json.dumps({f"filler.{n:02d}.color": "blue", f"filler.{n:02d}.size": 3})},
        ],
        "audience": json.dumps({"filter": [{"gte": [{"var": "age"}, {"value": 18 + n}]}]}),
        "audienceStrict": False, "customFieldValues": None,
    }


def context_data():
    return {"experiments": [tested_experiment()] + [filler_experiment(n) for n in range(FILLER_EXPERIMENTS)]}


def experiment_refs(data):
    return [{"id": e["id"], "name": e["name"]} for e in data["experiments"]]


EXPOSURE = {
    "id": 1, "name": "exp_test_ab", "unit": "session_id", "variant": 1, "assigned": True,
    "eligible": True, "overridden": False, "fullOn": False, "custom": False, "audienceMismatch": False,
}


def create_step(action, data):
    return {
        "action": action,
        "params": {"units": {"session_id": UID}, "options": {"publishDelay": -1, "refreshPeriod": 0}},
        "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                   "events": [{"type": "ready", "data": {"experiments": experiment_refs(data)}}]},
    }


def treatment_step(exposed):
    return {"action": "treatment", "params": {"experimentName": "exp_test_ab"},
            "expect": {"result": 1, "events": [{"type": "exposure", "data": dict(EXPOSURE)}] if exposed else []}}


def variable_step():
    return {"action": "variableValue", "params": {"key": "banner.size", "defaultValue": "small"},
            "expect": {"result": "large", "events": []}}


def refresh_step(data):
    return {"action": "refresh", "params": {"newData": copy.deepcopy(data)},
            "expect": {"result": None, "events": [{"type": "refresh", "data": {"experiments": experiment_refs(data)}}]}}


def publish_step():
    return {"action": "publish", "params": {},
            "expect": {"result": None, "events": [{"type": "publish", "data": {
                "exposures": [{"id": 1, "name": "exp_test_ab", "variant": 1}]}}]}}


def build_group(first_number):
    data = context_data()
    gzip_server = {"encodings": ["gzip", "identity"]}
    scenarios = [
        {
            "name": "Gzip When Advertised Matches Direct Data",
            "description": (
                "UNGATED (every SDK): the SDK's own GET {endpoint}/context is served by the negotiating "
                "context server, gzip only when the request advertised gzip and identity otherwise, with "
                "Content-Encoding and Vary: Accept-Encoding. The SDK must load the context, refresh it "
                "through the same fetch, and produce exactly the treatment, variable value, exposure and "
                "publish of the Direct Data Twin. Each fetch's served encoding is checked against the "
                "recorded Accept-Encoding."
            ),
            "contextServer": gzip_server,
            "contextData": data,
            "steps": [create_step("createContext", data), treatment_step(True), variable_step(),
                      refresh_step(data), treatment_step(False), publish_step()],
        },
        {
            "name": "Direct Data Twin",
            "description": (
                "UNGATED twin of Gzip When Advertised: the same context handed to the SDK as data "
                "(createContextWith, no HTTP). Its steps are the twin's create, treatment, variable "
                "value and publish, with identical expectations."
            ),
            "directData": True,
            "contextData": data,
            "steps": [create_step("createContextWith", data), treatment_step(True), variable_step(),
                      publish_step()],
        },
        {
            "name": "Request Advertises Gzip On Create And Refresh",
            "description": (
                "GATED on contextFetchCompression (measured by the orchestrator, not reported by the "
                "wrapper): the recorded Accept-Encoding of the SDK's own GET {endpoint}/context on "
                "create AND on refresh advertises gzip, the server answers gzip, and the decoded "
                "context serves the same treatment."
            ),
            "requires": [CAPABILITY],
            "contextServer": dict(gzip_server, requireAdvertised=["gzip"]),
            "contextData": data,
            "steps": [create_step("createContext", data), treatment_step(True), refresh_step(data),
                      treatment_step(False)],
        },
    ]
    for offset, scenario in enumerate(scenarios):
        scenario["name"] = f"{first_number + offset}{GROUP_MARKER}{scenario['name']}"
    return scenarios


def scenario_number(scenario):
    return int(scenario["name"].split(" - ", 1)[0])


def place_group(existing):
    """Replace the group in place at its first ID, or append it after the max retained ID."""
    positions = [i for i, s in enumerate(existing) if GROUP_MARKER in s["name"]]
    if positions:
        start, stop = positions[0], positions[-1] + 1
        if len(positions) != stop - start:
            raise ValueError("the Context Fetch Compression group is not contiguous")
        first_number = scenario_number(existing[start])
    else:
        start = stop = len(existing)
        first_number = max((scenario_number(s) for s in existing), default=0) + 1
    group = build_group(first_number)
    if stop < len(existing) and first_number + len(group) > scenario_number(existing[stop]):
        raise ValueError(f"the group would collide with {existing[stop]['name']!r}")
    return existing[:start] + group + existing[stop:], group


def render(scenarios):
    return json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    args = parser.parse_args()

    with open(CANONICAL, encoding="utf-8") as src:
        text = src.read()
    existing = json.loads(text)
    kept = [s for s in existing if GROUP_MARKER not in s["name"]]
    if len(kept) != BASELINE_COUNT or baseline_digest(kept) != BASELINE_SHA256:
        print("retained scenarios differ from the 30d23e9 baseline; review and update BASELINE_*",
              file=sys.stderr)
        return 1
    updated, group = place_group(existing)
    rendered = render(updated)
    if args.check:
        if text != rendered:
            print("test_scenarios_complete.json is stale: run generate_context_compression_scenarios.py")
            return 1
        print(f"up to date: {len(group)} context-fetch-compression scenarios")
        return 0
    with open(CANONICAL, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(group)} context-fetch-compression)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
