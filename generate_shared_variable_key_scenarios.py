#!/usr/bin/env python3
"""Generate the Shared Variable Key group of test_scenarios_complete.json (340+).

When two or more experiments define the same variable key, variableValue / peekVariableValue must
pick the experiment the java-sdk reference picks (core-api Context.getVariableAssignment; the
javascript-sdk returns the same values but exposes every candidate it visits and falls through past
a winner whose variant lacks the key, so it is a reviewed exception):

  * candidates are the experiments whose variants define the key, visited in ascending experiment id
    (not payload order);
  * the first candidate that is assigned, overridden or ruleOverride wins, even if its selected
    variant does not define the key (the caller then gets the default; later candidates are NOT
    tried);
  * otherwise the first holdout-suppressed candidate wins (its control value);
  * otherwise there is no winner: the default is returned and no exposure is queued.

A candidate that is not assigned (a strict audience mismatch, no unit for its unit type, a custom
assignment on a strict mismatch, a switchback with no linked unit) must never leak its control
value. A switchback whose linked unit is present but has no cluster value is an ineligible cluster:
the reference records it as assigned (eligible=false), so it wins with control and is never exposed. That
is the dotnet-sdk 1.4.0 bug this group was written for: its fallback returned the first candidate's
assignment whenever nothing was assigned. A unit outside the traffic split IS assigned in the
reference (eligible=false, control variant), so it wins and is exposed.

Expected results are recorded from the java-sdk reference through its cross-sdk wrapper (the
java-wrapper image built from the sibling ../java-sdk checkout at JAVA_SDK_COMMIT), never written by
hand. The wrapper reports the java-sdk commit it was built from (`sdkCommit` on GET /health), and
recording refuses a wrapper built from any other commit or from a modified tree. Recording drives the wrapper exactly like the orchestrator (payload endpoint, createContext,
steps); exposure data drops exposedAt; ruleOverride is retained for rule probes and omitted
for ordinary probes (SDKs without rules omit the field), and ready/refresh events are projected to experiment id/name.

The recorded group is then replayed against an independent Python model of the rule (ReferenceModel)
and against wrong models (MUTANTS) of the bug class: generation fails unless the model reproduces
every recorded read and exposure and every mutant fails at least one scenario. The same replay runs
in CI without Docker (orchestrator/tests/test_shared_variable_key_scenarios.py).

Usage:
    docker compose up -d java-sdk          # java-wrapper built from ../java-sdk at JAVA_SDK_COMMIT
    python3 generate_shared_variable_key_scenarios.py --java-url http://localhost:<port>
    python3 generate_shared_variable_key_scenarios.py --java-url ... --check   # exit 1 if stale
    python3 generate_shared_variable_key_scenarios.py --replay                 # model + mutants only
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import uuid

import scenario_groups

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")
JAVA_SDK_PATH = os.environ.get("JAVA_SDK_PATH", os.path.abspath(os.path.join(HERE, "..", "java-sdk")))

# java-sdk main (switchback, holdouts, assignment rules), the branch sdk-branches.sh pins for java.
JAVA_SDK_COMMIT = "07807b516df5ad0d7d92e5b682041982802cd871"

GROUP_MARKER = scenario_groups.SHARED_VARIABLE_KEY
FIRST_NUMBER = scenario_groups.SHARED_VARIABLE_KEY_FIRST_NUMBER

CAPABILITY = "sharedVariableKeys"
HOLDOUTS = "holdouts"
SWITCHBACK = "switchback"
VARIABLE_KEYS_MAP = "variableKeysMap"

UNIT_TYPE = "session_id"
OTHER_UNIT_TYPE = "user_id"
UNITS = {UNIT_TYPE: "shared-key-unit-1"}
KEY = "homePage.searchBar.button.text"
DEFAULT = "caller-default"
EXPOSURE_KEYS = ("id", "name", "unit", "variant", "assigned", "eligible", "overridden", "fullOn",
                 "custom", "audienceMismatch")

AUDIENCE_US = json.dumps({"filter": [{"eq": [{"var": "Country"}, {"value": "US"}]}]},
                         separators=(",", ":"))


# ---------------------------------------------------------------------------
# Payload builders. Every assignment is deterministic without hashing: the split is one-hot on the
# last variant, traffic is all-in ([0, 1]) or all-out ([1, 0]), and a holdout's split is one-hot on
# arm 0 (the held-out arm). The replay model relies on that and refuses any other shape.
# ---------------------------------------------------------------------------

def experiment(exp_id, name, values, *, key=KEY, unit_type=UNIT_TYPE, in_traffic=True, strict=None,
               full_on=0, holdout_ids=None, extra_configs=None, **fields):
    """`values` holds one variable value per variant (None: the variant does not define the key;
    the string "<null>" defines it as JSON null). `strict` None means no audience; True/False adds
    the Country == US audience with that audienceStrict."""
    variants = []
    for i, value in enumerate(values):
        config = dict((extra_configs or {}).get(i, {}))
        if value == "<null>":
            config[key] = None
        elif value is not None:
            config[key] = value
        variants.append({"name": "ABCDE"[i], "config": json.dumps(config, separators=(",", ":")) if config else None})
    data = {
        "id": exp_id,
        "name": name,
        "iteration": 1,
        "unitType": unit_type,
        "seedHi": 0,
        "seedLo": 0,
        "split": [0.0] * (len(values) - 1) + [1.0],
        "trafficSeedHi": 0,
        "trafficSeedLo": 0,
        "trafficSplit": [0, 1] if in_traffic else [1, 0],
        "fullOnVariant": full_on,
        "applications": [{"name": "website"}],
        "variants": variants,
        "audience": AUDIENCE_US if strict is not None else None,
        "audienceStrict": bool(strict),
        "customFieldValues": None,
    }
    if holdout_ids is not None:
        data["holdoutIds"] = list(holdout_ids)
    data.update(fields)
    return data


def holdout(holdout_id, name):
    """A two-arm holdout that holds every unit out (split one-hot on arm 0, the held-out arm)."""
    return {
        "id": holdout_id, "name": name, "iteration": 1, "unitType": UNIT_TYPE, "seedHi": 0, "seedLo": 0,
        "split": [1.0, 0.0], "trafficSeedHi": 0, "trafficSeedLo": 0, "trafficSplit": [0, 1],
        "fullOnVariant": 0, "applications": [], "variants": [{"name": "A", "config": None},
                                                              {"name": "B", "config": None}],
        "audience": None, "audienceStrict": False, "customFieldValues": None, "holdoutType": "full",
    }


def switchback_fields(cluster_unit_type):
    """An R33 switchback clustered on `cluster_unit_type`'s store_zone attribute, which no scenario
    sets. With no unit of that type in the context the experiment does not apply (not assigned).
    With one, the cluster has no value and is ineligible: a silent control the reference records as
    assigned with eligible=false and never exposes."""
    return {
        "unitType": cluster_unit_type,
        "randomizationType": "switchback",
        "switchbackClusters": {cluster_unit_type: "store_zone"},
        "switchbackStartAt": 1_704_067_200_000,
        "switchbackPeriodMs": 1_440_000_000_000,
        "switchbackCarryoverDurationMs": 0,
        "switchbackBurnInDurationMs": 0,
        "switchbackBurnOutDurationMs": 0,
        "switchbackDesignType": "balanced_block",
        "switchbackEstimand": "global_average_treatment_effect",
    }


# step helpers (action, params)
def value(key=KEY, default=DEFAULT):
    return ("variableValue", {"key": key, "defaultValue": default})


def peek_value(key=KEY, default=DEFAULT):
    return ("peekVariableValue", {"key": key, "defaultValue": default})


def treatment(name):
    return ("treatment", {"experimentName": name})


def peek(name):
    return ("peek", {"experimentName": name})


def country(code):
    return ("attribute", {"name": "Country", "value": code})


def override(name, variant):
    return ("override", {"experimentName": name, "variant": variant})


def custom_assignment(name, variant):
    return ("customAssignment", {"experimentName": name, "variant": variant})


def refresh(experiments, holdouts=None):
    data = {"experiments": experiments}
    if holdouts:
        data["holdouts"] = holdouts
    return ("refresh", {"newData": data})


PENDING = ("pending", {})
VARIABLE_KEYS = ("variableKeys", {})
VARIABLE_KEYS_MAP_STEP = ("variableKeysMap", {})
READS = [peek_value(), value(), value(), PENDING]


def spec(title, description, experiments, steps, *, units=None, holdouts=None, requires=None):
    return {"title": title, "description": description, "experiments": experiments, "steps": steps,
            "units": units or UNITS, "holdouts": holdouts, "requires": requires}


# ---------------------------------------------------------------------------
# Scenario specs
# ---------------------------------------------------------------------------

def specs():
    out = []
    ie = country("IE")

    # a. The reported case: #175 strict-mismatched, the only candidate.
    strict_first = experiment(175, "exp_search_strict", ["Search food", "Find food"], strict=True)
    out.append(spec(
        "Strict Audience Mismatch On The Only Candidate Returns The Default",
        "The reported case: experiment 175 is the only experiment defining the key, has audienceStrict "
        "and the unit fails its audience (Country=IE). It is not assigned, so variableValue and "
        "peekVariableValue return the caller's default, not the control value, and no exposure is "
        "queued for the key. treatment() still exposes the mismatch (variant 0, assigned=false, "
        "audienceMismatch=true).",
        [strict_first],
        [ie] + READS + [treatment("exp_search_strict"), PENDING]))

    # b. Strict mismatch first, second assigned.
    assigned_second = experiment(176, "exp_search_assigned", ["Search", "Search now"])
    out.append(spec(
        "Strict Mismatch On The First Candidate - The Assigned Second Candidate Wins",
        "Experiment 175 (strict, mismatched) has the lower id; experiment 176 is assigned. The key "
        "resolves to 176's variant 1 value and the exposure goes to 176 only.",
        [strict_first, assigned_second],
        [ie] + READS))

    # c. Non-strict mismatch: still assigned.
    loose = experiment(175, "exp_search_loose", ["Search food", "Find food"], strict=False)
    out.append(spec(
        "Non-Strict Audience Mismatch Is Still Assigned And Serves Its Value",
        "With audienceStrict false a mismatched unit is still assigned: the key returns the assigned "
        "variant's value and the exposure has audienceMismatch=true.",
        [loose],
        [ie] + READS))

    # d. variableKeys lists both experiments.
    out.append(spec(
        "Variable Keys Still List Every Experiment Sharing The Key",
        "A strict mismatch on one candidate does not remove it from the key index: variableKeys lists "
        "the key and variableKeysMap lists both experiments. Listing keys queues nothing.",
        [strict_first, assigned_second],
        [ie, VARIABLE_KEYS, VARIABLE_KEYS_MAP_STEP, PENDING],
        requires=[VARIABLE_KEYS_MAP]))

    # e. Override on the strict-mismatched experiment wins.
    out.append(spec(
        "Override On The Strict-Mismatched Candidate Still Wins",
        "An override on experiment 175 (strict, mismatched) makes it the winner over the assigned "
        "176: the key returns 175's overridden variant 1 value and the exposure is overridden=true.",
        [strict_first, assigned_second],
        [ie, override("exp_search_strict", 1)] + READS))

    # 1. Traffic-excluded first candidate.
    excluded = experiment(180, "exp_traffic_excluded", ["excluded-control", "excluded-treatment"],
                          in_traffic=False)
    after_excluded = experiment(181, "exp_after_excluded", ["after-control", "after-treatment"])
    out.append(spec(
        "Traffic-Excluded Only Candidate Is Assigned And Serves Control",
        "A unit outside the traffic split is assigned in the reference (assigned=true, eligible=false, "
        "variant 0): the key returns the control value and variableValue exposes it with "
        "eligible=false.",
        [excluded],
        READS))
    out.append(spec(
        "Traffic-Excluded First Candidate Beats An Assigned Second Candidate",
        "The traffic-excluded experiment 180 is assigned (eligible=false), so it wins over 181 with "
        "its control value; 181 is never exposed.",
        [excluded, after_excluded],
        READS))

    # 2. No unit for the first candidate's unit type.
    no_unit = experiment(185, "exp_no_unit", ["no-unit-control", "no-unit-treatment"],
                         unit_type=OTHER_UNIT_TYPE)
    after_no_unit = experiment(186, "exp_after_no_unit", ["after-control", "after-treatment"])
    out.append(spec(
        "Missing Unit On The Only Candidate Returns The Default",
        "The only candidate's unit type (user_id) has no unit in the context, so it is not assigned: "
        "the key returns the default and no exposure is queued.",
        [no_unit],
        READS))
    out.append(spec(
        "Missing Unit On The First Candidate - The Assigned Second Candidate Wins",
        "Experiment 185 has no unit and is not assigned; 186 is assigned and wins.",
        [no_unit, after_no_unit],
        READS))

    # 3. Both candidates strict-mismatched.
    strict_second = experiment(176, "exp_search_strict_too", ["Search", "Search now"], strict=True)
    out.append(spec(
        "Both Candidates Strict-Mismatched Return The Default With No Exposure",
        "Experiments 175 and 176 are both strict and mismatched: variableValue and peekVariableValue "
        "return the default and nothing is queued.",
        [strict_first, strict_second],
        [ie] + READS))

    # 4. Candidates ordered by id, not payload order.
    low = experiment(190, "exp_low_id", ["low-control", "low-treatment"])
    high = experiment(191, "exp_high_id", ["high-control", "high-treatment"])
    out.append(spec(
        "Candidates Are Visited In Experiment Id Order, Not Payload Order",
        "Both experiments are assigned; the payload lists id 191 first. The lower id (190) wins.",
        [high, low],
        READS))

    # 5. First assigned candidate's variant lacks the key.
    lacks_key = experiment(195, "exp_variant_lacks_key", ["lacks-control", None],
                           extra_configs={1: {"other.key": "x"}})
    after_lacks = experiment(196, "exp_after_lacks_key", ["after-control", "after-treatment"])
    out.append(spec(
        "Assigned First Candidate Whose Variant Lacks The Key Returns The Default",
        "Experiment 195 defines the key only in variant 0 and the unit is assigned variant 1. It still "
        "wins: the key returns the default (the second candidate's value is NOT used) and the exposure "
        "goes to 195.",
        [lacks_key, after_lacks],
        READS))

    # 6. Non-strict mismatch first, second assigned.
    out.append(spec(
        "Non-Strict Mismatch On The First Candidate Still Wins Over The Second",
        "Experiment 175 is non-strict and mismatched, so it is assigned and wins over 176; the "
        "exposure has audienceMismatch=true.",
        [loose, assigned_second],
        [ie] + READS))

    # 7. Full-on first candidate.
    full_on = experiment(200, "exp_full_on", ["fo-control", "fo-full-on", "fo-split"], full_on=1)
    after_full_on = experiment(201, "exp_after_full_on", ["after-control", "after-treatment"])
    out.append(spec(
        "Full-On First Candidate Serves Its Full-On Value",
        "Experiment 200 is full-on variant 1 (its split would give variant 2): the full-on value wins "
        "over 201, exposed with fullOn=true.",
        [full_on, after_full_on],
        READS))

    # 8. Override to variant 0 on an unassigned first candidate.
    out.append(spec(
        "Override To Control On The Strict-Mismatched Candidate Wins",
        "An override to variant 0 on the strict-mismatched 175 wins over the assigned 176: the key "
        "returns 175's control value with overridden=true.",
        [strict_first, assigned_second],
        [ie, override("exp_search_strict", 0)] + READS))
    out.append(spec(
        "Override To Control On The Traffic-Excluded Candidate Wins",
        "An override to variant 0 on the traffic-excluded 180 wins over 181 with overridden=true.",
        [excluded, after_excluded],
        [override("exp_traffic_excluded", 0)] + READS))

    # 9. Custom assignment on a strict-mismatched candidate.
    out.append(spec(
        "Custom Assignment On A Strict-Mismatched Candidate Does Not Make It Win",
        "A custom assignment does not bypass a strict audience: 175 stays unassigned (variant 0), so "
        "the assigned 176 wins.",
        [strict_first, assigned_second],
        [ie, custom_assignment("exp_search_strict", 1)] + READS + [treatment("exp_search_strict"), PENDING]))

    # 10. Holdout-suppressed first candidate.
    covered = experiment(205, "exp_held_out", ["held-control", "held-treatment"], holdout_ids=[9])
    after_held = experiment(206, "exp_after_held_out", ["after-control", "after-treatment"])
    out.append(spec(
        "Holdout-Suppressed Only Candidate Serves Its Control Value",
        "Experiment 205 is held out by holdout 9 and nothing else is assigned: the key returns 205's "
        "control value (the reference's suppressed fallback). variableValue fires only the holdout's "
        "exposure; peekVariableValue queues nothing.",
        [covered],
        READS, holdouts=[holdout(9, "holdout_shared_key")], requires=[HOLDOUTS]))
    out.append(spec(
        "Holdout-Suppressed First Candidate - The Assigned Second Candidate Wins",
        "Experiment 205 is held out; 206 is assigned and wins. variableValue fires the holdout's "
        "exposure while visiting 205 and exposes 206.",
        [covered, after_held],
        READS, holdouts=[holdout(9, "holdout_shared_key")], requires=[HOLDOUTS]))

    # 11. Switchback candidates.
    sb_alone = experiment(210, "exp_sb_no_linked_unit", ["sb-control", "sb-treatment"],
                          key="sb.only.key", **switchback_fields("store_id"))
    sb_first = experiment(211, "exp_sb_no_linked_unit_first", ["sbf-control", "sbf-treatment"],
                          key="sb.shared.key", **switchback_fields("store_id"))
    sb_after = experiment(212, "exp_after_sb", ["after-control", "after-treatment"], key="sb.shared.key")
    out.append(spec(
        "A Switchback With No Linked Unit Returns The Default Or Yields To The Next",
        "A switchback whose only cluster unit type (store_id) has no unit in the context does not "
        "apply and is not assigned: on its own its key returns the default with no exposure; sharing "
        "a key with an assigned unit experiment, the unit experiment wins and is exposed.",
        [sb_alone, sb_first, sb_after],
        [peek_value("sb.only.key"), value("sb.only.key"), PENDING,
         peek_value("sb.shared.key"), value("sb.shared.key"), PENDING],
        requires=[SWITCHBACK]))
    sb_ineligible = experiment(208, "exp_sb_ineligible_cluster", ["sbi-control", "sbi-treatment"],
                               key="sb.shared.key", **switchback_fields(UNIT_TYPE))
    out.append(spec(
        "An Ineligible Switchback Cluster Wins Silently With Its Control Value",
        "A switchback (id 208) whose linked unit (session_id) is present but has no cluster value is "
        "an ineligible cluster: the reference records it as assigned with eligible=false and never "
        "exposes it. As the lowest id it wins the shared key over the unit experiment 212 with its "
        "control value, and nothing is queued.",
        [sb_ineligible, sb_after],
        [peek_value("sb.shared.key"), value("sb.shared.key"), PENDING,
         treatment("exp_sb_ineligible_cluster"), PENDING],
        requires=[SWITCHBACK]))

    # 12. Consistency across reads.
    out.append(spec(
        "Treatment, Variable Reads And Variable Keys Agree",
        "For the strict-mismatched 175 and the assigned 176: peek(175) is 0, the variable reads give "
        "176's value, variableValue queues exactly one exposure (176), repeated reads queue nothing, "
        "variableKeys lists the key, and treatment(175) then queues 175's own mismatch exposure.",
        [strict_first, assigned_second],
        [ie, peek("exp_search_strict"), peek_value(), value(), value(), peek_value(), PENDING,
         VARIABLE_KEYS, treatment("exp_search_strict"), treatment("exp_search_assigned"), PENDING]))

    # 13. Attribute flip re-evaluates the strict candidate.
    out.append(spec(
        "An Attribute Change That Matches The Strict Audience Flips The Winner",
        "With Country=IE the assigned 176 wins. Setting Country=US makes the strict 175 assigned: the "
        "key flips to 175's value and 175's exposure is queued once; repeated reads queue nothing.",
        [strict_first, assigned_second],
        [ie, value(), country("US"), peek_value(), value(), value(), PENDING]))

    # 14. Refresh removes the winning experiment.
    winner = experiment(215, "exp_refresh_winner", ["w-control", "w-treatment"],
                        extra_configs={0: {"solo.key": "solo-control"}, 1: {"solo.key": "solo-treatment"}})
    runner_up = experiment(216, "exp_refresh_runner_up", ["r-control", "r-treatment"])
    out.append(spec(
        "Refresh That Removes The Winner Falls To The Next Candidate Or The Default",
        "215 wins the shared key and is the only experiment with solo.key. A refresh that removes 215 "
        "makes the shared key resolve to 216 (exposed) and solo.key return the default.",
        [winner, runner_up],
        [value(), value("solo.key"), refresh([runner_up]), peek_value(), value(), value("solo.key"),
         PENDING]))

    # 15. A variable defined as JSON null.
    null_value = experiment(220, "exp_null_value", ["null-control", "<null>"])
    after_null = experiment(221, "exp_after_null", ["after-control", "after-treatment"])
    out.append(spec(
        "A Variant Defining The Key As JSON Null Returns Null",
        "The winning experiment 220's variant 1 defines the key as JSON null: the reference returns "
        "null (the key is present), not the default and not the next candidate's value.",
        [null_value, after_null],
        READS))
    rule_first = experiment(230, "exp_rule_first", ["rule-control", "rule-treatment"], strict=True,
                            assignmentRules=json.dumps({"rules": [{"name": "shared-key-rule",
                                "type": "assign", "variant": 1}]}, separators=(",", ":")))
    rule_second = experiment(231, "exp_after_rule", ["after-control", "after-treatment"])
    out.append(spec(
        "Rule-Won First Candidate Beats An Assigned Second Candidate",
        "The lower-id experiment 230 has a matching assignment rule and a strict audience that "
        "Country=IE fails. The rule wins (assigned=false, ruleOverride=true), so both variable "
        "reads return its rule-treatment value, not the later assigned experiment's value. Only "
        "the rule-won experiment is exposed, once; peek queues nothing.",
        [rule_first, rule_second],
        [ie] + READS, requires=["assignmentRules"]))
    # A full-on experiment covered by a holdout on arm 0 is still held out.
    held_full_on = experiment(234, "exp_held_out_full_on", ["hfo-control", "hfo-full-on", "hfo-split"],
                              key="held.full.on.key", full_on=1, holdout_ids=[9])
    out.append(spec(
        "A Full-On Candidate Held Out By Holdout Arm 0 Serves Control",
        "Experiment 234 is full-on variant 1 and covered by holdout 9, whose arm 0 holds the unit "
        "out. Arm 0 holds out every covered experiment, full-on or not, so the key returns 234's "
        "control value, not its full-on value. variableValue fires only the holdout's exposure.",
        [held_full_on],
        [peek_value("held.full.on.key"), value("held.full.on.key"), value("held.full.on.key"), PENDING],
        holdouts=[holdout(9, "holdout_shared_key")], requires=[HOLDOUTS]))

    # Two held-out candidates, the higher id first in the payload: the lower id's control wins.
    held_low = experiment(232, "exp_held_out_low", ["held-low-control", "held-low-treatment"],
                          key="held.shared.key", holdout_ids=[9])
    held_high = experiment(233, "exp_held_out_high", ["held-high-control", "held-high-treatment"],
                           key="held.shared.key", holdout_ids=[9])
    out.append(spec(
        "Two Holdout-Suppressed Candidates - The Lower Id Serves Its Control Value",
        "Experiments 232 and 233 are both held out and nothing else is assigned; the payload lists "
        "233 first. The key returns 232's control value (the first suppressed candidate by id, not "
        "the last and not the first in the payload). variableValue fires only the holdout's "
        "exposure; peekVariableValue queues nothing.",
        [held_high, held_low],
        [peek_value("held.shared.key"), value("held.shared.key"), value("held.shared.key"), PENDING],
        holdouts=[holdout(9, "holdout_shared_key")], requires=[HOLDOUTS]))
    return out


# ---------------------------------------------------------------------------
# Recording from the java-sdk reference (through its wrapper)
# ---------------------------------------------------------------------------

def ensure_reference_checkout():
    head = subprocess.run(["git", "-C", JAVA_SDK_PATH, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False).stdout.strip()
    if head != JAVA_SDK_COMMIT:
        raise SystemExit(f"java-sdk checkout {JAVA_SDK_PATH} is at {head or '?'}, not {JAVA_SDK_COMMIT}; "
                         "check it out and rebuild the java-wrapper image from it")
    status = subprocess.run(["git", "-C", JAVA_SDK_PATH, "status", "--porcelain"],
                            capture_output=True, text=True, check=False)
    if status.returncode != 0:
        raise SystemExit(f"git status failed in {JAVA_SDK_PATH}: {status.stderr.strip()}")
    dirty = status.stdout.strip()
    if dirty:
        raise SystemExit(f"java-sdk checkout has uncommitted changes:\n{dirty}")


def ensure_reference_wrapper(base_url):
    """The running wrapper must have been built from JAVA_SDK_COMMIT, from a clean tree: a clean
    checkout alone does not prove which revision the wrapper at --java-url runs."""
    import requests

    health = requests.get(f"{base_url}/health", timeout=5)
    health.raise_for_status()
    built = health.json().get("sdkCommit")
    if built != JAVA_SDK_COMMIT:
        raise SystemExit(f"java-wrapper at {base_url} reports java-sdk {built or 'unknown'}, not "
                         f"{JAVA_SDK_COMMIT}; rebuild it from the pinned checkout")


def project_event(event, include_rule_override=False):
    kind = event.get("type")
    data = event.get("data") or {}
    if kind == "exposure":
        keys = EXPOSURE_KEYS + (("ruleOverride",) if include_rule_override else ())
        return {"type": "exposure", "data": {k: data[k] for k in keys}}
    if kind in ("ready", "refresh"):
        return {"type": kind, "data": {"experiments": [{"id": e["id"], "name": e["name"]}
                                                       for e in data.get("experiments", [])]}}
    raise SystemExit(f"unexpected {kind} event from the reference")


def record(s, base_url):
    import requests

    payload = f"{base_url}/context_payload/svk-{uuid.uuid4()}"
    data = {"experiments": s["experiments"]}
    if s["holdouts"]:
        data["holdouts"] = s["holdouts"]
    requests.put(payload, json={"data": data}, timeout=5).raise_for_status()
    create_params = {"units": s["units"], "options": {"publishDelay": -1, "refreshPeriod": 0}}
    response = requests.post(f"{base_url}/context", json={"endpoint": payload, **create_params}, timeout=10)
    response.raise_for_status()
    body = response.json()
    context_id = body["result"]["contextId"]
    ready = {k: body["result"][k] for k in ("ready", "failed", "finalized")}
    if ready != {"ready": True, "failed": False, "finalized": False}:
        raise SystemExit(f"{s['title']}: reference context not ready: {ready}")
    steps = [{"action": "createContextWith", "params": create_params,
              "expect": {"result": ready, "events": [project_event(e) for e in body["events"]]}}]
    for action, params in s["steps"]:
        if action == "refresh":
            requests.put(payload, json={"data": params["newData"]}, timeout=5).raise_for_status()
            response = requests.post(f"{base_url}/context/{context_id}/refresh", json=params, timeout=10)
        elif action == "pending":
            response = requests.get(f"{base_url}/context/{context_id}/pending", timeout=5)
        else:
            response = requests.post(f"{base_url}/context/{context_id}/{action}", json=params, timeout=5)
        response.raise_for_status()
        got = response.json()
        steps.append({"action": action, "params": params,
                      "expect": {"result": got["result"], "events": [project_event(e, "assignmentRules" in (s["requires"] or [])) for e in got["events"]]}})
    return steps


def build_group(base_url):
    ensure_reference_checkout()
    ensure_reference_wrapper(base_url)
    group = []
    for i, s in enumerate(specs()):
        context_data = {"experiments": s["experiments"]}
        if s["holdouts"]:
            context_data["holdouts"] = s["holdouts"]
        scenario = {
            "name": f"{FIRST_NUMBER + i}{GROUP_MARKER}{s['title']}",
            "description": f"{s['description']} Expected results recorded from java-sdk {JAVA_SDK_COMMIT[:8]} "
                           "(core-api Context.getVariableAssignment).",
            "requires": [CAPABILITY] + (s["requires"] or []),
            "contextData": context_data,
            "steps": record(s, base_url),
        }
        group.append(scenario)
    titles = [s["name"].split(GROUP_MARKER, 1)[1] for s in group]
    if len(set(titles)) != len(titles):
        raise SystemExit("duplicate scenario titles")
    failures = replay_failures(group)
    if failures:
        raise SystemExit("the recorded group does not match the replay model or a mutant survived:\n  - "
                         + "\n  - ".join(failures))
    return group


# ---------------------------------------------------------------------------
# Independent replay model and mutants
# ---------------------------------------------------------------------------

MUTANTS = (
    "ignore_rule_override",        # shared-key selection omits the RuleOverride win condition
    "first_candidate_fallback",    # dotnet-sdk 1.4.0: no winner -> the first candidate's assignment
    "payload_order",               # candidates in payload order instead of ascending id
    "skip_variant_without_key",    # an assigned candidate whose variant lacks the key falls through
    "strict_mismatch_assigned",    # a strict audience mismatch counts as assigned
    "traffic_excluded_unassigned", # a unit outside the traffic split counts as not assigned
    "override_needs_assignment",   # an override only wins if the experiment is otherwise assigned
    "custom_beats_strict",         # a custom assignment bypasses a strict audience
    "full_on_ignored",             # fullOnVariant ignored (the split variant is served)
    "no_suppressed_fallback",      # a held-out candidate never serves control
    "suppressed_beats_assigned",   # a held-out candidate wins over a later assigned one
    "last_suppressed",             # the last held-out candidate serves control, not the first
    "full_on_escapes_holdout",     # a full-on experiment is not held out by holdout arm 0
    "switchback_as_unit",          # a switchback is resolved like a unit experiment
    "null_is_default",             # a JSON null variable value returns the default
    "peek_exposes",                # peekVariableValue queues an exposure
    "no_reevaluation",             # setAttribute does not re-evaluate a cached strict mismatch
    "stale_after_refresh",         # a refresh keeps serving an experiment it removed
)


class ReferenceModel:
    """The reference rule over the deterministic fixtures this generator emits. Models the variable
    reads, treatment/peek results and which candidate experiment each read exposes; holdout exposures
    are filtered out of the comparison (they are pinned by the recording)."""

    def __init__(self, scenario, mutant=None):
        self.mutant = mutant
        self.units = dict(scenario["steps"][0]["params"]["units"])
        self.attributes = {}
        self.overrides = {}
        self.custom = {}
        self.cache = {}       # experiment name -> (state, exposed)
        self.set_data(scenario["contextData"])

    def set_data(self, data):
        if self.mutant == "stale_after_refresh" and hasattr(self, "experiments"):
            merged = {e["name"]: e for e in self.experiments}
            merged.update({e["name"]: e for e in data["experiments"]})
            experiments = list(merged.values())
        else:
            experiments = data["experiments"]
        self.experiments = experiments
        self.holdouts = {h["id"]: h for h in data.get("holdouts") or []}
        by_name = {e["name"]: e for e in experiments}
        self.cache = {n: c for n, c in self.cache.items() if n in by_name and c[2] == json.dumps(by_name[n])}

    def variables(self, exp):
        out = []
        for v in exp["variants"]:
            out.append(json.loads(v["config"]) if v["config"] else {})
        return out

    @staticmethod
    def check_shape(exp):
        n = len(exp["variants"])
        if exp["split"] != [0.0] * (n - 1) + [1.0] or exp["trafficSplit"] not in ([0, 1], [1, 0]):
            raise ValueError(f"{exp['name']}: not a deterministic fixture")

    def state(self, exp):
        """(variant, assigned, eligible, overridden, fullOn, custom, audienceMismatch, suppressed,
        exposes) for the current context."""
        self.check_shape(exp)
        m = self.mutant
        name = exp["name"]
        n = len(exp["variants"])
        mismatch = False
        if exp["audience"]:
            mismatch = self.attributes.get("Country") != "US"
        if name in self.overrides:
            return (self.overrides[name], False, True, True, False, False, False, False, True)
        if exp.get("assignmentRules"):
            rules = json.loads(exp["assignmentRules"])["rules"]
            if (len(rules) != 1 or rules[0].get("type") != "assign"
                    or set(rules[0]) - {"name", "type", "variant"}):
                raise ValueError(f"{name}: only a single unconditional assign rule is modelled")
            # Like an explicit override, a rule win is not an ordinary assignment.
            return (rules[0]["variant"], False, True, False, False, False, False, False, True)
        switchback = exp.get("randomizationType") == "switchback"
        if switchback and m != "switchback_as_unit":
            # The fixtures never set the cluster attribute: with no linked unit the switchback does not
            # apply; with one the cluster is ineligible, assigned but silent (never exposed).
            linked = next(iter(exp["switchbackClusters"]))
            return (0, linked in self.units, False, False, False, False, False, False, False)
        suppressed = False
        for holdout_id in exp.get("holdoutIds") or []:
            holdout = self.holdouts.get(holdout_id)
            if holdout is None:
                continue
            if holdout["split"] != [1.0, 0.0]:
                raise ValueError(f"{name}: only a two-arm holdout on arm 0 is modelled")
            # java-sdk isHeldOutBy: arm 0 holds out every covered experiment, full-on or not.
            if m != "full_on_escapes_holdout" or exp["fullOnVariant"] == 0:
                suppressed = True
        if suppressed:
            return (0, False, True, False, False, False, mismatch, True, False)
        if exp["audienceStrict"] and mismatch and m != "strict_mismatch_assigned":
            if m == "custom_beats_strict" and name in self.custom:
                return (self.custom[name], True, True, False, False, True, True, False, True)
            return (0, False, True, False, False, False, True, False, True)
        if exp["fullOnVariant"] != 0 and m != "full_on_ignored":
            return (exp["fullOnVariant"], True, True, False, True, False, mismatch, False, True)
        if exp["unitType"] not in self.units:
            return (0, False, True, False, False, False, mismatch, False, True)
        if exp["trafficSplit"] == [1, 0]:
            return (0, m != "traffic_excluded_unassigned", False, False, False, False, mismatch, False, True)
        if name in self.custom:
            return (self.custom[name], True, True, False, False, True, mismatch, False, True)
        return (n - 1, True, True, False, False, False, mismatch, False, True)

    def assignment(self, exp):
        name = exp["name"]
        current = self.state(exp)
        cached = self.cache.get(name)
        if cached is not None and (cached[0] == current or self.mutant == "no_reevaluation"):
            return cached
        entry = [current, False, json.dumps(exp)]
        self.cache[name] = entry
        return entry

    def candidates(self, key):
        cands = [e for e in self.experiments if any(key in v for v in self.variables(e))]
        if self.mutant == "payload_order":
            return cands
        return sorted(cands, key=lambda e: e["id"])

    def winner(self, key):
        suppressed = None
        cands = self.candidates(key)
        for exp in cands:
            entry = self.assignment(exp)
            variant, assigned, _, overridden, *_rest = entry[0]
            is_suppressed = entry[0][7]
            if self.mutant == "override_needs_assignment" and overridden:
                overridden = False
                assigned = self.state_without_override(exp)
            rule_override = bool(exp.get("assignmentRules")) and not overridden
            if assigned or overridden or (rule_override and self.mutant != "ignore_rule_override"):
                if self.mutant == "skip_variant_without_key" and key not in self.variables(exp)[variant]:
                    continue
                return exp, entry
            if is_suppressed:
                if self.mutant == "suppressed_beats_assigned":
                    return exp, entry
                if suppressed is None or self.mutant == "last_suppressed":
                    suppressed = (exp, entry)
        if suppressed is not None and self.mutant != "no_suppressed_fallback":
            return suppressed
        if self.mutant == "first_candidate_fallback" and cands:
            return cands[0], self.assignment(cands[0])
        return None, None

    def state_without_override(self, exp):
        saved = self.overrides.pop(exp["name"])
        try:
            return self.state(exp)[1]
        finally:
            self.overrides[exp["name"]] = saved

    def read(self, key, default, expose):
        exp, entry = self.winner(key)
        exposed = []
        if exp is None:
            return default, exposed
        if (expose or self.mutant == "peek_exposes") and not entry[1] and entry[0][8]:
            entry[1] = True
            exposed.append(exp["name"])
        variables = self.variables(exp)[entry[0][0]] if entry[0][0] < len(exp["variants"]) else {}
        if key not in variables:
            return default, exposed
        if variables[key] is None and self.mutant == "null_is_default":
            return default, exposed
        return variables[key], exposed

    def step(self, action, params):
        """(result, [exposed candidate names]) for the actions this group uses; None for actions the
        model does not compare (their recording is pinned by the digest)."""
        if action == "attribute":
            self.attributes[params["name"]] = params["value"]
            return None
        if action == "override":
            self.overrides[params["experimentName"]] = params["variant"]
            return None
        if action == "customAssignment":
            self.custom[params["experimentName"]] = params["variant"]
            return None
        if action == "refresh":
            self.set_data(params["newData"])
            return None
        if action in ("variableValue", "peekVariableValue"):
            return self.read(params["key"], params["defaultValue"], action == "variableValue")
        if action in ("treatment", "peek"):
            exp = next(e for e in self.experiments if e["name"] == params["experimentName"])
            entry = self.assignment(exp)
            exposed = []
            if action == "treatment" and not entry[1] and entry[0][8]:
                entry[1] = True
                exposed.append(exp["name"])
            return entry[0][0], exposed
        return None


def observed(scenario, mutant=None):
    model = ReferenceModel(scenario, mutant)
    out = []
    for index, step in enumerate(scenario["steps"][1:], start=1):
        got = model.step(step["action"], step["params"])
        if got is not None:
            out.append((index, got[0], got[1]))
    return out


def recorded(scenario):
    holdout_names = {h["name"] for h in scenario["contextData"].get("holdouts") or []}
    out = []
    for index, step in enumerate(scenario["steps"][1:], start=1):
        if step["action"] in ("variableValue", "peekVariableValue", "treatment", "peek"):
            names = [e["data"]["name"] for e in step["expect"]["events"]
                     if e["type"] == "exposure" and e["data"]["name"] not in holdout_names]
            out.append((index, step["expect"]["result"], names))
    return out


def replay_failures(group):
    failures = []
    for scenario in group:
        if observed(scenario) != recorded(scenario):
            failures.append(f"model disagrees with the recording: {scenario['name']}")
    for mutant in MUTANTS:
        if all(observed(s, mutant) == recorded(s) for s in group):
            failures.append(f"mutant {mutant} survives every scenario")
    return failures


def killed_by(group):
    return {m: [s["name"] for s in group if observed(s, m) != recorded(s)] for m in MUTANTS}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble(existing, build):
    """Place the group at FIRST_NUMBER.. (after the parity groups, before the Switchback group)."""
    return scenario_groups.place_group(existing, (GROUP_MARKER,), lambda n: build(),
                                       first_number=FIRST_NUMBER)


def group_of(scenarios):
    return [s for s in scenarios if GROUP_MARKER in s["name"]]


def main():
    parser = argparse.ArgumentParser(description="Generate the Shared Variable Key scenario group.")
    parser.add_argument("--java-url", default=os.environ.get("JAVA_WRAPPER_URL"),
                        help="base URL of a running java-wrapper built from JAVA_SDK_COMMIT")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    parser.add_argument("--replay", action="store_true",
                        help="only replay the checked-in group against the model and mutants")
    parser.add_argument("--file", default=CANONICAL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as src:
        raw = src.read()
    existing = json.loads(raw)

    if args.replay:
        group = group_of(existing)
        failures = replay_failures(group)
        for mutant, names in killed_by(group).items():
            print(f"{mutant}: killed by {len(names)} scenario(s)")
        if failures:
            print("\n".join(failures))
            return 1
        print(f"model reproduces {len(group)} scenarios; every mutant is killed")
        return 0

    if not args.java_url:
        parser.error("--java-url (or JAVA_WRAPPER_URL) is required to record from the reference")
    updated, group = assemble(existing, lambda: build_group(args.java_url.rstrip("/")))
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if rendered != raw:
            print("test_scenarios_complete.json is stale: run generate_shared_variable_key_scenarios.py")
            return 1
        print(f"up to date: {len(group)} shared variable key scenarios")
        return 0

    with open(args.file, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(group)} shared variable key)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
