#!/usr/bin/env python3
"""Generate the Context Fetch Compression group of test_scenarios_complete.json.

Every scenario in the group serves the same context through a different path:

  * Gzip When Advertised (ungated, every SDK): the SDK's own GET {endpoint}/context
    goes to the negotiating context server (context-server/server.py), which
    answers gzip only when the request advertised gzip and identity otherwise.
    The SDK must load it and produce exactly the treatment, variable, exposure
    and publish of the direct-data twin, then refresh through the same fetch
    and serve the refreshed data.
  * Direct Data Twin (ungated): the same context built from data handed to the
    SDK (createContextWith with `directData`), no HTTP at all. Its shared steps
    are emitted by the same builder, so the expectations are identical by
    construction.
  * Request Advertises Gzip (gated on contextFetchCompression): the recorded
    Accept-Encoding of the create AND the refresh fetch must advertise gzip and
    each gzip body must decode (the refreshed one is checked by its new value).
  * Brotli Offered (ungated, every SDK; 273): the same fetch against a payload that
    allows br > gzip > identity (negotiation.BR_PREFERENCE, Cloudflare's order among
    them). Whatever the SDK negotiates (br, gzip or identity) it must produce exactly
    the Direct Data Twin's (271) treatment, variable, exposure and publish, then
    refresh through its own fetch and serve the refreshed data: offering br never
    breaks an SDK. It shares 271 as its twin (same builder, same expectations).
  * Request Advertises Brotli (gated on contextFetchBrotli; 274): the recorded
    Accept-Encoding of the create AND the refresh fetch must advertise br. The
    server then serves br (every fetch is re-checked against the shared
    negotiation, so br advertised on a br payload means br served) and both bodies
    must decode, the refreshed one checked by its new value.

The gzip scenarios (270-272) keep negotiation.DEFAULT_PREFERENCE (gzip or identity):
br is never served to them, so an SDK that advertises br (swift) sees exactly what
it saw before the br scenarios existed.

The refreshed data differs from the created data: exp_test_ab's traffic split
changes to [0.5, 0.5], so this uid falls out of traffic and the next treatment
is 0 with a fresh, ineligible exposure. An SDK that refetches but keeps the old
data returns 1 with no event. The orchestrator stores the refreshed data on the
context server and sends the wrapper an empty refresh, so the change can only
arrive through the SDK's own fetch.

The treatment, variable and refresh expectations are the ones the suite already
pins for exp_test_ab and this uid (scenarios 29, 32 and 138). The payload holds
only the tested experiment: the server compresses any size, so a larger body
proves nothing more.

The group owns 270-274 (scenario_groups.CONTEXT_FETCH_FIRST_NUMBER), the free slot between the
2026-11 round groups (230-253) and the parity groups (300+) that
docs/sdk-round-2026-11-conformance.md reserves for it. It is rebuilt there in place, by number;
any other scenario numbered inside its range is a collision, never silently renumbered.

Usage:
    python3 generate_context_compression_scenarios.py          # rewrite the group in place
    python3 generate_context_compression_scenarios.py --check  # exit 1 if the file is stale
"""

import argparse
import copy
import json
import os
import sys

import scenario_groups

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")

GROUP_MARKER = scenario_groups.CONTEXT_FETCH
CAPABILITY = "contextFetchCompression"
BROTLI_CAPABILITY = "contextFetchBrotli"

# The payloads' server preferences (context-server/negotiation.py). Literal here so
# the generator stays importable without the context server's module path; a test
# pins them to negotiation.DEFAULT_PREFERENCE and negotiation.BR_PREFERENCE.
GZIP_ENCODINGS = ["gzip", "identity"]
BR_ENCODINGS = ["br", "gzip", "identity"]

UID = "e791e240fcd3df7d238cfc285f475e8152fcc0ec"


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


def context_data():
    return {"experiments": [tested_experiment()]}


def refreshed_data():
    """The refresh payload: traffic split [0.5, 0.5] takes this uid out of traffic (as scenario 29)."""
    data = context_data()
    data["experiments"][0]["trafficSplit"] = [0.5, 0.5]
    return data


def experiment_refs(data):
    return [{"id": e["id"], "name": e["name"]} for e in data["experiments"]]


def exposure(variant, eligible):
    return {"id": 1, "name": "exp_test_ab", "unit": "session_id", "variant": variant, "assigned": True,
            "eligible": eligible, "overridden": False, "fullOn": False, "custom": False,
            "audienceMismatch": False}


def create_step(action, data):
    return {
        "action": action,
        "params": {"units": {"session_id": UID}, "options": {"publishDelay": -1, "refreshPeriod": 0}},
        "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                   "events": [{"type": "ready", "data": {"experiments": experiment_refs(data)}}]},
    }


def treatment_step():
    return {"action": "treatment", "params": {"experimentName": "exp_test_ab"},
            "expect": {"result": 1, "events": [{"type": "exposure", "data": exposure(1, True)}]}}


def refreshed_treatment_step():
    """After the refresh: out of traffic, so variant 0 and a new, ineligible exposure."""
    return {"action": "treatment", "params": {"experimentName": "exp_test_ab"},
            "expect": {"result": 0, "events": [{"type": "exposure", "data": exposure(0, False)}]}}


