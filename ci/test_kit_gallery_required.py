"""Offline contract for the inert, unqualified gallery workflow fixture."""

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
