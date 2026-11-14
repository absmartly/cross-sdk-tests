#!/usr/bin/env python3
"""Generate the 2026-11 feature-parity scenario groups of test_scenarios_complete.json (300+).

The feature-parity round ports three javascript-sdk features to every SDK (each SDK's
`feat/assignment-rules-semver-x-agent-publish` branch; java-sdk#22 and the javascript-sdk#68
reference already carry the first two):

  * Assignment rules (Experiment.assignmentRules). The 2026-11 round group (230-239) pins the
    basics; this file adds what it does not cover: a split rule hashes the unit with the
    experiment's own seedHi/seedLo (pinned per unit), environment scoping (listed / unlisted /
    empty list / no environment configured), first match wins, a false condition falls through,
    precedence over holdout suppression, traffic split and full-on, the missing-unit split
    (no-unit result, re-resolved once the unit is set unless already exposed), malformed splits
    skipped for the next rule, wrong-count split and negative assign variant stopping evaluation,
    the 0.01 percentage-sum tolerance boundary, the no-repeated-exposure fix for a rule beating a
    differing custom assignment (javascript-sdk#66), and a semver_* operator inside a condition.
  * Semver operators: each operator both ways, pre-release ordering (the semver.org precedence
    chain), build metadata ignored, and invalid / missing versions evaluating the way JS does.
  * X-Agent: the context fetch and publish requests carry `absmartly-<lang>-sdk/<version>`.
    That group is not computed by the JS reference: javascript-sdk#68 sends the bare
    `absmartly-javascript-sdk`, without a version, so the expectation is the brief's contract
    (a regex) and the JS-family lanes list `xAgentHeader` as a reviewed exception.

Every rules/semver expectation is recorded by running the steps through the javascript-sdk
reference (tools/sdk-round-2026-11/compute.js, the pinned build of the round generator), not
written by hand. Units and seeds were chosen with the reference's own VariantAssigner so that each
split-rule case lands on a variant that differs from what the traffic seed, swapped seeds, zero
seeds or the experiment's normal split would give — a wrong hash cannot pass by accident.

Usage:
    python3 generate_parity_round_scenarios.py          # rewrite the groups in place
    python3 generate_parity_round_scenarios.py --check  # exit 1 if the canonical file is stale
"""

import argparse
import json
import os
import subprocess
import sys

