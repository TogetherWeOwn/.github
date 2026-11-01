"""Every workflow in this repo must run on GitHub-hosted runners.

The org has no self-hosted runners. A required workflow routed to a
self-hosted label never starts, so its check never reports and every merge
in the repos it covers is blocked. These tests fail if any workflow or
workflow template here names a self-hosted label, or picks its runner from
a repository/org variable that could hold one.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_hosted_runners.py'
Also wired into the `pr-standards selftest` job in .github/workflows/ci.yml.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIRS = [ROOT / ".github" / "workflows", ROOT / "workflow-templates"]

# `self-hosted` is the label GitHub gives every self-hosted runner; the rest
# are the retired pool's own labels and the variable that routed to it.
SELF_HOSTED = re.compile(r"self[-_ ]?hosted|two-(?:isolated|ephemeral)|CI_OVERFLOW_RUNNER", re.I)
RUNS_ON = re.compile(r"^\s*runs-on:\s*(.*)$", re.M)


def workflow_files():
    return sorted(p for d in WORKFLOW_DIRS for p in d.glob("*.y*ml"))


def violations(text):
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if SELF_HOSTED.search(line):
            found.append((n, "names a self-hosted label", line.strip()))
    for m in RUNS_ON.finditer(text):
        if "vars." in m.group(1):
            n = text.count("\n", 0, m.start()) + 1
            found.append((n, "takes its runner from a variable", m.group(0).strip()))
    return found


class Scanner(unittest.TestCase):
    def test_flags_self_hosted_labels(self):
        for line in [
            "    runs-on: [self-hosted, linux]",
            "    runs-on: ${{ fromJSON(github.event.repository.private && '[\"self-hosted\",\"two-selfhosted\"]' || '[\"ubuntu-latest\"]') }}",
            "      runs-on-json: '[\"two-isolated\"]'",
            "    # Private repos: run on the self-hosted pool.",
        ]:
            self.assertTrue(violations(line), line)

    def test_flags_runner_from_variable(self):
        self.assertTrue(violations("    runs-on: ${{ fromJSON(vars.CI_RUNNER) }}"))

    def test_hosted_runners_pass(self):
        for line in [
            "    runs-on: ubuntu-latest",
            "    runs-on: ${{ fromJSON(inputs.runs-on-json) }}",
            "      runs-on-json: '[\"ubuntu-latest\"]'",
        ]:
            self.assertEqual(violations(line), [], line)


class Repo(unittest.TestCase):
    def test_workflows_found(self):
        names = {p.name for p in workflow_files()}
        self.assertIn("pr-standards-required.yml", names)
        self.assertIn("test-guard.yml", names)

    def test_no_workflow_uses_self_hosted_runners(self):
        problems = [
            f"{p.relative_to(ROOT)}:{n}: {why}: {line}"
            for p in workflow_files()
            for n, why, line in violations(p.read_text())
        ]
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
