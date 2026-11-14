"""Offline tests of lifecycle report validation, not runtime cleanup."""

import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "kit_gallery_lifecycle", Path(__file__).with_name("kit_gallery_lifecycle.py")
)
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)
LifecycleError = lifecycle.LifecycleError
check_lifecycle = lifecycle.check_lifecycle


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.report = {
            "conclusion": "success",
            "overall_deadline_seconds": 850,
            "phases": {
                name: {"outcome": "success", "deadline_seconds": bound}
                for name, bound in (
                    ("readiness", 30),
                    ("build", 240),
                    ("capture", 240),
                    ("lhci", 240),
                )
            },
            "child_cleanup": "direct-child-reaped",
            "child_grace_seconds": 5,
            "child_force_seconds": 5,
            "browser_cleanup": "closed",
            "whole_workload_disposal": "delegated-to-github",
        }

    def refused(self, report):
        with self.assertRaises(LifecycleError):
            check_lifecycle(report)

    def test_complete_report(self):
        self.assertIsNone(check_lifecycle(self.report))

    def test_attempt_and_phase_failures(self):
        for status in ("failure", "timeout", "cancelled", "pending", None, True):
            with self.subTest(status=status):
                self.refused(dict(self.report, conclusion=status))
                for name in self.report["phases"]:
                    report = copy.deepcopy(self.report)
                    report["phases"][name]["outcome"] = status
                    self.refused(report)

    def test_conclusion_type_checked_before_comparison(self):
        class Hostile:
            def __ne__(self, other):
                raise AssertionError("unexpected comparison")

        self.refused(dict(self.report, conclusion=Hostile()))

    def test_missing_extra_and_malformed_fields(self):
        for key in self.report:
            report = dict(self.report)
            del report[key]
            self.refused(report)
        for value in (None, [], {}, dict(self.report, extra=True)):
            self.refused(value)
        for name in self.report["phases"]:
            for bad in (
                None,
                {},
                {"outcome": "success"},
                {"outcome": "success", "deadline_seconds": 1, "extra": 1},
            ):
                report = copy.deepcopy(self.report)
                report["phases"][name] = bad
                self.refused(report)
            report = copy.deepcopy(self.report)
            del report["phases"][name]
            self.refused(report)

    def test_deadlines_and_cleanup(self):
        for key in (
            "overall_deadline_seconds",
            "child_grace_seconds",
            "child_force_seconds",
        ):
            for bad in (None, True, 0, -1, 900, 1.5, "5"):
                self.refused(dict(self.report, **{key: bad}))
        for name in self.report["phases"]:
            for bad in (None, True, 0, -1, 241, 1.5, "30"):
                report = copy.deepcopy(self.report)
                report["phases"][name]["deadline_seconds"] = bad
                self.refused(report)
        report = copy.deepcopy(self.report)
        report["phases"]["readiness"]["deadline_seconds"] = 31
        self.refused(report)
        self.refused(dict(self.report, overall_deadline_seconds=10))
        for key in ("child_cleanup", "browser_cleanup", "whole_workload_disposal"):
            for bad in (None, "unknown", "failed", "verified", "all-descendants-gone"):
                self.refused(dict(self.report, **{key: bad}))


if __name__ == "__main__":
    unittest.main()