import generate_sdk_round_2026_11_scenarios as round2611
from generate_sdk_round_2026_11_scenarios import (
    PENDING, SYSATTR_ENV, attribute, audience_filter, custom_assignment, experiment, override, peek,
    rules, treatment, variable_value,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")

# First id of the parity groups. Ids in use when this was written: main up to 229, #17 up to 247,
# #19 up to 253, #20 up to 232, #13 up to 271 and unopened branches up to 286, so 300+ is free.
FIRST_NUMBER = 300

RULES_MARKER = " - Assignment Rules - "
SEMVER_MARKER = " - Semver Comparison - "
XAGENT_MARKER = " - X-Agent - "
CLIENT_APP_MARKER = " - Client Application - "
PARITY_MARKERS = (RULES_MARKER, SEMVER_MARKER, XAGENT_MARKER, CLIENT_APP_MARKER)

RULES_CAPABILITY = "assignmentRules"
SEMVER_CAPABILITY = "semverOperators"
HOLDOUTS_CAPABILITY = "holdouts"
NO_ENVIRONMENT_CAPABILITY = "clientNoEnvironment"
XAGENT_CAPABILITY = "xAgentHeader"
REJECTS_BLANK_APP_CAPABILITY = "clientRejectsBlankApplication"
ACCEPTS_BLANK_APP_CAPABILITY = "clientAcceptsBlankApplication"
# Each rejecting SDK's own client-construction message for a blank application.
BLANK_APPLICATION_ERRORS = [
    "Invalid 'application' in options argument",
    "Missing Application configuration",
    "Missing application configuration",
    "application cannot be empty",
    "application must not be empty",
]

UNIT = {"session_id": "svk-unit-1"}

# Seeds for the split-rule cases (see the module docstring for how they were chosen).
SEED = (24301, 5003)
TRAFFIC_SEED = (2827, 9011)
# With SEED and the rule split 20/30/50 these units land on variants 0, 1 and 2.
SPLIT_UNITS = (("split-unit-21", [0.1, 0.45, 0.45]),
               ("split-unit-12", [0.6, 0.2, 0.2]),
               ("split-unit-24", [0.6, 0.2, 0.2]))
# Lands on variant 1 of a 50/50 (and 50/50.014) split with SEED; variant 0 of the same split with
# zero seeds.
LATE_UNIT = "late-unit-1"

# The X-Agent contract from the brief: absmartly-<lang>-sdk/<semver>. The version is the semver.org
# 2.0.0 grammar (no leading zeroes, no empty identifiers). {lang} is replaced by the orchestrator with
# the language of the SDK under test, so the header must also name that SDK.
_NUM = r"(?:0|[1-9][0-9]*)"
_PRE_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD_ID = r"[0-9A-Za-z-]+"
XAGENT_PATTERN = (rf"^absmartly-{{lang}}-sdk/{_NUM}\.{_NUM}\.{_NUM}"
                  rf"(?:-{_PRE_ID}(?:\.{_PRE_ID})*)?(?:\+{_BUILD_ID}(?:\.{_BUILD_ID})*)?$")


def seeded(exp, *, seed=SEED, traffic_seed=TRAFFIC_SEED, unit_type=None, full_on=None):
    exp = dict(exp)
    exp["seedHi"], exp["seedLo"] = seed
    exp["trafficSeedHi"], exp["trafficSeedLo"] = traffic_seed
    if unit_type is not None:
        exp["unitType"] = unit_type
    if full_on is not None:
        exp["fullOnVariant"] = full_on
    return exp


def set_unit(unit_type, uid):
    return ("setUnit", {"unitType": unit_type, "uid": uid})


def spec(title, description, experiments, steps, *, units=None, client=None, requires=None,
         holdouts=None):
    return {"title": title, "description": description, "experiments": experiments,
            "steps": steps, "units": units or UNIT, "client": client, "requires": requires,
            "holdouts": holdouts}


# ---------------------------------------------------------------------------
# Assignment rules
# ---------------------------------------------------------------------------

def rules_specs():
    specs = []
    us = {"eq": [{"var": "country"}, {"value": "US"}]}

    split_rule = rules({"name": "split", "type": "split", "percentages": "20/30/50"})
    exps, units, steps = [], {}, []
    for i, (uid, normal_split) in enumerate(SPLIT_UNITS):
        unit_type = f"split_u{i}"
        name = f"exp_rules_split_seed_{i}"
        exps.append(seeded(experiment(400 + i, name, variants=3, split=normal_split,
                                      assignment_rules=split_rule), unit_type=unit_type))
        units[unit_type] = uid
        steps.append(treatment(name))
    steps.append(PENDING)
    specs.append(spec(
        "Split Rule Hashes Each Unit With The Experiment Seed",
        "A 20/30/50 split rule assigns three units (one per unit type) to variants 0, 1 and 2. The "
        "units are pinned so that each variant differs from what the traffic seed, swapped seeds, "
        "zero seeds or the experiment's own split would give: only hashing the unit with the "
        "experiment's seedHi/seedLo over the rule's percentages passes. Each exposure is "
        "ruleOverride:true, assigned:false, eligible:true.",
        exps, steps, units=units, requires=[RULES_CAPABILITY]))

    env_listed = rules({"name": "listed", "type": "assign", "variant": 0,
                        "environments": ["staging", "parity-env"]})
    env_unlisted = rules({"name": "unlisted", "type": "assign", "variant": 0,
                          "environments": ["staging", "production"]})
    env_empty = rules({"name": "everywhere", "type": "assign", "variant": 0, "environments": []})
    env_client = {"application": "parity-app", "environment": "parity-env"}
    specs.append(spec(
        "Environment Scoping Matches Listed, Skips Unlisted, Empty List Matches Everywhere",
        "With the client environment 'parity-env': a rule listing it matches (variant 0, "
        "ruleOverride), a rule listing only other environments is skipped (normal assignment, "
        "variant 1), and a rule with an empty environments list matches every environment.",
        [experiment(410, "exp_rules_env_listed", assignment_rules=env_listed),
         experiment(411, "exp_rules_env_unlisted", assignment_rules=env_unlisted),
         experiment(412, "exp_rules_env_empty", assignment_rules=env_empty)],
        [treatment("exp_rules_env_listed"), treatment("exp_rules_env_unlisted"),
         treatment("exp_rules_env_empty"), PENDING],
        client=env_client, requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "No Environment Configured Skips Environment-Scoped Rules Only",
        "With no client environment, a rule scoped to environments is skipped (normal assignment), "
        "while a rule with an empty environments list and a rule with no environments key still "
        "match.",
        [experiment(413, "exp_rules_noenv_scoped", assignment_rules=env_listed),
         experiment(414, "exp_rules_noenv_empty", assignment_rules=env_empty),
         experiment(415, "exp_rules_noenv_absent",
                    assignment_rules=rules({"name": "absent", "type": "assign", "variant": 0}))],
        [treatment("exp_rules_noenv_scoped"), treatment("exp_rules_noenv_empty"),
         treatment("exp_rules_noenv_absent"), PENDING],
        client={"application": "parity-app"},
        requires=[RULES_CAPABILITY, NO_ENVIRONMENT_CAPABILITY]))

    specs.append(spec(
        "First Matching Rule Wins",
        "Two rules both match; the first one's variant (2) wins over the second's (1).",
        [experiment(420, "exp_rules_first", variants=3, assignment_rules=rules(
            {"name": "first", "type": "assign", "variant": 2, "conditions": us},
            {"name": "second", "type": "assign", "variant": 1}))],
        [attribute("country", "US"), treatment("exp_rules_first"), PENDING],
        requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "False Condition Falls Through To The Next Rule",
        "The first rule's condition is false (country is CA), so evaluation continues and the "
        "second rule assigns variant 0 with ruleOverride:true.",
        [experiment(421, "exp_rules_fallthrough", variants=3, assignment_rules=rules(
            {"name": "us", "type": "assign", "variant": 1, "conditions": us},
            {"name": "rest", "type": "assign", "variant": 0}))],
        [attribute("country", "CA"), treatment("exp_rules_fallthrough"), PENDING],
        requires=[RULES_CAPABILITY]))

    holdout = dict(seeded(experiment(9001, "parity_holdout", split=[1.0, 0.0])),
                   holdoutType="full")
    holdout["applications"] = []
    del holdout["assignmentRules"]
    covered = experiment(422, "exp_rules_holdout", assignment_rules=rules(
        {"name": "a", "type": "assign", "variant": 1}))
    covered["holdoutIds"] = [9001]
    plain = experiment(423, "exp_rules_holdout_plain")
    plain["holdoutIds"] = [9001]
    specs.append(spec(
        "Rule Wins Over Holdout Suppression",
        "Both experiments are covered by a holdout whose split holds every unit out. The one with "
        "a matching rule still serves the rule variant (1) with a ruleOverride exposure; the one "
        "without a rule is suppressed to variant 0.",
        [covered, plain],
        [treatment("exp_rules_holdout"), treatment("exp_rules_holdout_plain"), PENDING],
        holdouts=[holdout], requires=[RULES_CAPABILITY, HOLDOUTS_CAPABILITY]))

    assign_1 = rules({"name": "a", "type": "assign", "variant": 1})
    specs.append(spec(
        "Rule Wins Over Traffic Split And Full-On",
        "A unit outside the traffic split (trafficSplit [1, 0]) still gets the rule variant 1 "
        "(eligible:true, ruleOverride); without the rule it gets 0 with eligible:false. A rule "
        "assigning variant 1 also beats fullOnVariant 2 (fullOn:false).",
        [experiment(430, "exp_rules_traffic", traffic_eligible=False, assignment_rules=assign_1),
         experiment(431, "exp_rules_traffic_plain", traffic_eligible=False),
         dict(experiment(432, "exp_rules_fullon", variants=3, assignment_rules=assign_1),
              fullOnVariant=2)],
        [treatment("exp_rules_traffic"), treatment("exp_rules_traffic_plain"),
         treatment("exp_rules_fullon"), PENDING],
        requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "Rule Beating A Different Custom Assignment Exposes Once",
        "javascript-sdk#66 fix: a rule assigns variant 1 although customAssignment(2) is set. "
        "Repeated treatment() and variableValue() calls keep returning the rule variant and queue "
        "exactly one exposure, instead of treating the custom mismatch as a stale assignment and "
        "re-exposing on every call.",
        [experiment(433, "exp_rules_custom_once", variants=3, assignment_rules=assign_1,
                    configs=[json.dumps({"k": v}, separators=(",", ":")) for v in ("a", "b", "c")])],
        [custom_assignment("exp_rules_custom_once", 2), treatment("exp_rules_custom_once"),
         treatment("exp_rules_custom_once"), variable_value("k", "default"),
         treatment("exp_rules_custom_once"), PENDING],
        requires=[RULES_CAPABILITY]))

    late_split = rules({"name": "late", "type": "split", "percentages": "50/50"})
    no_unit = {"user_id": "parity-user"}
    specs.append(spec(
        "Split Rule Without Its Unit Gives Variant 0, Then Re-Resolves Once The Unit Is Set",
        "The split rule matches but its unit type (session_id) is not set: the experiment's "
        "no-unit result applies (variant 0, not full-on). The assignment is not exposed (peek), "
        "so setting the unit re-resolves it through the split (variant 1) and the first treatment "
        "exposes that with ruleOverride:true.",
        [seeded(experiment(440, "exp_rules_late_unit", assignment_rules=late_split, split=[1, 0]))],
        [peek("exp_rules_late_unit"), set_unit("session_id", LATE_UNIT),
         peek("exp_rules_late_unit"), treatment("exp_rules_late_unit"), PENDING],
        units=no_unit, requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "Split Rule Exposed Without Its Unit Stays Pinned After The Unit Is Set",
        "The split rule matches without its unit and treatment() exposes the no-unit result "
        "(variant 0). Setting the unit afterwards does not move the user: treatment still returns "
        "0 and no second exposure is queued.",
        [seeded(experiment(441, "exp_rules_late_pinned", assignment_rules=late_split, split=[1, 0]))],
        [treatment("exp_rules_late_pinned"), set_unit("session_id", LATE_UNIT),
         treatment("exp_rules_late_pinned"), PENDING],
        units=no_unit, requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "Split Rule Without Its Unit On A Full-On Experiment Gives The Full-On Variant",
        "On a full-on experiment (fullOnVariant 2) a split rule matching without its unit gives "
        "the full-on variant; once the unit is set (not yet exposed) it re-resolves through the "
        "split.",
        [seeded(experiment(442, "exp_rules_late_fullon", variants=3, assignment_rules=rules(
            {"name": "late", "type": "split", "percentages": "50/50/0"})), full_on=2)],
        [peek("exp_rules_late_fullon"), set_unit("session_id", LATE_UNIT),
         peek("exp_rules_late_fullon"), treatment("exp_rules_late_fullon"), PENDING],
        units=no_unit, requires=[RULES_CAPABILITY]))

    fallback = {"name": "fallback", "type": "assign", "variant": 0}
    malformed = (("number", 50), ("non-numeric", "abc/def"), ("negative", "-10/110"),
                 ("sum-90", "50/40"), ("missing", None))
    exps, steps = [], []
    for i, (label, value) in enumerate(malformed):
        bad = {"name": label, "type": "split"}
        if value is not None:
            bad["percentages"] = value
        name = f"exp_rules_malformed_{i}"
        exps.append(experiment(450 + i, name, assignment_rules=rules(bad, fallback)))
        steps.append(treatment(name))
    steps.append(PENDING)
    specs.append(spec(
        "Malformed Split Rules Are Skipped For The Next Rule",
        "A split rule whose percentages are a number, non-numeric, negative, sum to 90, or are "
        "missing is skipped, and the next rule (assign variant 0) wins with ruleOverride:true.",
        exps, steps, requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "Wrong-Count Split And Negative Assign Variant Stop Rule Evaluation",
        "A split with three values on a two-variant experiment, and an assign rule with variant "
        "-1, each stop rule evaluation: the later assign-0 rule is NOT tried and the unit gets "
        "its normal assignment (variant 1, assigned:true, ruleOverride:false).",
        [experiment(460, "exp_rules_wrong_count", assignment_rules=rules(
            {"name": "three", "type": "split", "percentages": "50/25/25"}, fallback)),
         experiment(461, "exp_rules_negative", assignment_rules=rules(
             {"name": "neg", "type": "assign", "variant": -1}, fallback))],
        [treatment("exp_rules_wrong_count"), treatment("exp_rules_negative"), PENDING],
        requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "Split Percentages Must Sum To 100 Within 0.01",
        "'50/50.014' (gap 0.014, rounded to 0.01) is accepted and splits the unit to variant 1; "
        "'50/50.015' (gap rounds to 0.02) is skipped and the next rule assigns variant 0 (the same "
        "unit would split to variant 1, so accepting it is visible).",
        [seeded(experiment(462, "exp_rules_tol_ok", split=[1, 0], assignment_rules=rules(
            {"name": "ok", "type": "split", "percentages": "50/50.014"}, fallback))),
         seeded(experiment(463, "exp_rules_tol_bad", split=[1, 0], assignment_rules=rules(
             {"name": "bad", "type": "split", "percentages": "50/50.015"}, fallback)))],
        [treatment("exp_rules_tol_ok"), treatment("exp_rules_tol_bad"), PENDING],
        units={"session_id": LATE_UNIT}, requires=[RULES_CAPABILITY]))

    specs.append(spec(
        "Rule Condition Uses A Semver Operator",
        "A rule condition semver_gte(app_version, 2.0.0) matches 2.1.0 (variant 0 via the rule) "
        "and stops matching when app_version is set to 1.9.9 (normal assignment, variant 1).",
        [experiment(470, "exp_rules_semver", assignment_rules=rules(
            {"name": "modern", "type": "assign", "variant": 0,
             "conditions": {"semver_gte": [{"var": "app_version"}, {"value": "2.0.0"}]}}))],
        [attribute("app_version", "2.1.0"), treatment("exp_rules_semver"), PENDING,
         attribute("app_version", "1.9.9"), treatment("exp_rules_semver"), PENDING],
        requires=[RULES_CAPABILITY, SEMVER_CAPABILITY]))

    return specs


# ---------------------------------------------------------------------------
# Semver operators
# ---------------------------------------------------------------------------

def semver_case_spec(title, description, cases, first_id):
    """One experiment per (operator, lhs, rhs) case, each with a strict audience on its own
    attribute, so one context checks many comparisons: a match is variant 1, a miss variant 0
    with audienceMismatch:true. lhs None leaves the attribute unset."""
    exps, steps = [], []
    for i, (op, lhs, rhs) in enumerate(cases):
        var = f"v{i}"
        name = f"exp_semver_{first_id + i}"
        exps.append(experiment(first_id + i, name, audience=audience_filter(
            {op: [{"var": var}, {"value": rhs}]}), audience_strict=True))
        if lhs is not None:
            steps.append(attribute(var, lhs))
    for i in range(len(cases)):
        steps.append(treatment(f"exp_semver_{first_id + i}"))
    steps.append(PENDING)
    listing = "; ".join(f"{op}({json.dumps(lhs)}, {json.dumps(rhs)})" for op, lhs, rhs in cases)
    return spec(title, f"{description} Cases, in order: {listing}.", exps, steps,
                requires=[SEMVER_CAPABILITY])


def semver_specs():
    return [
        semver_case_spec(
            "semver_eq Normalizes Prefix, Missing Parts And Build Metadata",
            "semver_eq ignores a leading v, treats missing parts as 0, ignores build metadata, and "
            "distinguishes a different patch and a pre-release from its release.",
            [("semver_eq", "v1.2.3", "1.2.3"), ("semver_eq", "1.2", "1.2.0"),
             ("semver_eq", "1.0.0+a", "1.0.0+b"), ("semver_eq", "1.2.3", "1.2.4"),
             ("semver_eq", "1.0.0-rc.1", "1.0.0")], 500),
        semver_case_spec(
            "semver_gt And semver_gte Compare Numerically",
            "semver_gt/semver_gte compare numeric identifiers numerically (1.10.0 > 1.9.9), rank a "
            "release above its pre-release, and treat build metadata as equal.",
            [("semver_gt", "1.10.0", "1.9.9"), ("semver_gt", "2.0.0", "2.0.0-rc.1"),
             ("semver_gt", "1.0.0", "1.0.0"), ("semver_gt", "1.0.0+b", "1.0.0+a"),
             ("semver_gte", "1.0.0", "1.0.0"), ("semver_gte", "0.9.10", "0.10.0"),
             ("semver_gte", "1.0.0+build.9", "1.0.0")], 510),
        semver_case_spec(
            "semver_lt And semver_lte Compare Numerically",
            "semver_lt/semver_lte mirror gt/gte, including the pre-release and equal boundaries.",
            [("semver_lt", "1.9.9", "1.10.0"), ("semver_lt", "1.0.0-alpha", "1.0.0"),
             ("semver_lt", "1.0.0", "1.0.0"), ("semver_lte", "1.0.0", "1.0.0"),
             ("semver_lte", "1.0.1", "1.0.0"), ("semver_lte", "1.0.0-rc.1", "1.0.0-rc.1")], 520),
        semver_case_spec(
            "Pre-Release Precedence Follows The semver.org Chain",
            "Each step of 1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < "
            "1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0 holds under semver_lt, and the reverse "
            "does not (numeric identifiers rank below alphanumeric ones, a longer identifier list "
            "ranks above its prefix, and numeric identifiers compare numerically).",
            [("semver_lt", "1.0.0-alpha", "1.0.0-alpha.1"),
             ("semver_lt", "1.0.0-alpha.1", "1.0.0-alpha.beta"),
             ("semver_lt", "1.0.0-alpha.beta", "1.0.0-beta"),
             ("semver_lt", "1.0.0-beta", "1.0.0-beta.2"),
             ("semver_lt", "1.0.0-beta.2", "1.0.0-beta.11"),
             ("semver_lt", "1.0.0-beta.11", "1.0.0-rc.1"),
             ("semver_lt", "1.0.0-rc.1", "1.0.0"),
             ("semver_lt", "1.0.0-beta.11", "1.0.0-beta.2"),
             ("semver_lt", "1.0.0-alpha.beta", "1.0.0-alpha.1")], 530),
        semver_case_spec(
            "Invalid And Missing Versions Evaluate Like The Reference",
            "An empty version, a bare 'v', a bare '+build', an unset attribute and an empty "
            "right-hand side make the comparison null, so a strict audience does not match. Numbers "
            "are compared as their string form (2 > 1.9.9), and a non-numeric core is compared as an "
            "alphanumeric identifier, which ranks above any numeric one (so 'abc' > 1.0.0 and "
            "'abc' < 1.0.0 is false), exactly as the JS reference does.",
            [("semver_eq", "", "1.0.0"), ("semver_eq", "v", "1.0.0"),
             ("semver_eq", "+build", "1.0.0"), ("semver_gte", None, "1.0.0"),
             ("semver_lte", "1.0.0", ""), ("semver_gt", 2, "1.9.9"),
             ("semver_gt", "abc", "1.0.0"), ("semver_lt", "abc", "1.0.0")], 540),
    ]


# ---------------------------------------------------------------------------
# X-Agent (not computed by the JS reference; see the module docstring)
# ---------------------------------------------------------------------------

def xagent_scenarios(first_number):
    exp = experiment(600, "exp_xagent")
    create = {
        "action": "createContext",
        "params": {"units": UNIT, "options": {"publishDelay": -1, "refreshPeriod": 0},
                   "publishToEndpoint": True},
        "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                   "events": [{"type": "ready",
                               "data": {"experiments": [{"id": 600, "name": "exp_xagent"}]}}]},
    }
    agent = {"minRequests": 1, "headerPatterns": {"X-Agent": XAGENT_PATTERN}}
    return [
        {
            "name": f"{first_number}{XAGENT_MARKER}Context Fetch Sends absmartly-<lang>-sdk/<version>",
            "description": "The SDK's own GET {endpoint}/context request carries an X-Agent header "
                           "of the form absmartly-<lang>-sdk/<semver>. The wrapper records the "
                           "request it served (capturedRequests).",
            "contextData": {"experiments": [exp]},
            "steps": [create, {"action": "capturedRequests", "params": {"method": "GET"},
                               "expect": agent}],
            "requires": [XAGENT_CAPABILITY],
        },
        {
            "name": f"{first_number + 1}{XAGENT_MARKER}Publish Sends absmartly-<lang>-sdk/<version>",
            "description": "The SDK's own PUT {endpoint}/context publish request carries an X-Agent "
                           "header of the form absmartly-<lang>-sdk/<semver>. The context is created "
                           "with publishToEndpoint so the SDK's real publisher sends to the wrapper's "
                           "mock collector, which records the request.",
            "contextData": {"experiments": [exp]},
            "steps": [
                create,
                {"action": "treatment", "params": {"experimentName": "exp_xagent"},
                 "expect": {"result": 1, "events": [{"type": "exposure"}]}},
                {"action": "publish", "params": {}, "expect": {"result": None,
                                                               "events": [{"type": "publish"}]}},
                {"action": "capturedRequests", "params": {"method": "PUT"},
                 "expect": agent},
            ],
            "requires": [XAGENT_CAPABILITY],
        },
    ]


