"""Accept-Encoding negotiation for the context server's GET /context.

Stdlib only. The single source of these rules: imported by the context server
(context-server/server.py) and by the orchestrator (orchestrator/test_runner.py;
its image copies this file next to test_runner.py), so the server's choice and
the orchestrator's reading of a recorded request cannot drift apart.

The server produces gzip or identity only. br and zstd are never served: the
orchestrator only reports whether an SDK's Accept-Encoding lists them.

Parsing choices (RFC 9110 section 12.5.3 leaves some of these open):
  * codings are case-insensitive and surrounding whitespace is ignored;
  * `x-gzip` is an alias of `gzip`;
  * a malformed qvalue counts as 0 (refused); qvalues are not range-checked;
  * a coding listed twice keeps its last qvalue;
  * several Accept-Encoding header lines are one comma-joined list
    (see `combine_header_lines`).
"""

from typing import Dict, Iterable, Optional, Sequence

# What the context server can serve, in its default preference order.
CODINGS = ("gzip", "identity")
DEFAULT_PREFERENCE = ["gzip", "identity"]
ALIASES = {"x-gzip": "gzip"}


def settings_error(settings: dict) -> Optional[str]:
    """The problem with the `encodings`/`force` keys of a payload or scenario spec, else None."""
    if "encodings" in settings:
        encodings = settings["encodings"]
        if not isinstance(encodings, list) or not encodings or any(e not in CODINGS for e in encodings):
            return f"'encodings' must be a non-empty list drawn from {list(CODINGS)}"
    force = settings.get("force")
    if force is not None and force not in CODINGS:
        return f"'force' must be null or one of {list(CODINGS)}"
    return None


def combine_header_lines(lines: Optional[Iterable[str]]) -> Optional[str]:
    """Join repeated Accept-Encoding header lines; None when there were none."""
    lines = list(lines or [])
    return ", ".join(lines) if lines else None


def parse_accept_encoding(header: Optional[str]) -> Dict[str, float]:
    """{coding: q} for an Accept-Encoding value (None or "" -> {})."""
    accepted: Dict[str, float] = {}
    for part in (header or "").split(","):
        coding, *params = [p.strip() for p in part.split(";")]
        if not coding:
            continue
        q = 1.0
        for param in params:
            if param.lower().startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        coding = coding.lower()
        accepted[ALIASES.get(coding, coding)] = q
    return accepted


def advertises(header: Optional[str], coding: str) -> bool:
    """True when the header lists `coding` itself with q > 0 (`*` does not count)."""
    q = parse_accept_encoding(header).get(coding)
    return q is not None and q > 0


def acceptable(header: Optional[str], coding: str) -> bool:
    """RFC 9110 acceptability: the coding's own q, else `*`, else identity only."""
    accepted = parse_accept_encoding(header)
    q = accepted.get(coding)
    if q is not None:
        return q > 0
    if "*" in accepted:
        return accepted["*"] > 0
    return coding == "identity"


def choose_encoding(header: Optional[str], preference: Sequence[str] = DEFAULT_PREFERENCE,
                    force: Optional[str] = None) -> str:
    """The served coding: `force` if set, else the first preferred acceptable one.

    When nothing is acceptable (`identity;q=0` with no other coding, or `*;q=0`)
    this deliberately falls back to identity instead of answering 406/415: RFC
    9110 allows either, and an SDK that refuses everything should still be
    measured on what it does with the body rather than on an error status.
    """
    if force:
        return force
    for coding in preference:
        if coding in CODINGS and acceptable(header, coding):
            return coding
    return "identity"
