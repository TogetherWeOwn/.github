#!/usr/bin/env python3
"""Workflow contracts; YAML syntax is additionally checked with pinned actionlint."""

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REUSABLE = ROOT / ".github/workflows/pr-attestation.yml"
WRAPPER = ROOT / ".github/workflows/pr-attestation-required.yml"


class WorkflowTests(unittest.TestCase):
    def test_reusable_executes_only_pinned_org_policy(self):
        text = REUSABLE.read_text()
        self.assertIn("repository: TogetherWeOwn/.github", text)
        self.assertRegex(text, r"(?m)^          ref: [a-f0-9]{40}$")
        self.assertIn("persist-credentials: false", text)
        self.assertEqual(text.count("uses: actions/checkout@"), 1)
        self.assertIn('python3 -I "$GITHUB_WORKSPACE/attestation-policy/.github/scripts/pr_attestation.py"', text)
        self.assertNotIn("inputs.mode", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("github.event.pull_request.body", text)
        self.assertIn("pull-requests: read", text)
        self.assertIn("contents: read", text)
        self.assertNotIn(": write", text)

    def test_every_action_and_reusable_ref_fully_pinned(self):
        for path in (REUSABLE, WRAPPER):
            for ref in re.findall(r"(?m)^\s*(?:- )?uses: (\S+)", path.read_text()):
                self.assertRegex(ref, r"^[A-Za-z0-9_./-]+@[a-f0-9]{40}$")

    def test_entry_point_events_and_bare_check(self):
        text = WRAPPER.read_text()
        self.assertIn("  pull_request:", text)
        self.assertIn("  merge_group: {}", text)
        self.assertIn("ready_for_review, edited]", text)
        self.assertIn("  pr-attestation:\n    name: pr-attestation", text)
        self.assertIn("    needs: [observation]\n    if: always()", text)
        self.assertNotIn("paths:", text)
        self.assertNotIn("inputs.mode", text)

    def test_reusable_checkout_runtime_failures_are_shadow(self):
        text = REUSABLE.read_text()
        self.assertGreaterEqual(text.count("continue-on-error: true"), 3)
        self.assertIn("if: always() && steps.check.outputs.verdict == ''", text)
        self.assertIn("not a clean verdict", text)

    def test_actual_wrapper_shell_warns_never_fails(self):
        script = textwrap.dedent(WRAPPER.read_text().split("        run: |\n", 1)[1])
        for result in ("success", "failure", "cancelled", "skipped"):
            for verdict in ("valid", "invalid", "error", "", "$(exit 3)"):
                with self.subTest(result=result, verdict=verdict), tempfile.TemporaryDirectory() as directory:
                    summary = Path(directory) / "summary"
                    run = subprocess.run(["bash", "-euo", "pipefail", "-c", script], check=False, text=True, capture_output=True,
                                         env={**os.environ, "RESULT": result, "VERDICT": verdict,
                                              "GITHUB_STEP_SUMMARY": str(summary)})
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn("No enforcement", summary.read_text())
                    if result != "success" or verdict != "valid":
                        self.assertIn("::warning::", run.stdout)
                        self.assertNotIn("**valid**", summary.read_text())

    def test_selftest_is_wired_but_not_added_to_required_aggregator(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("python3 -I .github/scripts/test_pr_attestation.py", text)
        self.assertIn("python3 -I .github/scripts/test_pr_attestation_workflows.py", text)
        self.assertIn("python3 -I .github/scripts/check_pr_attestation_pins.py", text)
        job = text.split("  pr-attestation-selftest:\n", 1)[1].split("\n  ci-ok:", 1)[0]
        self.assertIn("continue-on-error: true", job)
        needs = text.split("\n  ci-ok:", 1)[1].split("    if:", 1)[0]
        self.assertNotIn("pr-attestation-selftest", needs)


if __name__ == "__main__":
    unittest.main()
