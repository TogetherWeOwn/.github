#!/usr/bin/env python3
"""Tests for scripts/sync-labels.sh against a fake `gh` on PATH.

Stdlib unittest; run with: python3 -I .github/scripts/test_sync_labels.py
The fake records every invocation, reports `bug` as existing and every
other label as missing, and fails if the script ever issues a DELETE.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync-labels.sh"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
case "$*" in
  *" -X DELETE"*) exit 9 ;;
  "api repos/acme/broken/labels/"*) echo "gh: Bad credentials (HTTP 401)" >&2; exit 1 ;;
  "api repos/acme/widgets/labels/bug") exit 0 ;;
  "api repos/acme/widgets/labels/"*) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;
  *) exit 0 ;;
esac
"""


class SyncLabelsTest(unittest.TestCase):
    def run_script(self, *args):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bindir = Path(tmp.name) / "bin"
        bindir.mkdir()
        fake = bindir / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        log = Path(tmp.name) / "gh.log"
        log.touch()
        env = dict(os.environ, GH=str(fake), FAKE_GH_LOG=str(log))
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env, timeout=60,
        )
        return result, log.read_text().splitlines()

    def test_creates_missing_updates_existing_never_deletes(self):
        result, calls = self.run_script("acme/widgets")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("DELETE" in c for c in calls), calls)
        patches = [c for c in calls if "-X PATCH" in c]
        posts = [c for c in calls if "-X POST" in c]
        self.assertEqual(len(patches), 1, calls)  # only `bug` exists in the fake
        self.assertIn("labels/bug", patches[0])
        self.assertEqual(len(posts), 11, calls)  # other 11 core labels created
        joined = "\n".join(posts)
        for name in ("security", "dependencies", "ci", "wontfix", "question"):
            self.assertIn(f"name={name}", joined)

    def test_dry_run_makes_no_writes(self):
        result, calls = self.run_script("--dry-run", "acme/widgets")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("-X POST" in c or "-X PATCH" in c for c in calls), calls)
        self.assertIn("dry-run:", result.stdout)

    def test_lookup_errors_are_failures_not_creates(self):
        result, calls = self.run_script("acme/broken")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(any("-X POST" in c or "-X PATCH" in c for c in calls), calls)
        self.assertIn("FAIL (lookup)", result.stderr)

    def test_requires_a_repo_argument(self):
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