def variable_step():
    return {"action": "variableValue", "params": {"key": "banner.size", "defaultValue": "small"},
            "expect": {"result": "large", "events": []}}


def refresh_step(data):
    # On a contextServer scenario the orchestrator stores newData on the context
    # server and sends the wrapper an empty refresh (the SDK must fetch it).
    return {"action": "refresh", "params": {"newData": copy.deepcopy(data)},
            "expect": {"result": None, "events": [{"type": "refresh", "data": {"experiments": experiment_refs(data)}}]}}


def publish_step():
    return {"action": "publish", "params": {},
            "expect": {"result": None, "events": [{"type": "publish", "data": {
                "exposures": [{"id": 1, "name": "exp_test_ab", "variant": 1}]}}]}}


def build_group(first_number):
    data = context_data()
    refreshed = refreshed_data()
    gzip_server = {"encodings": list(GZIP_ENCODINGS)}
    br_server = {"encodings": list(BR_ENCODINGS)}
    scenarios = [
        {
            "name": "Gzip When Advertised Matches Direct Data",
            "description": (
                "UNGATED (every SDK): the SDK's own GET {endpoint}/context is served by the negotiating "
                "context server, gzip only when the request advertised gzip and identity otherwise, with "
                "Content-Encoding and Vary: Accept-Encoding. The SDK must load the context and produce "
                "exactly the treatment, variable value, exposure and publish of the Direct Data Twin. Then "
                "the server's data changes (traffic split [0.5, 0.5], as scenario 29) and the SDK must "
                "refresh through its own fetch and serve the new data: treatment 0 with an ineligible "
                "exposure. Each fetch's served encoding is checked against the recorded Accept-Encoding."
            ),
            "contextServer": gzip_server,
            "contextData": data,
            "steps": [create_step("createContext", data), treatment_step(), variable_step(), publish_step(),
                      refresh_step(refreshed), refreshed_treatment_step()],
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
            "steps": [create_step("createContextWith", data), treatment_step(), variable_step(), publish_step()],
        },
        {
            "name": "Request Advertises Gzip On Create And Refresh",
            "description": (
                "GATED on contextFetchCompression (measured by the orchestrator, not reported by the "
                "wrapper): the recorded Accept-Encoding of the SDK's own GET {endpoint}/context on "
                "create AND on refresh advertises gzip, the server answers gzip, and both bodies decode: "
                "treatment 1 from the created data, then treatment 0 from the refreshed data."
            ),
            "requires": [CAPABILITY],
            "contextServer": dict(gzip_server, requireAdvertised=["gzip"]),
            "contextData": data,
            "steps": [create_step("createContext", data), treatment_step(), refresh_step(refreshed),
                      refreshed_treatment_step()],
        },
        {
            "name": "Brotli Offered Matches Direct Data",
            "description": (
                "UNGATED (every SDK): as Gzip When Advertised, but the payload allows br > gzip > "
                "identity (Cloudflare's order among them), so the context server answers br when the "
                "request advertised br, else gzip when it advertised gzip, else identity, honouring "
                "q-values (br;q=0 refuses br). Whatever the SDK negotiates, it must produce exactly the "
                "treatment, variable value, exposure and publish of the Direct Data Twin (271), then "
                "refresh through its own fetch and serve the new data (treatment 0 with an ineligible "
                "exposure). Offering br must never break an SDK. Each fetch's served encoding is "
                "checked against the recorded Accept-Encoding."
            ),
            "contextServer": br_server,
            "contextData": data,
            "steps": [create_step("createContext", data), treatment_step(), variable_step(), publish_step(),
                      refresh_step(refreshed), refreshed_treatment_step()],
        },
        {
            "name": "Request Advertises Brotli On Create And Refresh",
            "description": (
                "GATED on contextFetchBrotli (measured by the orchestrator, not reported by the "
                "wrapper): on a payload that allows br > gzip > identity, the recorded Accept-Encoding "
                "of the SDK's own GET {endpoint}/context on create AND on refresh advertises br, so the "
                "server answers br (checked against the shared negotiation), and both br bodies decode: "
                "treatment 1 from the created data, then treatment 0 from the refreshed data."
            ),
            "requires": [BROTLI_CAPABILITY],
            "contextServer": dict(br_server, requireAdvertised=["br"]),
            "contextData": data,
            "steps": [create_step("createContext", data), treatment_step(), refresh_step(refreshed),
                      refreshed_treatment_step()],
        },
    ]
    for offset, scenario in enumerate(scenarios):
        scenario["name"] = f"{first_number + offset}{GROUP_MARKER}{scenario['name']}"
    return scenarios


def render(scenarios):
    return json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    args = parser.parse_args()

    with open(CANONICAL, encoding="utf-8") as src:
        text = src.read()
    existing = json.loads(text)
    updated, group = scenario_groups.place_group(
        existing, (GROUP_MARKER,), build_group, first_number=scenario_groups.CONTEXT_FETCH_FIRST_NUMBER)
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
