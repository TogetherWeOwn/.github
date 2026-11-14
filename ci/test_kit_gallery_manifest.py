"""Offline tests of the inert trusted-source policy contract."""

import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "kit_gallery_manifest", Path(__file__).with_name("kit_gallery_manifest.py")
)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)
ManifestError, check_manifest = manifest.ManifestError, manifest.check_manifest


def valid_manifest():
    return {
        "repository": "example/workflows",
        "path": ".github/workflows/gallery.yml",
        "sha": "a" * 40,
        "pins": [
            {"name": "actions/checkout", "kind": "git", "digest": "b" * 40},
            {"name": "images/runner", "kind": "sha256", "digest": "sha256:" + "c" * 64},
        ],
    }


class ManifestTests(unittest.TestCase):
    def test_explicit_immutable_policy(self):
        self.assertIsNone(check_manifest(valid_manifest()))

    def test_missing_unknown_or_wrong_shape(self):
        for candidate in (None, [], {}, {**valid_manifest(), "extra": True}):
            with self.subTest(candidate=candidate), self.assertRaises(ManifestError):
                check_manifest(candidate)
        for key in valid_manifest():
            candidate = valid_manifest()
            del candidate[key]
            with self.subTest(key=key), self.assertRaises(ManifestError):
                check_manifest(candidate)

    def test_source_identity_refusals(self):
        for key, values in {
            "repository": [None, "", "owner", "owner/repo/extra", "owner/..", "https://x/y", "_invalid/repo", "x--y/repo", "a" * 40 + "/repo"],
            "path": [None, "", "/absolute", "../gallery.yml", "a/../b", "a//b", "a\\b"],
            "sha": [None, "main", "a" * 39, "A" * 40, "0" * 40, "a" * 40 + "\n"],
        }.items():
            for value in values:
                candidate = valid_manifest()
                candidate[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ManifestError):
                    check_manifest(candidate)

    def test_transitive_pin_refusals(self):
        pins = valid_manifest()["pins"]
        for invalid in (None, [], {}, [None], [{}],
                        [{**pins[0], "kind": "tag"}],
                        [{**pins[0], "digest": "main"}],
                        [{**pins[0], "digest": "0" * 40}],
                        [{**pins[1], "digest": "sha256:" + "0" * 64}],
                        [{**pins[1], "digest": "latest"}],
                        [{**pins[0], "name": "../tool"}],
                        [pins[0], copy.deepcopy(pins[0])],
                        [pins[0], {**pins[0], "name": "Actions/Checkout", "digest": "d" * 40}]):
            candidate = valid_manifest()
            candidate["pins"] = invalid
            with self.subTest(pins=invalid), self.assertRaises(ManifestError):
                check_manifest(candidate)


if __name__ == "__main__":
    unittest.main()
