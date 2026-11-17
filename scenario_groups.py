"""Group markers of the generated scenario groups in test_scenarios_complete.json.

One definition for both generators and their tests: generate_sdk_round_2026_11_scenarios.py owns the
ROUND groups (230-253) and generate_parity_round_scenarios.py the PARITY groups (300+), which stay
after the round groups.
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
