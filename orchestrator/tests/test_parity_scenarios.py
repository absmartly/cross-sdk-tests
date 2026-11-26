"""Feature-parity groups of test_scenarios_complete.json (300-332, generate_parity_round_scenarios.py).

Their expectations are recorded from the pinned javascript-sdk reference, and only
`generate_parity_round_scenarios.py --check` (which needs that build) re-derives them. This test pins
them in CI without the reference: a hand edit to any parity scenario (an expected variant, an
exposure flag, a weakened `events` list, a dropped step or `requires`) fails
`test_parity_scenarios_unchanged`.

Update PARITY_DIGEST ONLY together with a regeneration: run the generator against the pinned
javascript-sdk checkout, confirm `--check` is clean, then paste the digest this test reports.
"""

import hashlib
import json
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIOS_PATH = os.path.join(REPO_ROOT, "test_scenarios_complete.json")

sys.path.insert(0, REPO_ROOT)
import scenario_groups  # noqa: E402

FIRST, LAST = 300, 332
# sha256 of json.dumps([parity scenarios in file order], sort_keys=True).
PARITY_DIGEST = "56b70c02fa1b00a2d7571a2b7d3a27b7ff5d4bc3cf02b260f600873dc01a9248"


def parity_scenarios(scenarios):
    return [s for s in scenarios if any(m in s["name"] for m in scenario_groups.PARITY_MARKERS)]


def digest(scenarios):
    return hashlib.sha256(json.dumps(scenarios, sort_keys=True).encode("utf-8")).hexdigest()


class ParityGroupsPinnedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SCENARIOS_PATH, encoding="utf-8") as f:
            cls.everything = json.load(f)
        cls.parity = parity_scenarios(cls.everything)

    def test_numbering(self):
        numbers = [int(re.match(r"^(\d+) - ", s["name"]).group(1)) for s in self.parity]
        self.assertEqual(numbers, list(range(FIRST, LAST + 1)))
        later = (scenario_groups.SHARED_VARIABLE_KEY, scenario_groups.SWITCHBACK, scenario_groups.HOLDOUT_CONFORMANCE)
        rest = [s for s in self.everything if not any(m in s["name"] for m in later)]
        self.assertEqual(rest[-len(self.parity):], self.parity,
                         "the parity groups are the tail of the file, before the Shared Variable Key, "
                         "Switchback and Holdout Conformance groups")

    def test_parity_scenarios_unchanged(self):
        self.assertEqual(digest(self.parity), PARITY_DIGEST,
                         "a parity scenario (300-332) changed without regenerating it from the reference")


if __name__ == "__main__":
    unittest.main()
