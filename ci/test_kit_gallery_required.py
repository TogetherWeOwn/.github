"""Guard the source-preparation entry point until its supervisor is implemented."""

import unittest
from pathlib import Path


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
