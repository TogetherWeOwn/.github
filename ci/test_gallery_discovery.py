"""Exercise the CI gallery discovery command using offline fixture tests."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"


class GalleryDiscoveryTest(unittest.TestCase):
    def test_discovery_includes_lifecycle_and_propagates_failure(self):
        workflow = WORKFLOW.read_text()
        step = workflow.split("      - name: Offline gallery contracts\n", 1)[1]
        command = step.splitlines()[0].removeprefix("        run: ")
        self.assertNotEqual(command, step.splitlines()[0])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ci = root / "ci"
            ci.mkdir()
            required = ci / "test_kit_gallery_required.py"
            lifecycle = ci / "test_kit_gallery_lifecycle.py"
            required.write_text("import unittest\nclass Required(unittest.TestCase):\n def test_required(self): pass\n")
            lifecycle.write_text("import unittest\nclass Lifecycle(unittest.TestCase):\n def test_lifecycle(self): pass\n")

            def run():
                return subprocess.run(
                    ["bash", "-e", "-c", command], cwd=root,
                    text=True, capture_output=True, timeout=30, check=False,
                )

            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("test_required", result.stderr)
            self.assertIn("test_lifecycle", result.stderr)
            self.assertIn("Ran 2 tests", result.stderr)

            lifecycle.write_text("import unittest\nclass Lifecycle(unittest.TestCase):\n def test_lifecycle(self): self.fail('fixture failure')\n")
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fixture failure", result.stderr)

            required.unlink()
            self.assertNotEqual(run().returncode, 0)


if __name__ == "__main__":
    unittest.main()
