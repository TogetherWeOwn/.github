"""Offline tests of expected policy, not evidence of a running database."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "kit_gallery_database_policy",
    Path(__file__).with_name("kit_gallery_database_policy.py"),
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class DatabasePolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "image": "postgres:17@sha256:" + "a" * 64,
            "network": "attempt-owned-internal",
            "storage": "disposable",
            "host_network": False,
            "published_ports": [],
        }

    def test_expected_policy(self):
        self.assertIsNone(fixture.check_database_policy(self.policy))

    def test_image_must_be_an_exact_digest_pin(self):
        for image in (
            None,
            "postgres:17",
            "postgres:16@sha256:" + "a" * 64,
            "postgres:17@sha256:" + "a" * 63,
            "postgres:17@sha256:" + "g" * 64,
            "postgres:17@sha256:" + "A" * 64,
            self.policy["image"] + "\n",
            "other/postgres:17@sha256:" + "a" * 64,
        ):
            with (
                self.subTest(image=image),
                self.assertRaises(fixture.DatabasePolicyError),
            ):
                fixture.check_database_policy({**self.policy, "image": image})

    def test_missing_extra_and_malformed_controls_refused(self):
        for key in self.policy:
            candidate = dict(self.policy)
            del candidate[key]
            with (
                self.subTest(missing=key),
                self.assertRaises(fixture.DatabasePolicyError),
            ):
                fixture.check_database_policy(candidate)
        for candidate in (None, [], {}, {**self.policy, "endpoint": "db"}):
            with (
                self.subTest(candidate=candidate),
                self.assertRaises(fixture.DatabasePolicyError),
            ):
                fixture.check_database_policy(candidate)

    def test_unsafe_or_ambiguous_controls_refused(self):
        cases = {
            "network": [
                None,
                "host",
                "bridge",
                "external",
                "attempt-owned-internal ",
                [],
            ],
            "storage": [None, "persistent", "host-bind", "named-volume", False],
            "host_network": [True, 0, None, "false"],
            "published_ports": [
                None,
                False,
                {},
                [5432],
                ["127.0.0.1:5432"],
                ["0.0.0.0:5432"],
            ],
        }
        for key, values in cases.items():
            for value in values:
                with (
                    self.subTest(key=key, value=value),
                    self.assertRaises(fixture.DatabasePolicyError),
                ):
                    fixture.check_database_policy({**self.policy, key: value})


if __name__ == "__main__":
    unittest.main()
