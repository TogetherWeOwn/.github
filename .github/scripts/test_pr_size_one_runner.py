"""One-runner contract: the required gate job may also write the advisory
size label (`advisory-label: true`), so a caller uses one hosted runner per
event instead of two while keeping the `pr-size-gate / gate` check context.

Reads the working-tree reusable (the template still pins an older reviewed
snapshot until it is repinned through the reviewed pin chain).

Run with: python3 -m unittest discover -s .github/scripts -p 'test_pr_size*.py'
"""

import unittest
from pathlib import Path

import test_pr_size_source as src

GATE = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-size-gate.yml"
LABEL_IF = "github.event_name != 'push' && inputs.advisory-label"
PR_NUMBER = "${{ inputs.pr-number != '' && inputs.pr-number || github.event.pull_request.number }}"


class OneRunnerContract(unittest.TestCase):
    def check(self, gate):
        inputs = src.block(src.block(src.block(gate, 0, "on"), 2, "workflow_call"), 4, "inputs")
        flag = src.scalars(src.block(inputs, 6, "advisory-label"), 8)
        self.assertEqual((flag.get("type"), flag.get("default"), flag.get("required")),
                         ("boolean", "false", "false"))
        job = src.block(src.block(gate, 0, "jobs"), 2, "gate")
        perms = src.scalars(src.block(job, 4, "permissions"), 6)
        self.assertEqual(perms, {"contents": "read", "issues": "write", "pull-requests": "write"})
        self.assertNotIn("continue-on-error:", gate)
        self.assertNotRegex(gate, r"(?m)^    if:")
        steps = src.steps(gate, "gate")
        self.assertEqual(len(steps), 5)
        self.assertEqual([src.scalars(s, 8).get("if") for s in steps],
                         [None, LABEL_IF, "github.event_name == 'push'",
                          "github.event_name == 'push'", "github.event_name != 'push'"])
        label, gate_step = steps[1], steps[4]
        self.assertEqual(src.scalars(src.block(label, 8, "env"), 10).get("PR_NUMBER"), PR_NUMBER)
        self.assertEqual(src.scalars(src.block(gate_step, 8, "env"), 10).get("PR_NUMBER"), PR_NUMBER)
        label_run = [l.strip() for l in label.splitlines() if "pr_size.py" in l]
        self.assertEqual(len(label_run), 1)
        self.assertTrue(label_run[0].startswith("python3 .pr-size-central/.github/scripts/pr_size.py label || "))
        self.assertEqual(src.scalars(gate_step, 8).get("run"),
                         "python3 .pr-size-central/.github/scripts/pr_size.py gate")

    def test_working_tree_gate(self):
        self.check(GATE.read_text(encoding="utf-8"))

    def test_mutations_fail(self):
        gate = GATE.read_text(encoding="utf-8")
        for original, changed in (
            (" label || echo", " label; echo"),                        # failure no longer swallowed
            (f"if: {LABEL_IF}", "if: inputs.advisory-label"),          # would run on push
            ("default: false\n        type: boolean", "default: true\n        type: boolean"),
            ("pr_size.py gate", "pr_size.py gate || true"),            # gate fail-open
            ("        timeout-minutes: 2\n", "        timeout-minutes: 2\n        continue-on-error: true\n"),
        ):
            mutated = gate.replace(original, changed, 1)
            self.assertNotEqual(mutated, gate, original)
            with self.subTest(mutation=original), self.assertRaises(AssertionError):
                self.check(mutated)


if __name__ == "__main__":
    unittest.main()