# ---------------------------------------------------------------------------
# Explicitly blank client application (not computed by the JS reference: it rejects "" outright)
# ---------------------------------------------------------------------------

def client_application_scenarios(first_number):
    """A wrapper must hand an explicitly supplied `client.application` to its SDK unchanged, never
    repair a blank one into its own default (WRAPPER_API_SPEC.md, POST /context `client`). SDKs split
    on what they then do with "": most reject it at client construction (the wrapper's 400), the rest
    accept it and leave `application` out of the evaluation map. Each lane runs the one that matches
    its SDK; a wrapper that substitutes a default fails either way (an accepted context in the first,
    a present application in the second)."""
    blank = {"application": "", "environment": SYSATTR_ENV}
    create_params = {"units": UNIT, "options": {"publishDelay": -1, "refreshPeriod": 0},
                     "client": blank}
    present = experiment(610, "exp_blank_app_present",
                         audience=audience_filter({"not": {"null": {"var": "application"}}}),
                         audience_strict=True)
    env = experiment(611, "exp_blank_app_env",
                     audience=audience_filter({"eq": [{"var": "environment"}, {"value": SYSATTR_ENV}]}),
                     audience_strict=True)

    def exposure(exp, matched):
        return {"type": "exposure", "data": {
            "id": exp["id"], "name": exp["name"], "unit": "session_id", "variant": 1 if matched else 0,
            "assigned": matched, "eligible": True, "overridden": False, "fullOn": False,
            "custom": False, "audienceMismatch": not matched}}

    return [
        {
            "name": f"{first_number}{CLIENT_APP_MARKER}An Explicitly Blank Application Is Rejected",
            "description": "POST /context with client.application \"\" on an SDK whose client "
                           "rejects a blank application: the wrapper forwards the value unchanged, so "
                           "creation fails with the SDK's own validation error as a 400. A wrapper "
                           "that repairs the blank name into its default application returns a ready "
                           "context instead and fails.",
            "contextData": {"experiments": [env]},
            "steps": [{"action": "createContextWith", "params": create_params,
                       "expect": {"errorAnyOf": BLANK_APPLICATION_ERRORS, "events": []}}],
            "requires": [REJECTS_BLANK_APP_CAPABILITY],
        },
        {
            "name": f"{first_number + 1}{CLIENT_APP_MARKER}An Explicitly Blank Application Is Left Out "
                    f"Of The Evaluation Map",
            "description": "POST /context with client.application \"\" on an SDK that accepts a blank "
                           "application: the SDK leaves application out of the evaluation map, so a "
                           "strict audience requiring application to be present misses (variant 0, "
                           "audienceMismatch:true), while the configured environment still matches. A "
                           "wrapper that repairs the blank name into its default application makes "
                           "the first audience match and fails. Expectations follow the system-"
                           "attributes contract (scenario 253's absence check), not the JS reference, "
                           "which rejects a blank application.",
            "contextData": {"experiments": [present, env]},
            "steps": [
                {"action": "createContextWith", "params": create_params,
                 "expect": {"result": {"ready": True, "failed": False, "finalized": False},
                            "events": [{"type": "ready", "data": {"experiments": [
                                {"id": 610, "name": "exp_blank_app_present"},
                                {"id": 611, "name": "exp_blank_app_env"}]}}]}},
                {"action": "treatment", "params": {"experimentName": "exp_blank_app_present"},
                 "expect": {"result": 0, "events": [exposure(present, False)]}},
                {"action": "treatment", "params": {"experimentName": "exp_blank_app_env"},
                 "expect": {"result": 1, "events": [exposure(env, True)]}},
                {"action": "pending", "params": {}, "expect": {"result": 2, "events": []}},
            ],
            "requires": [ACCEPTS_BLANK_APP_CAPABILITY],
        },
    ]


