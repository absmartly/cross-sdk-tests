"""Group markers and stable placement of the generated scenario groups in test_scenarios_complete.json.

One definition for the generators and their tests: generate_sdk_round_2026_11_scenarios.py owns the
ROUND groups (230-253), generate_parity_round_scenarios.py the PARITY groups (300+), which stay
after the round groups, and generate_switchback_scenarios.py the SWITCHBACK group (400+), which
stays after the parity groups, and generate_context_compression_scenarios.py the CONTEXT_FETCH
group (500+, transport and context-fetch scenarios), which stays after the Switchback group.
"""

ROUND_RULES = " - Custom Assignment Rules - "
ROUND_SEMVER = " - Semver Operators - "
ROUND_REEVAL = " - Audience Re-Evaluation - "
ROUND_SYSATTR = " - System Attributes - "
ROUND_MARKERS = (ROUND_RULES, ROUND_SEMVER, ROUND_REEVAL, ROUND_SYSATTR)

PARITY_RULES = " - Assignment Rules - "
PARITY_SEMVER = " - Semver Comparison - "
PARITY_XAGENT = " - X-Agent - "
PARITY_CLIENT_APP = " - Client Application - "
PARITY_MARKERS = (PARITY_RULES, PARITY_SEMVER, PARITY_XAGENT, PARITY_CLIENT_APP)

SWITCHBACK = " - Switchback "
SWITCHBACK_FIRST_NUMBER = 400

CONTEXT_FETCH = " - Context Fetch Compression - "
CONTEXT_FETCH_FIRST_NUMBER = 500


def number(scenario):
    return int(scenario["name"].split(" - ", 1)[0])


def place_group(existing, markers, build, first_number=None):
    """Rebuild a group and return (updated scenarios, the rebuilt group).

    Without `first_number` the group keeps its existing first ID and position, or is appended after
    the highest ID if absent. With it, the group is built at `first_number` and inserted before the
    first other scenario numbered above it. Any other scenario numbered inside the rebuilt range is
    a collision: retained scenarios are never silently renumbered."""
    positions = [i for i, scenario in enumerate(existing)
                 if any(marker in scenario["name"] for marker in markers)]
    if positions:
        start, stop = positions[0], positions[-1] + 1
        if len(positions) != stop - start:
            raise ValueError(f"scenario group {markers!r} has non-contiguous markers")
    else:
        start = stop = len(existing)
    rest = existing[:start] + existing[stop:]

    if first_number is None:
        if positions:
            first_number = number(existing[start])
        else:
            # Retained IDs can have gaps left by deleted scenarios.
            first_number = max((number(s) for s in existing), default=0) + 1
    else:
        start = next((i for i, s in enumerate(rest) if number(s) > first_number), len(rest))

    group = build(first_number)
    if group:
        last = first_number + len(group) - 1
        clash = next((s["name"] for s in rest if first_number <= number(s) <= last), None)
        if clash is not None:
            raise ValueError(
                f"scenario group {markers!r} would collide with {clash!r}. "
                "Regenerate the following group(s) after explicitly making room for this group; "
                "retained scenarios will not be silently renumbered."
            )
    return rest[:start] + group + rest[start:], group
