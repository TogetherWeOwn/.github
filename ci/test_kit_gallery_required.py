"""Offline contract for the inert, unqualified gallery workflow fixture."""

import copy
import importlib.util
import subprocess
import unittest
from pathlib import Path

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
