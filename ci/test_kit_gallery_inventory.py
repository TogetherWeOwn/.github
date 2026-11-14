"""Offline tests for the inert collector inventory contract."""

import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "kit_gallery_inventory", Path(__file__).with_name("kit_gallery_inventory.py")
)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)
InventoryError = inventory.InventoryError
check_inventory = inventory.check_inventory


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.required = [
            {"state": "default", "viewport": "desktop"},
            {"state": "menu", "viewport": "mobile"},
        ]
        self.outcome = {
            "status": "success",
            "results": [dict(entry, status="success") for entry in self.required],
        }

    def test_complete_inventory_is_order_independent(self):
        self.outcome["results"].reverse()
        self.assertIsNone(check_inventory(self.required, self.outcome))

    def test_missing_duplicate_unexpected_or_failed_results(self):
        results = self.outcome["results"]
        cases = [
            [], results[:1], results + results[:1],
            results + [{"state": "other", "viewport": "desktop", "status": "success"}],
            [dict(results[0], status="failure"), results[1]],
            [dict(results[0], status="partial"), results[1]],
        ]
        for case in cases:
            with self.subTest(results=case), self.assertRaises(InventoryError):
                check_inventory(self.required, dict(self.outcome, results=case))

    def test_bad_outcomes_and_candidate_selected_inventory(self):
        for outcome in (None, {}, {"status": "success", "results": []},
                        dict(self.outcome, status="partial"),
                        dict(self.outcome, status="failure"),
                        dict(self.outcome, status="cancelled"),
                        dict(self.outcome, required=self.required),
                        dict(self.outcome, results=None)):
            with self.subTest(outcome=outcome), self.assertRaises(InventoryError):
                check_inventory(self.required, outcome)

    def test_invalid_trusted_requirements(self):
        for required in (None, [], [self.required[0], self.required[0]],
                         [{"state": "", "viewport": "mobile"}],
                         [{"state": "x", "viewport": True}],
                         [{"state": " x", "viewport": "mobile"}],
                         [{"state": "x"}], [{"state": "x", "viewport": "y", "extra": 1}]):
            with self.subTest(required=required), self.assertRaises(InventoryError):
                check_inventory(required, self.outcome)

    def test_malformed_results_are_refused(self):
        for result in (None, {}, {"state": "default", "viewport": "desktop"},
                       dict(self.outcome["results"][0], extra=True),
                       dict(self.outcome["results"][0], state=[]),
                       dict(self.outcome["results"][0], viewport=" ")):
            outcome = copy.deepcopy(self.outcome)
            outcome["results"][0] = result
            with self.subTest(result=result), self.assertRaises(InventoryError):
                check_inventory(self.required, outcome)


if __name__ == "__main__":
    unittest.main()