# ---------------------------------------------------------------------------
# JS reference run
# ---------------------------------------------------------------------------

def run_reference(specs):
    cases = []
    for s in specs:
        data = {"experiments": s["experiments"]}
        if s["holdouts"]:
            data["holdouts"] = s["holdouts"]
        case = {"units": s["units"], "contextData": data,
                "steps": [{"action": a, "params": p} for a, p in s["steps"]]}
        if s["client"] is not None:
            case["client"] = s["client"]
        cases.append(case)
    env = dict(os.environ, SDK_PATH=round2611.SDK_PATH)
    proc = subprocess.run(["node", round2611.COMPUTE], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"compute.js failed ({proc.returncode}):\n{proc.stderr}")
    out = json.loads(proc.stdout)
    if out["sdkVersion"] != round2611.JS_SDK_VERSION:
        raise SystemExit(f"JS reference is {out['sdkVersion']}, expected {round2611.JS_SDK_VERSION}")
    if len(out["cases"]) != len(specs):
        raise SystemExit("JS reference returned an incomplete scenario list")
    return out["cases"]


def build_scenario(s, ran, number, marker, strip_rule_override):
    if ran["ready"] != {"ready": True, "failed": False, "finalized": False}:
        raise SystemExit(f"{s['title']}: JS context not ready: {ran['ready']}")
    create_params = {"units": s["units"], "options": {"publishDelay": -1, "refreshPeriod": 0}}
    if s["client"] is not None:
        create_params["client"] = s["client"]
    out_steps = [{"action": "createContextWith", "params": create_params,
                  "expect": {"result": ran["ready"],
                             "events": round2611.ready_events(ran["readyEvents"])}}]
    for (action, params), got in zip(s["steps"], ran["steps"]):
        events = got["events"]
        for event in events:
            if event["type"] != "exposure":
                raise SystemExit(f"{s['title']}: unexpected {event['type']} event from {action}")
        if strip_rule_override:
            # The semver group also runs on SDKs without assignment rules, which never emit
            # `ruleOverride`; only the rules group asserts it.
            events = [{**e, "data": {k: v for k, v in e["data"].items() if k != "ruleOverride"}}
                      for e in events]
        out_steps.append({"action": action, "params": params,
                          "expect": {"result": got["result"], "events": events}})
    context_data = {"experiments": s["experiments"]}
    if s["holdouts"]:
        context_data["holdouts"] = s["holdouts"]
    scenario = {
        "name": f"{number}{marker}{s['title']}",
        "description": f"{s['description']} Expected results recorded from "
                       f"@absmartly/javascript-sdk {round2611.JS_SDK_VERSION} (javascript-sdk#68).",
        "contextData": context_data,
        "steps": out_steps,
    }
    if s["requires"]:
        scenario["requires"] = s["requires"]
    return scenario


