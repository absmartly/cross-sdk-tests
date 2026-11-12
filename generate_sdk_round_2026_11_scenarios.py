#!/usr/bin/env python3
"""Generate the 2026-11 SDK-round conformance scenario groups of test_scenarios_complete.json.

This round of SDK PRs aligns every SDK to the JavaScript reference on four behaviours that the
wrapper API can observe black-box:

  * Custom assignment rules (an experiment's `assignmentRules` string), ported from the JS SDK to
    Java (java-sdk#17) and fixed in JS by javascript-sdk#67. A rule that matches sets the variant,
    flags the exposure `ruleOverride:true` with `assigned:false`, serves variable values, takes
    precedence over audience targeting and custom assignments, and is beaten only by an override.
    A rule whose variant is out of range (or whose split has the wrong arity) is ignored and later
    rules are NOT tried. Rules can be scoped to environments. A rule variant for an experiment
    removed from the context data is rebuilt, not served stale (one of #67's four edge-case fixes).
  * Semver operators `semver_eq/gt/gte/lt/lte` in audience filters (java-sdk#17 adds them to Java;
    the JS reference already has them): leading v/V ignored, +build metadata ignored, numeric
    identifiers compared numerically (1.10.0 > 1.9.0), locale-independent.
  * Audience re-evaluation after setAttribute: setting an attribute that flips a strict audience
    reassigns the unit and queues a fresh exposure (go-sdk#10, php-sdk#8, ruby-sdk#39,
    python3-sdk#11 all adopt the JS attribute-sequence behaviour).
  * System attributes (§5 of Márcio's 2026-11 brief, every SDK's feat/sdk-system-attributes branch):
    audiences and assignment-rule conditions always evaluate with the client's configured
    `application` and `environment` names (no opt-in), user attributes always overlay the system
    values, a name that is not configured is left out of the map, and `app_version` is gone. The
    group mirrors §4 tests 1 (strict audience on environment matches), 3 (no match), 4 (user value
    overrides + cache invalidation) and 6 (no environment configured), plus a variable read through
    the matched variant. Tests 1/3/4 and the variable read are UNGATED: every SDK in §3 must pass
    them, and an SDK that does not inject application/environment (or injects but lets them beat a
    user value) must fail. Test 6 (no environment configured) is gated on the `clientNoEnvironment`
    capability: an SDK whose public client API mandates a non-empty environment cannot construct the
    case black-box (its own unit tests cover it), so it lists a reviewed exception and the one
    scenario SKIPs there.

The reference is @absmartly/javascript-sdk built from the sibling ../javascript-sdk checkout on the
branch sdk-branches.sh pins for javascript-sdk: feat/sdk-system-attributes (javascript-sdk PR #68).
That is main (already the TypeScript rewrite, custom assignment rules from #66, the semver operators,
and #67's four edge-case fixes) plus §5's always-on application/environment attributes and
app_version removal. The expected results are NOT written by hand: every scenario's steps run through
that build via tools/sdk-round-2026-11/compute.js and each step's `result` and `events` are recorded
verbatim (exposedAt/achievedAt/publishedAt dropped, the ready event projected to experiment id/name
as in the rest of the suite).

The two rule/semver groups carry `requires` so SDKs whose round-2026-11 PR has not landed are
skipped rather than failed; the audience-re-evaluation and system-attributes groups are ungated
(attribute + treatment are core actions and every §3 SDK implements the system attributes). See
orchestrator/expected-capabilities.json and docs/sdk-round-2026-11-conformance.md.

Usage:
    python3 generate_sdk_round_2026_11_scenarios.py          # rewrite the groups in place
    python3 generate_sdk_round_2026_11_scenarios.py --check   # exit 1 if the canonical file is stale

Scenarios whose name carries one of the GROUP_MARKERs are replaced; every other scenario is left
untouched, the groups are appended after the last existing scenario, and the file is serialized
exactly as before (indent=2, ensure_ascii=False, trailing newline).
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(HERE, "test_scenarios_complete.json")
TOOL_DIR = os.path.join(HERE, "tools", "sdk-round-2026-11")
COMPUTE = os.path.join(TOOL_DIR, "compute.js")
SDK_PATH = os.environ.get("SDK_PATH", os.path.abspath(os.path.join(HERE, "..", "javascript-sdk")))

# javascript-sdk PR #68 (feat/sdk-system-attributes) head. The reference build must be this
# exact commit so the recorded expectations are reproducible; the build carries version 1.14.0.
# This branch is main (the TypeScript rewrite, custom assignment rules from #66, the semver_*
# operators, and the four #67 edge-case fixes, all already merged to main) plus §5 of Márcio's
# 2026-11 brief: always-on application/environment audience attributes and app_version removal.
PINNED_COMMIT = "437a425c570ee34af098cf7e77d6cfb37ab4b475"
JS_SDK_VERSION = "1.14.0"

RULES_MARKER = " - Custom Assignment Rules - "
SEMVER_MARKER = " - Semver Operators - "
REEVAL_MARKER = " - Audience Re-Evaluation - "
SYSATTR_MARKER = " - System Attributes - "
GROUP_MARKERS = (RULES_MARKER, SEMVER_MARKER, REEVAL_MARKER, SYSATTR_MARKER)

RULES_CAPABILITY = "assignmentRules"
SEMVER_CAPABILITY = "semverOperators"
# Gates ONLY the system-attributes no-environment scenario. An SDK whose public
# client API mandates a non-empty environment cannot construct the "no environment
# configured" case black-box through the wrapper (its own unit tests cover it with
# an internal override), so it lists this as a reviewed exception and the scenario
# SKIPs; wrappers that can build a no-environment-reporting client advertise it true.
NO_ENVIRONMENT_CAPABILITY = "clientNoEnvironment"

UNIT = {"session_id": "svk-unit-1"}

# The system-attributes group's fixture client. Names chosen so no SDK wrapper's
# own default application/environment matches them by accident (the wrappers use
# test-app/test-env, website, test, etc.), so a scenario that targets
# "sysattr-app"/"sysattr-env" only matches when the SDK actually injects the
# configured client names into the evaluation map.
SYSATTR_APP = "sysattr-app"
SYSATTR_ENV = "sysattr-env"


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def experiment(exp_id, name, *, variants=2, split=None, traffic_eligible=True,
               audience=None, audience_strict=False, assignment_rules=None, configs=None):
    """A deterministic experiment: split one-hot on the last arm so a unit whose unit type is set is
    always served the highest variant by ordinary assignment (no seed search), and every SDK agrees
    on that variant by construction. `configs` overrides per-variant variable configs."""
    if split is None:
        # one-hot on the last variant: ordinary assignment always lands there.
        split = [0.0] * (variants - 1) + [1.0]
    variant_list = []
    for i in range(variants):
        cfg = configs[i] if configs is not None else None
        variant_list.append({"name": "ABCDE"[i], "config": cfg})
    return {
        "id": exp_id,
        "name": name,
        "iteration": 1,
        "unitType": "session_id",
        "seedHi": 0,
        "seedLo": 0,
        "split": split,
        "trafficSeedHi": 0,
        "trafficSeedLo": 0,
        "trafficSplit": [0, 1] if traffic_eligible else [1, 0],
        "fullOnVariant": 0,
        "applications": [{"name": "website"}],
        "variants": variant_list,
        "audience": audience,
        "audienceStrict": audience_strict,
        "customFieldValues": None,
        "assignmentRules": assignment_rules,
    }


def rules(*rule_list):
    return json.dumps({"rules": list(rule_list)}, separators=(",", ":"))


def audience_filter(expr):
    return json.dumps({"filter": [expr]}, separators=(",", ":"))


# step helpers (action, params)
def treatment(name):
    return ("treatment", {"experimentName": name})


def peek(name):
    return ("peek", {"experimentName": name})


def variable_value(key, default):
    return ("variableValue", {"key": key, "defaultValue": default})


def attribute(name, value):
    return ("attribute", {"name": name, "value": value})


def override(name, variant):
    return ("override", {"experimentName": name, "variant": variant})


def custom_assignment(name, variant):
    return ("customAssignment", {"experimentName": name, "variant": variant})


def refresh(new_data):
    return ("refresh", {"newData": new_data})


PENDING = ("pending", {})


# ---------------------------------------------------------------------------
# Scenario specs
# ---------------------------------------------------------------------------

def rules_specs():
    """(title, description, contextData experiments, steps). The JS build computes every result."""
    specs = []

    assign_us = rules({"name": "us", "type": "assign", "variant": 1,
                       "conditions": {"eq": [{"var": "country"}, {"value": "US"}]}})

    specs.append((
        "Matching Assign Rule Wins And Flags ruleOverride",
        "An assign rule whose condition matches sets the variant; the exposure is flagged "
        "ruleOverride:true with assigned:false (the backend excludes it from analysis), not custom.",
        [experiment(1, "exp_rule_assign", assignment_rules=assign_us)],
        [attribute("country", "US"), treatment("exp_rule_assign"), PENDING],
    ))

    specs.append((
        "Non-Matching Assign Rule Falls Through To Normal Assignment",
        "An assign rule whose condition does not match is skipped and the unit takes its ordinary "
        "assignment (variant 1 here, assigned:true, ruleOverride:false).",
        [experiment(2, "exp_rule_nomatch", assignment_rules=assign_us)],
        [attribute("country", "CA"), treatment("exp_rule_nomatch"), PENDING],
    ))

    out_of_range = rules(
        {"name": "oor", "type": "assign", "variant": 5},
        {"name": "fallback", "type": "assign", "variant": 0},
    )
    specs.append((
        "Out-Of-Range Rule Variant Is Ignored And Later Rules Are Not Tried",
        "The first matching rule's variant (5) is out of range for a two-variant experiment, so "
        "normal assignment applies and the second rule (variant 0) is NOT tried: the unit gets its "
        "ordinary assignment (variant 1), not variant 0.",
        [experiment(3, "exp_rule_oor", assignment_rules=out_of_range)],
        [treatment("exp_rule_oor"), PENDING],
    ))

    split_rule = rules({"name": "split", "type": "split", "percentages": "0/100"})
    specs.append((
        "Split Rule Randomizes Over Its Own Percentages And Flags ruleOverride",
        "A split rule with percentages 0/100 sends every unit to variant 1 over the rule's own "
        "percentages; the exposure is flagged ruleOverride:true, assigned:false.",
        [experiment(4, "exp_rule_split", assignment_rules=split_rule)],
        [treatment("exp_rule_split"), PENDING],
    ))

    env_rule = rules({"name": "prod-only", "type": "assign", "variant": 0,
                      "environments": ["production"]})
    specs.append((
        "Environment-Scoped Rule Does Not Match A Different Environment",
        "A rule scoped to environment 'production' does not match the reference context's "
        "environment, so it is skipped and the unit takes its ordinary assignment (variant 1). "
        "'production' is used because it matches no SDK wrapper's environment name.",
        [experiment(5, "exp_rule_env", assignment_rules=env_rule)],
        [treatment("exp_rule_env"), PENDING],
    ))

    assign_1 = rules({"name": "a", "type": "assign", "variant": 1})
    specs.append((
        "Override Beats A Matching Rule",
        "setOverride wins over every rule: with a rule that would assign variant 1, an override to "
        "variant 2 gives variant 2 with overridden:true (ruleOverride:false, assigned:false).",
        [experiment(6, "exp_rule_override", variants=3, assignment_rules=assign_1)],
        [override("exp_rule_override", 2), treatment("exp_rule_override"), PENDING],
    ))

    strict_gold = audience_filter({"eq": [{"var": "tier"}, {"value": "gold"}]})
    specs.append((
        "Rule Takes Precedence Over A Strict Audience Mismatch",
        "A matching rule is evaluated before audience targeting: even though the strict audience "
        "(tier == gold) does not match, the rule assigns variant 1 with ruleOverride:true and "
        "audienceMismatch:false (audience is not evaluated for a rule win).",
        [experiment(7, "exp_rule_audience", audience=strict_gold, audience_strict=True,
                    assignment_rules=assign_1)],
        [treatment("exp_rule_audience"), PENDING],
    ))

    specs.append((
        "Rule Takes Precedence Over A Custom Assignment",
        "A matching rule wins over a custom assignment: customAssignment(2) is set, but the rule "
        "assigns variant 1 with ruleOverride:true (custom:false). The custom assignment only shows "
        "when no rule matches.",
        [experiment(8, "exp_rule_custom", variants=3, assignment_rules=assign_1)],
        [custom_assignment("exp_rule_custom", 2), treatment("exp_rule_custom"), PENDING],
    ))

    red_blue = [json.dumps({"color": "red"}, separators=(",", ":")),
                json.dumps({"color": "blue"}, separators=(",", ":"))]
    specs.append((
        "Variable Value Is Served Through A Rule Win",
        "variableValue resolves through a rule-won assignment (assigned:false but ruleOverride): "
        "the rule assigns variant 1, so variableValue('color') returns variant 1's 'blue' and "
        "queues the rule-flagged exposure.",
        [experiment(9, "exp_rule_variable", assignment_rules=assign_1, configs=red_blue)],
        [variable_value("color", "default"), PENDING],
    ))

    removed_before = [experiment(10, "exp_rule_removed", assignment_rules=assign_us)]
    specs.append((
        "Rule Variant Of A Removed Experiment Is Rebuilt, Not Served Stale",
        "javascript-sdk#67 edge case: a rule assigns variant 1 (ruleOverride:true). After a refresh "
        "whose data no longer contains the experiment, the cached rule variant is dropped and the "
        "experiment is rebuilt to variant 0 (assigned:false, ruleOverride:false) with a fresh "
        "exposure, instead of serving the stale rule variant.",
        removed_before,
        [attribute("country", "US"), treatment("exp_rule_removed"), PENDING,
         refresh({"experiments": []}), peek("exp_rule_removed"),
         treatment("exp_rule_removed"), PENDING],
    ))

    return specs


def semver_specs():
    specs = []
    # Each: operator, lhs attribute value, rhs literal in the filter, whether it should match.
    cases = [
        ("semver_gte", "app_version", "1.5.0", "1.2.0", True,
         "1.5.0 >= 1.2.0 is true"),
        ("semver_gte", "app_version", "1.1.0", "1.2.0", False,
         "1.1.0 >= 1.2.0 is false"),
        ("semver_eq", "app_version", "v1.2", "1.2.0+build", True,
         "v1.2 == 1.2.0+build: leading v and +build metadata are ignored, missing patch is 0"),
        ("semver_gt", "app_version", "1.10.0", "1.9.0", True,
         "1.10.0 > 1.9.0: numeric identifiers compare numerically, not lexicographically"),
        ("semver_lt", "app_version", "1.0.0-beta", "1.0.0", True,
         "1.0.0-beta < 1.0.0: a pre-release ranks below its release"),
        ("semver_lte", "app_version", "2.0.0", "2.0.0", True,
         "2.0.0 <= 2.0.0 is true"),
    ]
    for i, (op, attr, lhs, rhs, should_match, why) in enumerate(cases, start=1):
        filt = audience_filter({op: [{"var": attr}, {"value": rhs}]})
        name = f"exp_semver_{i}"
        verb = "matches" if should_match else "does not match"
        specs.append((
            f"{op} {verb.title()} ({lhs} vs {rhs})",
            f"Strict audience uses {op}; with {attr}={lhs!r} the filter {verb} ({why}), so the unit "
            f"gets {'variant 1 (assigned)' if should_match else 'variant 0 with audienceMismatch:true'}.",
            [experiment(100 + i, name, audience=filt, audience_strict=True)],
            [attribute(attr, lhs), treatment(name), PENDING],
        ))
    return specs


def reeval_specs():
    strict_gold = audience_filter({"eq": [{"var": "tier"}, {"value": "gold"}]})
    specs = []
    specs.append((
        "Setting An Attribute That Satisfies A Strict Audience Reassigns And Re-Exposes",
        "A strict audience (tier == gold) is unmatched at first, so treatment is variant 0 with "
        "audienceMismatch:true. Setting tier=gold re-evaluates the audience on the next treatment: "
        "the unit now matches, is assigned variant 1, and a fresh exposure is queued (pending grows).",
        [experiment(200, "exp_reeval_in", audience=strict_gold, audience_strict=True)],
        [treatment("exp_reeval_in"), PENDING,
         attribute("tier", "gold"), treatment("exp_reeval_in"), PENDING],
    ))
    specs.append((
        "Setting An Attribute That Breaks A Strict Audience Reassigns To Control",
        "The mirror case: tier=gold matches first (variant 1, assigned). Setting tier=silver "
        "re-evaluates the audience on the next treatment: the unit no longer matches, is reassigned "
        "variant 0 with audienceMismatch:true, and a fresh exposure is queued.",
        [experiment(201, "exp_reeval_out", audience=strict_gold, audience_strict=True)],
        [attribute("tier", "gold"), treatment("exp_reeval_out"), PENDING,
         attribute("tier", "silver"), treatment("exp_reeval_out"), PENDING],
    ))
    return specs


def sysattr_specs():
    """System-attributes group (§5 of Márcio's 2026-11 brief), ungated.

    Every SDK must inject the client's configured `application` and `environment`
    names into the audience/rule evaluation map (always on, no opt-in), overlay
    user attributes on top, leave an unconfigured name out of the map, and never
    see `app_version`. These mirror §4 tests 1, 3, 4 and 6 plus a variable read
    through the matched variant. Each scenario sets the client config explicitly
    (the 5th spec element) so it does not depend on any wrapper's default names;
    the names "sysattr-app"/"sysattr-env" match no wrapper default, so a scenario
    only passes when the SDK actually injects the configured names.
    """
    env_filter = audience_filter({"eq": [{"var": "environment"}, {"value": SYSATTR_ENV}]})
    app_filter = audience_filter({"eq": [{"var": "application"}, {"value": SYSATTR_APP}]})
    other_env_filter = audience_filter({"eq": [{"var": "environment"}, {"value": "other-env"}]})
    blue = [json.dumps({"color": "red"}, separators=(",", ":")),
            json.dumps({"color": "blue"}, separators=(",", ":"))]
    full_client = {"application": SYSATTR_APP, "environment": SYSATTR_ENV}
    specs = []

    # §4 test 1: strict audience on the configured environment matches (variant 1,
    # audienceMismatch:false), plus a variable read through the matched variant.
    specs.append((
        "Strict Audience On The Configured Environment Matches And Serves The Variant",
        "§4.1 + variable read: a strict audience (environment == the configured environment name) "
        "matches because the SDK always injects the client's environment into the evaluation map, so "
        "the unit is assigned variant 1 with audienceMismatch:false, and variableValue('color') reads "
        "the matched variant 1's 'blue' through the same assignment.",
        [experiment(300, "exp_sysattr_env", audience=env_filter, audience_strict=True, configs=blue)],
        [treatment("exp_sysattr_env"), PENDING, variable_value("color", "default"), PENDING],
        full_client,
    ))

    # §4 test 1 variant: strict audience on the configured application matches.
    specs.append((
        "Strict Audience On The Configured Application Matches",
        "§4.1 (application): a strict audience (application == the configured application name) "
        "matches because the SDK always injects the client's application into the evaluation map, so "
        "the unit is assigned variant 1 with audienceMismatch:false.",
        [experiment(301, "exp_sysattr_app", audience=app_filter, audience_strict=True)],
        [treatment("exp_sysattr_app"), PENDING],
        full_client,
    ))

    # §4 test 3: a strict audience naming a different environment does not match.
    specs.append((
        "Strict Audience On A Different Environment Does Not Match",
        "§4.3: a strict audience (environment == 'other-env') does not match the configured "
        "environment, so the unit gets variant 0 with audienceMismatch:true.",
        [experiment(302, "exp_sysattr_nomatch", audience=other_env_filter, audience_strict=True)],
        [treatment("exp_sysattr_nomatch"), PENDING],
        full_client,
    ))

    # §4 test 4: a user-set environment overlays the configured one and wins, and it
    # invalidates an assignment cached before the attribute was set.
    specs.append((
        "A User-Set Environment Overrides The Configured One And Invalidates The Cache",
        "§4.4: the configured environment matches at first (peek -> variant 1). Setting "
        "environment='other-env' as a user attribute overlays the system value and wins: the cached "
        "assignment is invalidated and the next treatment reassigns the unit to variant 0 with "
        "audienceMismatch:true and a fresh exposure. The strict audience names the configured "
        "environment, so only a user value that overrides it can flip the result.",
        [experiment(303, "exp_sysattr_override", audience=env_filter, audience_strict=True)],
        [peek("exp_sysattr_override"),
         attribute("environment", "other-env"), treatment("exp_sysattr_override"), PENDING],
        full_client,
    ))

    # §4 test 4 (mirror): a user-set environment satisfies an audience the configured
    # environment did not, reassigning the unit into the experiment.
    specs.append((
        "A User-Set Environment Satisfies An Audience The Configured One Did Not",
        "§4.4 (mirror): the configured environment does not match the strict audience "
        "(environment == 'other-env'), so the unit starts at variant 0 with audienceMismatch:true. "
        "Setting environment='other-env' as a user attribute overlays the system value, matches the "
        "audience, and reassigns the unit to variant 1 with a fresh exposure.",
        [experiment(304, "exp_sysattr_user_in", audience=other_env_filter, audience_strict=True)],
        [treatment("exp_sysattr_user_in"), PENDING,
         attribute("environment", "other-env"), treatment("exp_sysattr_user_in"), PENDING],
        full_client,
    ))

    # §4 test 6: no environment configured means no environment key in the map, so a
    # filter on environment does not match (and does not crash).
    specs.append((
        "No Environment Configured Means A Strict Environment Audience Does Not Match",
        "§4.6: with the application configured but NO environment, the SDK leaves environment out of "
        "the evaluation map entirely, so a strict audience on environment does not match and the unit "
        "gets variant 0 with audienceMismatch:true (no crash). An application audience still matches, "
        "confirming the application was injected while the environment was left absent.",
        [experiment(305, "exp_sysattr_noenv", audience=env_filter, audience_strict=True),
         experiment(306, "exp_sysattr_noenv_app", audience=app_filter, audience_strict=True)],
        [treatment("exp_sysattr_noenv"), PENDING,
         treatment("exp_sysattr_noenv_app"), PENDING],
        {"application": SYSATTR_APP},
        [NO_ENVIRONMENT_CAPABILITY],
    ))

    return specs


# ---------------------------------------------------------------------------
# JS reference run
# ---------------------------------------------------------------------------

def ensure_reference_build():
    """Build the sibling javascript-sdk checkout (lib/) from the pinned commit.

    Both the commit *and* the working tree are verified: a detached HEAD at the
    pin is not enough if the tree (including any previously built ``lib/``) has
    been modified, because the generator would then record results from code
    other than the pin. We therefore (a) require HEAD == the pin, (b) require a
    clean working tree, and (c) always rebuild ``lib/`` from source so its
    provenance is the checked-out, unmodified pin rather than a stale artifact
    left over from an earlier checkout.
    """
    if not os.path.isdir(SDK_PATH):
        raise SystemExit(f"javascript-sdk checkout not found at {SDK_PATH} (set SDK_PATH)")
    head = subprocess.run(["git", "-C", SDK_PATH, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False).stdout.strip()
    if head != PINNED_COMMIT:
        raise SystemExit(
            f"javascript-sdk checkout is at {head}, not the pinned reference {PINNED_COMMIT} "
            f"(javascript-sdk#68 head). Check it out (sdk-branches.sh pins this branch for CI)."
        )
    # A modified working tree (including a hand-edited or stale lib/) means the
    # build no longer corresponds to the pin, so refuse rather than record
    # results from unknown code. lib/ itself is gitignored, so it does not show
    # up here; we rebuild it unconditionally below to guarantee its provenance.
    dirty = subprocess.run(["git", "-C", SDK_PATH, "status", "--porcelain"],
                           capture_output=True, text=True, check=False).stdout.strip()
    if dirty:
        raise SystemExit(
            "javascript-sdk checkout has uncommitted changes; the reference build would not "
            f"match the pinned commit {PINNED_COMMIT}. Reset the checkout to a clean pin and retry.\n"
            + dirty
        )
    lib_index = os.path.join(SDK_PATH, "lib", "index.js")
    # Always rebuild lib/ from the (verified-clean, verified-pinned) source so a
    # leftover artifact from an earlier checkout can never leak into the oracle.
    print("building the javascript-sdk reference (npm install + compile + build-cjs)...", file=sys.stderr)
    subprocess.run(["npm", "install", "--ignore-scripts"], cwd=SDK_PATH, check=True)
    subprocess.run(["npm", "run", "generate-version"], cwd=SDK_PATH, check=True)
    subprocess.run(["npm", "run", "compile"], cwd=SDK_PATH, check=True)
    subprocess.run(["npm", "run", "build-cjs"], cwd=SDK_PATH, check=True)
    if not os.path.isfile(lib_index):
        raise SystemExit("javascript-sdk build did not produce lib/index.js")


def spec_client(spec):
    """The optional per-scenario client config (5th spec element): a dict with
    `application` and/or `environment`, or None when the scenario uses the fixed
    default client (application "website", environment "test"). Only the
    system-attributes group sets it."""
    return spec[4] if len(spec) > 4 else None


def spec_requires(spec):
    """The optional per-scenario `requires` list (6th spec element), overriding the
    group default. Used only by the system-attributes group's no-environment
    scenario, which needs the `clientNoEnvironment` capability (an SDK whose public
    client API mandates an environment cannot construct the no-environment case
    black-box; its own unit tests cover it)."""
    return spec[5] if len(spec) > 5 else None


def run_reference(specs):
    cases = []
    for spec in specs:
        _, _, exps, steps = spec[0], spec[1], spec[2], spec[3]
        case = {"units": UNIT, "contextData": {"experiments": exps},
                "steps": [{"action": a, "params": p} for a, p in steps]}
        client = spec_client(spec)
        if client is not None:
            case["client"] = client
        cases.append(case)
    env = dict(os.environ, SDK_PATH=SDK_PATH)
    proc = subprocess.run(["node", COMPUTE], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"compute.js failed ({proc.returncode}):\n{proc.stderr}")
    out = json.loads(proc.stdout)
    if out["sdkVersion"] != JS_SDK_VERSION:
        raise SystemExit(f"JS reference is {out['sdkVersion']}, expected {JS_SDK_VERSION}")
    if len(out["cases"]) != len(specs):
        raise SystemExit("JS reference returned an incomplete scenario list")
    return out["cases"]


def ready_events(events):
    projected = []
    for event in events:
        if event["type"] != "ready":
            raise SystemExit(f"unexpected event during context creation: {event}")
        exps = [{"id": e["id"], "name": e["name"]} for e in event["data"]["experiments"]]
        projected.append({"type": "ready", "data": {"experiments": exps}})
    return projected


def refresh_events(events):
    projected = []
    for event in events:
        if event["type"] != "refresh":
            raise SystemExit(f"unexpected event during refresh: {event['type']}")
        exps = [{"id": e["id"], "name": e["name"]} for e in event["data"].get("experiments", [])]
        projected.append({"type": "refresh", "data": {"experiments": exps}})
    return projected


def build_scenario(spec, ran, strip_rule_override=False):
    title, description, exps, steps = spec[0], spec[1], spec[2], spec[3]
    if ran["ready"] != {"ready": True, "failed": False, "finalized": False}:
        raise SystemExit(f"{title}: JS context not ready: {ran['ready']}")
    if len(ran["steps"]) != len(steps):
        raise SystemExit(f"{title}: JS reference returned an incomplete step list")
    create_params = {"units": UNIT, "options": {"publishDelay": -1, "refreshPeriod": 0}}
    client = spec_client(spec)
    if client is not None:
        # The orchestrator forwards this to POST /context as the `client` field so
        # the wrapper configures the SDK's application/environment for this context.
        create_params["client"] = client
    out_steps = [{
        "action": "createContextWith",
        "params": create_params,
        "expect": {"result": ran["ready"], "events": ready_events(ran["readyEvents"])},
    }]
    for (action, params), got in zip(steps, ran["steps"]):
        if action == "refresh":
            expect = {"result": got["result"], "events": refresh_events(got["events"])}
        else:
            events = got["events"]
            for event in events:
                if event["type"] != "exposure":
                    raise SystemExit(f"{title}: unexpected {event['type']} event from {action}")
            if strip_rule_override:
                # Groups that do not require the assignment-rules capability can run on SDKs
                # that have no custom assignment rules and therefore never emit the
                # `ruleOverride` exposure field (e.g. an SDK with the semver operators but not
                # rules, or the ungated re-evaluation group). Asserting `ruleOverride: false`
                # there would wrongly fail an SDK that simply omits the field, so drop it;
                # only the assignment-rules group keeps it.
                events = [
                    {**e, "data": {k: v for k, v in e["data"].items() if k != "ruleOverride"}}
                    if e["type"] == "exposure" and isinstance(e.get("data"), dict) else e
                    for e in events
                ]
            expect = {"result": got["result"], "events": events}
        out_steps.append({"action": action, "params": params, "expect": expect})
    return {"name": title, "description": description, "contextData": {"experiments": exps},
            "steps": out_steps}


def build_group(specs, marker, requires, first_number):
    titles = [s[0] for s in specs]
    if len(set(titles)) != len(titles):
        raise SystemExit(f"duplicate scenario titles in {marker}")
    group = []
    strip_rule_override = RULES_CAPABILITY not in (requires or [])
    for spec, ran in zip(specs, run_reference(specs)):
        scenario = build_scenario(spec, ran, strip_rule_override=strip_rule_override)
        scenario["name"] = f"{first_number + len(group)}{marker}{scenario['name']}"
        scenario["description"] += f" Expected results recorded from @absmartly/javascript-sdk {JS_SDK_VERSION} (javascript-sdk#68)."
        # A per-scenario `requires` (6th spec element) overrides the group default,
        # so the system-attributes group can gate only its no-environment scenario.
        scenario_requires = spec_requires(spec) if spec_requires(spec) is not None else requires
        if scenario_requires:
            scenario["requires"] = scenario_requires
        group.append(scenario)
    return group


def build_all_groups(first_number):
    ensure_reference_build()
    groups = []
    n = first_number
    for specs, marker, requires in (
        (rules_specs(), RULES_MARKER, [RULES_CAPABILITY]),
        (semver_specs(), SEMVER_MARKER, [SEMVER_CAPABILITY]),
        (reeval_specs(), REEVAL_MARKER, None),
        (sysattr_specs(), SYSATTR_MARKER, None),
    ):
        group = build_group(specs, marker, requires, n)
        groups.extend(group)
        n += len(group)
    return groups


def main():
    parser = argparse.ArgumentParser(description="Generate the 2026-11 SDK-round scenario groups.")
    parser.add_argument("--check", action="store_true", help="fail if the canonical file is stale")
    parser.add_argument("--file", default=CANONICAL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as src:
        raw = src.read()
    existing = json.loads(raw)
    kept = [s for s in existing if not any(m in s["name"] for m in GROUP_MARKERS)]
    # Number after the highest retained ID, not the count: earlier scenarios may
    # have been deleted without renumbering (e.g. #18), leaving gaps.
    groups = build_all_groups(max(int(s["name"].split(" - ", 1)[0]) for s in kept) + 1)
    updated = kept + groups
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if rendered != raw:
            print("test_scenarios_complete.json is stale: run generate_sdk_round_2026_11_scenarios.py")
            return 1
        print(f"up to date: {len(groups)} SDK-round 2026-11 scenarios")
        return 0

    with open(args.file, "w", encoding="utf-8") as out:
        out.write(rendered)
    print(f"wrote {len(updated)} scenarios ({len(groups)} SDK-round 2026-11)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
