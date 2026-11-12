"""Stable placement for independently generated, numbered scenario groups."""


def place_group(existing, markers, build):
    """Build at the existing group's first ID and replace in place, or append if absent."""
    positions = [i for i, scenario in enumerate(existing)
                 if any(marker in scenario["name"] for marker in markers)]
    if positions:
        start, stop = positions[0], positions[-1] + 1
        if positions != list(range(start, stop)):
            raise ValueError(f"scenario group {markers!r} has non-contiguous markers")
        first_number = int(existing[start]["name"].split(" - ", 1)[0])
    else:
        start = stop = len(existing)
        # Retained IDs can have gaps left by deleted scenarios.
        first_number = max((int(s["name"].split(" - ", 1)[0]) for s in existing), default=0) + 1

    group = build(first_number)
    if group and stop < len(existing):
        next_scenario = existing[stop]["name"]
        if first_number + len(group) - 1 >= int(next_scenario.split(" - ", 1)[0]):
            raise ValueError(
                f"scenario group {markers!r} would collide with {next_scenario!r}. "
                "Regenerate the following group(s) after explicitly making room for this group; "
                "retained scenarios will not be silently renumbered."
            )
    return existing[:start] + group + existing[stop:], group
