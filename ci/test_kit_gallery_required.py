"""Offline contract for the inert, unqualified gallery workflow fixture."""

import copy
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "ci/gallery-source/kit-gallery-required.yml.in"
REFUSAL = (
    "printf '%s\\n' 'Gallery supervisor and evidence qualification are not implemented.'\n"
    "exit 1\n"
)


spec = importlib.util.spec_from_file_location(
    "kit_gallery_subject", Path(__file__).with_name("kit_gallery_subject.py")
)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class SubjectTest(unittest.TestCase):
    def setUp(self):
        self.api = {
            "number": 7,
            "state": "open",
            "base": {"repo": {"id": 10}, "sha": "a" * 40, "ref": "main"},
            "head": {"repo": {"id": 20}, "sha": "b" * 40, "ref": "feature"},
        }
        self.event = {
            "number": 7,
            "repository": {"id": 10},
            "pull_request": copy.deepcopy(self.api),
        }

    def test_matching_fork_identity(self):
        self.assertEqual(
            subject.resolve_pull_request(self.event, self.api),
            {
                "number": 7,
                "base_repository_id": 10,
                "base_sha": "a" * 40,
                "base_ref": "main",
                "head_repository_id": 20,
                "head_sha": "b" * 40,
                "head_ref": "feature",
            },
        )

    def test_authenticated_fetch_is_bounded_and_reconciled(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(self.api).encode()
        with mock.patch.object(subject.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            self.assertEqual(
                subject._fetch_metadata(10, 7, "token"), response.read.return_value
            )
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(
                request.full_url, "https://api.github.com/repositories/10/pulls/7"
            )
            self.assertEqual(request.get_header("Authorization"), "Bearer token")
            self.assertIs(build.call_args.args[0], subject._NoRedirect)
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 10)
            response.read.assert_called_once_with(subject._MAX_RESPONSE_BYTES + 1)
        self.assertIsNone(
            subject._NoRedirect().redirect_request(
                None, None, 302, None, None, "https://other.example"
            )
        )

    def test_fetch_refuses_failures_and_invalid_input(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        with mock.patch.object(subject.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            response.read.return_value = b"x" * (subject._MAX_RESPONSE_BYTES + 1)
            with self.assertRaises(subject.SubjectError):
                subject._fetch_metadata(10, 7, "token")
            response.read.side_effect = subject.http.client.IncompleteRead(b"partial")
            with self.assertRaises(subject.SubjectError):
                subject._fetch_metadata(10, 7, "token")
            response.read.side_effect = None
            response.status = 302
            with self.assertRaises(subject.SubjectError):
                subject._fetch_metadata(10, 7, "token")
            for error in (subject.urllib.error.URLError("refused"), TimeoutError()):
                build.return_value.open.side_effect = error
                with self.assertRaises(subject.SubjectError):
                    subject._fetch_metadata(10, 7, "token")
        with mock.patch.object(subject.urllib.request, "build_opener") as build:
            for token in ("", "bad\ntoken", None, "é"):
                with self.subTest(token=token), self.assertRaises(subject.SubjectError):
                    subject.fetch_pull_request_subject(self.event, token)
            with self.assertRaises(subject.SubjectError):
                subject.fetch_pull_request_subject(
                    {"repository": {"id": True}, "number": 7}, "token"
                )
            build.assert_not_called()

    def test_fetch_supervisor_reconciles_and_refuses_bad_results(self):
        with mock.patch.object(subject.subprocess, "run") as run:
            run.return_value.stdout = json.dumps(self.api).encode()
            self.assertEqual(
                subject.fetch_pull_request_subject(self.event, "token"),
                subject.resolve_pull_request(self.event, self.api),
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 10)
            self.assertNotIn("token", run.call_args.args[0])
            for data in (b"invalid", b"[" * 2000 + b"]" * 2000, b"{}"):
                run.return_value.stdout = data
                with self.assertRaises(subject.SubjectError):
                    subject.fetch_pull_request_subject(self.event, "token")
            run.side_effect = subprocess.TimeoutExpired("worker", 10)
            with self.assertRaises(subject.SubjectError):
                subject.fetch_pull_request_subject(self.event, "token")

    def test_stale_or_mismatched_identities_refused(self):
        for path, value in (
            (("number",), 8),
            (("state",), "closed"),
            (("base", "repo", "id"), 11),
            (("head", "repo", "id"), 21),
            (("base", "sha"), "c" * 40),
            (("head", "sha"), "c" * 40),
            (("base", "ref"), "release"),
            (("head", "ref"), "other"),
        ):
            with self.subTest(path=path):
                api = copy.deepcopy(self.api)
                target = api
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(subject.SubjectError):
                    subject.resolve_pull_request(self.event, api)
        for key, value in (("number", 8), ("repository", {"id": 11})):
            event = {**self.event, key: value}
            with self.subTest(key=key), self.assertRaises(subject.SubjectError):
                subject.resolve_pull_request(event, self.api)

    def test_malformed_identity_refused(self):
        for path, values in (
            (("number",), (True, 0, -1, "7", None)),
            (("head", "repo", "id"), (False, 0, "20", None)),
            (("base", "sha"), (None, "a" * 39, "A" * 40, "g" * 40)),
            (("base", "ref"), (None, "", 7)),
            (("head", "repo"), (None, {})),
        ):
            for value in values:
                with self.subTest(path=path, value=value):
                    api = copy.deepcopy(self.api)
                    target = api
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = value
                    with self.assertRaises(subject.SubjectError):
                        subject.resolve_pull_request(self.event, api)
        for ref in (
            "bad ref",
            "a..b",
            "a//b",
            ".hidden",
            "a.lock",
            "a@{b",
            "a\\b",
            "-feature",
        ):
            with self.subTest(ref=ref), self.assertRaises(subject.SubjectError):
                api = copy.deepcopy(self.api)
                api["base"]["ref"] = ref
                subject.resolve_pull_request({**self.event, "pull_request": api}, api)
        for side in ("base", "head"):
            with self.subTest(side=side), self.assertRaises(subject.SubjectError):
                api = copy.deepcopy(self.api)
                api[side]["ref"] = "HEAD"
                subject.resolve_pull_request({**self.event, "pull_request": api}, api)
        for event in (None, {}, {**self.event, "pull_request": None}):
            with self.subTest(event=event), self.assertRaises(subject.SubjectError):
                subject.resolve_pull_request(event, self.api)


class GalleryPreparationTest(unittest.TestCase):
    def test_fixture_is_inert_and_declares_refusal_contract(self):
        self.assertFalse((ROOT / ".github/workflows/kit-gallery-required.yml").exists())
        workflow = yaml.load(FIXTURE.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(
            workflow["on"],
            {
                "pull_request": {"types": ["opened", "synchronize", "reopened"]},
                "merge_group": {"types": ["checks_requested"]},
            },
        )
        self.assertEqual(workflow["permissions"], {})
        self.assertEqual(set(workflow["jobs"]), {"kit-gallery"})
        job = workflow["jobs"]["kit-gallery"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], "15")
        self.assertEqual(
            job["steps"],
            [{"name": "Refuse unqualified gallery evidence", "run": REFUSAL}],
        )

    def test_refusal_is_not_a_successful_gallery_result(self):
        workflow = yaml.load(FIXTURE.read_text(), Loader=yaml.BaseLoader)
        script = workflow["jobs"]["kit-gallery"]["steps"][0]["run"]
        self.assertEqual(script, REFUSAL)
        result = subprocess.run(
            ["bash", "-e", "-c", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not implemented", result.stdout)


if __name__ == "__main__":
    unittest.main()
