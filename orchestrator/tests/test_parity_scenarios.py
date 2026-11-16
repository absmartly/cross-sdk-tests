"""Feature-parity groups of test_scenarios_complete.json (300-326, generate_parity_round_scenarios.py).

Their expectations are recorded from the pinned javascript-sdk reference, and only
`generate_parity_round_scenarios.py --check` (which needs that build) re-derives them. This test pins
them in CI without the reference: a hand edit to any parity scenario (an expected variant, an
exposure flag, a weakened `events` list, a dropped step) changes the digest.

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

FIRST, LAST = 300, 326
# sha256 of json.dumps([parity scenarios in file order], sort_keys=True).
PARITY_DIGEST = "d2806931c30ceebc745b9b6c131055bda1597cc0a16804559fc0f941794adeb2"


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
        self.assertEqual(self.everything[-len(self.parity):], self.parity,
                         "the parity groups are the tail of the file")

    def test_parity_scenarios_unchanged(self):
        self.assertEqual(digest(self.parity), PARITY_DIGEST,
                         "a parity scenario (300-326) changed without regenerating it from the reference")

    def test_digest_catches_hand_edits(self):
        """The edits that used to pass every CI check change the digest."""
        by_number = {int(s["name"].split(" - ", 1)[0]): s for s in self.parity}

        def edited(number, edit):
            copy = json.loads(json.dumps(self.parity))
            edit(next(s for s in copy if s["name"] == by_number[number]["name"]))
            return digest(copy)

        def change_result(scenario):
            step = next(st for st in scenario["steps"] if st["action"] == "treatment")
            step["expect"]["result"] = 1 - step["expect"]["result"]

        def flip_assigned(scenario):
            step = next(st for st in scenario["steps"] if st["action"] == "treatment")
            data = step["expect"]["events"][0]["data"]
            data["assigned"] = not data["assigned"]

        def weaken_events(scenario):
            for st in scenario["steps"]:
                st["expect"]["events"] = [{"type": e["type"]} for e in st["expect"].get("events", [])]

        for number, edit in ((305, change_result), (316, change_result), (300, flip_assigned),
                             (305, weaken_events)):
            with self.subTest(number=number, edit=edit.__name__):
                self.assertNotEqual(edited(number, edit), PARITY_DIGEST)


if __name__ == "__main__":
    unittest.main()