def build_groups():
    round2611.ensure_reference_build()
    groups = []
    n = FIRST_NUMBER
    for specs, marker in ((rules_specs(), RULES_MARKER), (semver_specs(), SEMVER_MARKER)):
        titles = [s["title"] for s in specs]
        if len(set(titles)) != len(titles):
            raise SystemExit(f"duplicate scenario titles in {marker}")
        for s, ran in zip(specs, run_reference(specs)):
            if len(ran["steps"]) != len(s["steps"]):
                raise SystemExit(f"{s['title']}: JS reference returned an incomplete step list")
            groups.append(build_scenario(s, ran, n, marker, strip_rule_override=marker != RULES_MARKER))
            n += 1
    groups.extend(xagent_scenarios(n))
    groups.extend(client_application_scenarios(FIRST_NUMBER + len(groups)))
    return groups


def assemble(existing, build_groups):
    """The canonical file with the parity groups (from `build_groups()`) rebuilt after every other
    scenario. Returns (updated scenarios, the rebuilt groups)."""
    kept = [s for s in existing if not any(m in s["name"] for m in PARITY_MARKERS)]
    groups = build_groups()
    # The parity groups own FIRST_NUMBER..; any other scenario numbered in that range (e.g. a later
    # generator appending without knowing it) is a collision, not something to renumber silently.
    taken = {int(s["name"].split(" - ", 1)[0]) for s in kept}
    clashes = sorted(taken & {int(g["name"].split(" - ", 1)[0]) for g in groups})
    if clashes or max(taken) >= FIRST_NUMBER:
        raise SystemExit(f"scenarios {clashes or [max(taken)]} collide with the parity range "
                         f"starting at {FIRST_NUMBER}")
    return kept + groups, groups


def main():
    parser = argparse.ArgumentParser(description="Generate the 2026-11 feature-parity scenario groups.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    parser.add_argument("--file", default=CANONICAL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as src:
        raw = src.read()
    updated, groups = assemble(json.loads(raw), build_groups)
    kept = [s for s in updated if s not in groups]
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if rendered != raw:
            print("test_scenarios_complete.json is stale: run generate_parity_round_scenarios.py")
            return 1
        print(f"up to date: {len(updated) - len(kept)} feature-parity scenarios")
        return 0

    with open(args.file, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(updated) - len(kept)} feature-parity)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
