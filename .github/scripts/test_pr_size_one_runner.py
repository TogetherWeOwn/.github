"""One-runner contract: the required gate job may also write the advisory
size label (`advisory-label: true`), so a caller uses one hosted runner per
event instead of two while keeping the `pr-size-gate / gate` check context.

Reads the working-tree reusable (the template still pins an older reviewed
snapshot until it is repinned through the reviewed pin chain).

Run with: python3 -m unittest discover -s .github/scripts -p 'test_pr_size*.py'
"""

import os
import shutil
import subprocess
import tempfile
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
        self.assertNotIn("timeout-minutes", src.scalars(label, 8))
        label_run = [l.strip() for l in label.splitlines() if "pr_size.py" in l]
        self.assertEqual(label_run, [src.ADVISORY_LABEL_RUN])
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
            (" label || echo", " label || false; echo"),               # fallback keeps failure
            (" label || echo", " label || echo x; false; echo"),
            ("timeout --kill-after=5s 90s ", ""),                      # unbounded advisory
            ("        if: github.event_name != 'push' && inputs.advisory-label\n",
             "        if: github.event_name != 'push' && inputs.advisory-label\n        timeout-minutes: 2\n"),
            ("        if: github.event_name != 'push' && inputs.advisory-label\n",
             "        if: github.event_name != 'push' && inputs.advisory-label\n        continue-on-error: true\n"),
        ):
            mutated = gate.replace(original, changed, 1)
            self.assertNotEqual(mutated, gate, original)
            with self.subTest(mutation=original), self.assertRaises(AssertionError):
                self.check(mutated)


STUB = """import os, sys, time
mode = sys.argv[1]
action = os.environ["STUB_" + mode.upper()]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(mode + "\\n")
if action == "hang":
    time.sleep(60)
sys.exit(int(action))
"""


def run_block(step):
    """The step's `run:` script, de-indented, as the runner hands it to bash."""
    lines = step.split("        run: ", 1)[1].splitlines()
    if lines[0].strip() != "|":
        return lines[0].strip() + "\n"
    body = []
    for line in lines[1:]:
        if line.strip() and not line.startswith(" " * 10):
            break  # next step's leading comment or key
        body.append(line[10:])
    return "\n".join(body).strip() + "\n"


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "needs bash + coreutils timeout")
class OneRunnerExecution(unittest.TestCase):
    """Run the shipped label and gate scripts the way the runner does
    (`bash --noprofile --norc -eo pipefail`, a later step is skipped once an
    earlier one fails) against a stub pr_size.py, and check the job verdict
    is exactly the gate's verdict for every advisory outcome."""

    def job(self, label_action, gate_action, gate_text=None, advisory_seconds="2s"):
        gate_text = gate_text or GATE.read_text(encoding="utf-8")
        every = src.steps(gate_text, "gate")
        label_script = run_block(every[1]).replace(" 90s ", f" {advisory_seconds} ")
        gate_script = run_block(every[4])
        with tempfile.TemporaryDirectory() as work:
            scripts = Path(work, ".pr-size-central/.github/scripts")
            scripts.mkdir(parents=True)
            (scripts / "pr_size.py").write_text(STUB)
            log = Path(work, "calls")
            env = dict(os.environ, STUB_LABEL=label_action, STUB_GATE=gate_action, STUB_LOG=str(log))
            outcomes = []
            for script in (label_script, gate_script):
                if outcomes and outcomes[-1] != 0:
                    break  # default `if: success()`: the gate step is skipped
                path = Path(work, f"step{len(outcomes)}.sh")
                path.write_text(script)
                outcomes.append(subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                    cwd=work, env=env, capture_output=True, text=True, timeout=30, check=False,
                ).returncode)
            calls = log.read_text().split() if log.exists() else []
        return outcomes, calls

    def test_gate_verdict_is_independent_of_advisory_outcome(self):
        for label in ("0", "1", "3", "hang"):
            for gate, verdict in (("0", 0), ("1", 1)):
                with self.subTest(label=label, gate=gate):
                    outcomes, calls = self.job(label, gate)
                    # Label always runs first (refresh survives a failing gate),
                    # its step always succeeds, and the gate always runs.
                    self.assertEqual(calls, ["label", "gate"])
                    self.assertEqual(outcomes[0], 0)
                    self.assertEqual(outcomes[1], verdict)

    def test_forced_timeout_is_bounded_by_the_shell(self):
        import time
        start = time.monotonic()
        outcomes, calls = self.job("hang", "1")
        self.assertLess(time.monotonic() - start, 20)
        self.assertEqual((outcomes, calls), ([0, 1], ["label", "gate"]))

    def test_negative_controls_skip_the_gate(self):
        gate = GATE.read_text(encoding="utf-8")
        for original, changed in (
            (" label || echo", " label || false; echo"),
            (" label || echo", " label; echo"),
        ):
            mutated = gate.replace(original, changed, 1)
            self.assertNotEqual(mutated, gate)
            with self.subTest(mutation=changed):
                outcomes, calls = self.job("3", "0", gate_text=mutated)
                self.assertNotEqual(outcomes[0], 0)
                self.assertEqual(calls, ["label"])  # the required gate never ran


if __name__ == "__main__":
    unittest.main()
