"""Guard the source-preparation entry point until its supervisor is implemented."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

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

    def test_authenticated_fetch_binds_numeric_repository_and_pr(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = json.dumps(self.api).encode()
        with mock.patch.object(subject.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            self.assertEqual(
                subject.fetch_pull_request_subject(self.event, "token"),
                subject.resolve_pull_request(self.event, self.api),
            )
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(
                request.full_url, "https://api.github.com/repositories/10/pulls/7"
            )
            self.assertEqual(request.get_header("Authorization"), "Bearer token")
            self.assertIs(build.call_args.args[0], subject._NoRedirect)
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 10)

    def test_fetch_refuses_invalid_or_stale_api_data(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        with mock.patch.object(subject.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            for data in (
                b"invalid",
                b"x" * (subject._MAX_RESPONSE_BYTES + 1),
                json.dumps({**self.api, "state": "closed"}).encode(),
            ):
                with self.subTest(length=len(data)):
                    response.read.return_value = data
                    with self.assertRaises(subject.SubjectError):
                        subject.fetch_pull_request_subject(self.event, "token")
            with self.assertRaises(subject.SubjectError):
                subject.fetch_pull_request_subject(self.event, "bad\ntoken")
            build.return_value.open.side_effect = subject.urllib.error.URLError(
                "refused"
            )
            with self.assertRaises(subject.SubjectError):
                subject.fetch_pull_request_subject(self.event, "token")

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
