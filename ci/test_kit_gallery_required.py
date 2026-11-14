"""Guard the source-preparation entry point until its supervisor is implemented."""

import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "kit_gallery_subject", Path(__file__).with_name("kit_gallery_subject.py")
)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def pull():
    return {
        "number": 7,
        "state": "open",
        "base": {"repo": {"id": 10}, "sha": "a" * 40},
        "head": {"repo": {"id": 20}, "sha": "b" * 40},
    }


class SubjectTest(unittest.TestCase):
    def setUp(self):
        self.api = pull()
        self.event = {
            "number": 7,
            "repository": {"id": 10},
            "pull_request": copy.deepcopy(self.api),
        }

    def test_fork_identity_must_match_current_api(self):
        self.assertEqual(
            subject.resolve_pull_request(self.event, self.api)["head_sha"], "b" * 40
        )
        self.api["head"]["sha"] = "c" * 40
        with self.assertRaises(subject.SubjectError):
            subject.resolve_pull_request(self.event, self.api)

    def test_mismatched_repository_number_and_closed_pull_refused(self):
        for field in ("repository", "number", "state"):
            with self.subTest(field=field):
                event, api = copy.deepcopy(self.event), copy.deepcopy(self.api)
                if field == "repository":
                    event["repository"]["id"] = 11
                elif field == "number":
                    event["number"] = 8
                else:
                    api["state"] = "closed"
                with self.assertRaises(subject.SubjectError):
                    subject.resolve_pull_request(event, api)

    def test_invalid_or_missing_identity_refused(self):
        for bad in (None, "b" * 39, "B" * 40, "g" * 40):
            with self.subTest(sha=bad):
                self.api["head"]["sha"] = bad
                with self.assertRaises(subject.SubjectError):
                    subject.resolve_pull_request(self.event, self.api)
        self.api = pull()
        self.api["head"]["repo"] = None
        with self.assertRaises(subject.SubjectError):
            subject.resolve_pull_request(self.event, self.api)


class GalleryPreparationTest(unittest.TestCase):
    def test_unimplemented_entry_point_refuses_both_events(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/kit-gallery-required.yml"
        ).read_text()
        self.assertIn("  pull_request:\n", workflow)
        self.assertIn("  merge_group:\n    types: [checks_requested]\n", workflow)
        self.assertIn("    runs-on: ubuntu-24.04\n", workflow)
        self.assertIn("    timeout-minutes: 15\n", workflow)
        self.assertIn("permissions: {}\n", workflow)
        self.assertIn("          exit 1\n", workflow)
        self.assertNotIn("uses:", workflow)
        self.assertNotIn("continue-on-error", workflow)


if __name__ == "__main__":
    unittest.main()
