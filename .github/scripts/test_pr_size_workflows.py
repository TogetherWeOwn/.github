"""Static permission contract between the pr-size caller template and the
reusable workflows it calls.

GitHub validates a reusable workflow's nested-job permissions against the
caller job's grant when it PARSES the workflow, and it does not consult
the nested jobs' `if:` conditions (runner
`PermissionsHelper.ValidateEmbeddedPermissions`). A caller job granting
less than any job in the reusable file requests therefore fails the whole
workflow at startup - including unrelated jobs in the same file. Nothing
that executes in this repo can see that failure (the reusables are only
ever invoked from other repositories), so this test compares the shipped
template's grants with each reusable file's requests statically.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_pr_size*.py'
Also wired into the `pr-size selftest` job in .github/workflows/ci.yml.

The parser is deliberately small: it reads only the block shapes these
three files use (top-level `permissions:`, per-job `permissions:` and
`uses:`, two-space indents, plain scalar values). It is not a YAML parser.
"""

import re
import subprocess
import unittest
from pathlib import Path

import test_pr_size
import test_pr_size_source

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "workflow-templates" / "pr-size.yml"
WORKFLOWS = ROOT / ".github" / "workflows"

LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}
USES_RE = re.compile(r"workflows/(?P<file>[\w.-]+\.yml)@(?P<ref>[\w./-]+)")


def parse_workflow(path):
    """Return (top_permissions, {job: {"permissions": {...}|None, "uses": str|None}})."""
    return parse_workflow_text(path.read_text(encoding="utf-8"))


def parse_workflow_text(text):
    top = {}
    jobs = {}
    section = None  # None | "top-permissions" | "jobs"
    job = None
    in_job_permissions = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if indent == 0:
            section = {"permissions": "top-permissions", "jobs": "jobs"}.get(key)
            job = None
            in_job_permissions = False
            continue
        if section == "top-permissions" and indent == 2:
            top[key] = value
            continue
        if section != "jobs":
            continue
        if indent == 2:  # a job id
            job = key
            jobs[job] = {"permissions": None, "uses": None}
            in_job_permissions = False
        elif job is not None and indent == 4:
            in_job_permissions = key == "permissions"
            if in_job_permissions:
                jobs[job]["permissions"] = {}
            elif key == "uses":
                jobs[job]["uses"] = value
        elif job is not None and indent == 6 and in_job_permissions:
            jobs[job]["permissions"][key] = value
    return top, jobs


def effective(top, job):
    """A job's own permissions block replaces the top-level one wholesale;
    any scope a block omits is `none`."""
    if job["permissions"] is not None:
        return job["permissions"]
    return top


def requested_union(path):
    """The most any job in a reusable workflow file requests, per scope."""
    top, jobs = parse_workflow(path)
    union = {}
    for job in jobs.values():
        for scope, level in effective(top, job).items():
            if LEVELS[level] > LEVELS[union.get(scope, "none")]:
                union[scope] = level
    return union


class TemplatePermissionContract(unittest.TestCase):
    def test_all_callers_pin_reviewed_main_snapshot(self):
        # This main commit contains all three reusable workflows. The old
        # template pinned a pre-squash commit GitHub could not resolve for
        # callers.
        reviewed_sha = "c9d36db5b08703a3d7c8cd2112256f8768be44ce"
        _, jobs = parse_workflow(TEMPLATE)
        callers = {name: job["uses"] for name, job in jobs.items() if job["uses"]}
        self.assertEqual(
            callers,
            {
                name: f"TogetherWeOwn/.github/.github/workflows/{name}.yml@{reviewed_sha}"
                for name in ("pr-size", "pr-size-weekly", "pr-size-gate")
            },
        )

    def test_every_template_caller_grants_what_its_reusable_requests(self):
        top, jobs = parse_workflow(TEMPLATE)
        callers = {
            name: job
            for name, job in jobs.items()
            if job["uses"] and USES_RE.search(job["uses"])
        }
        self.assertTrue(callers, "template has no reusable-workflow caller jobs")
        for name, job in callers.items():
            match = USES_RE.search(job["uses"])
            reusable = WORKFLOWS / match.group("file")
            self.assertTrue(
                reusable.exists(), f"{name}: template calls missing {reusable}"
            )
            granted = effective(top, job)
            for scope, level in requested_union(reusable).items():
                self.assertGreaterEqual(
                    LEVELS[granted.get(scope, "none")],
                    LEVELS[level],
                    f"{name}: grants {scope}: {granted.get(scope, 'none')} but "
                    f"{match.group('file')} requests {level}; GitHub rejects "
                    "this caller at workflow-parse time",
                )

    def test_caller_selected_workflows_and_script_behavior(self):
        top, callers = parse_workflow(TEMPLATE)
        fixtures = (
            (test_pr_size.InventoryValidation, "test_successful_capped_inventory_fails_before_exclusions"),
            (test_pr_size.Gate, "test_read_only_api_exception_and_negative_controls"),
            (test_pr_size.Gate, "test_hard_stop_fails_even_with_exception_label_and_approval"),
        )
        runner = test_pr_size_source.PinnedBehavior()
        for filename, mode in test_pr_size_source.MODES.items():
            with self.subTest(workflow=filename):
                match = USES_RE.search(callers[filename.removesuffix(".yml")]["uses"])
                text = subprocess.check_output(
                    ["git", "show", f"{match.group('ref')}:.github/workflows/{filename}"],
                    cwd=ROOT, text=True,
                )
                test_pr_size_source.assert_trusted_source(self, text, mode)
                checkout, execution = test_pr_size_source.steps(text, mode)
                options = test_pr_size_source.checkout_options(text, mode)
                self.assertRegex(test_pr_size_source.scalars(checkout, 8)["uses"], r"^actions/checkout@[0-9a-f]{40}$")
                self.assertEqual(options["persist-credentials"], "false")
                self.assertEqual(options["path"], ".pr-size-central")
                self.assertEqual(options["sparse-checkout"], test_pr_size_source.SCRIPT)
                if mode == "gate":
                    env = test_pr_size_source.scalars(test_pr_size_source.block(execution, 8, "env"), 10)
                    for key, value in (("PR_SIZE_GATE_THRESHOLD", "400"), ("PR_SIZE_GATE_FILES", "20"), ("PR_SIZE_HARD_STOP", "1000")):
                        self.assertEqual(env[key], value)
                attrs = test_pr_size_source.scalars(execution, 8)
                self.assertEqual(attrs["run"], f"python3 .pr-size-central/{test_pr_size_source.SCRIPT} {mode}")
                reusable_top, jobs = parse_workflow_text(text)
                granted = effective(top, callers[filename.removesuffix(".yml")])
                for job in jobs.values():
                    for scope, level in effective(reusable_top, job).items():
                        self.assertGreaterEqual(LEVELS[granted.get(scope, "none")], LEVELS[level])
                for case, name in fixtures:
                    result = runner.run_fixture(options["ref"], case, name)
                    self.assertTrue(result.wasSuccessful(), (result.failures, result.errors))

    def test_previous_caller_chain_fails_inventory_regression(self):
        old = "f9aef7f2aa4312c4ae5744c58f2933a0091ec9ef"
        text = subprocess.check_output(
            ["git", "show", f"{old}:.github/workflows/pr-size-gate.yml"], cwd=ROOT, text=True
        )
        ref = test_pr_size_source.checkout_options(text, "gate")["ref"]
        result = test_pr_size_source.PinnedBehavior().run_fixture(
            ref, test_pr_size.InventoryValidation, "test_successful_capped_inventory_fails_before_exclusions"
        )
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])

    def test_template_subscribes_to_edited(self):
        # `edited` fires on a base-branch change, which changes the diff
        # size; without it a previously passing gate stays green for an
        # oversized PR until some other subscribed event occurs.
        text = TEMPLATE.read_text(encoding="utf-8")
        pull_request = re.search(
            r"^  pull_request:\n    types: \[([^\]\n]+)\]$", text, re.MULTILINE
        )
        self.assertIsNotNone(pull_request, "template has no pull_request types list")
        self.assertIn("edited", [event.strip() for event in pull_request[1].split(",")])

    def test_weekly_reusable_requests_read_only(self):
        # CI-STANDARD promises the weekly tally runs read-only; that is only
        # possible because pr-size-weekly.yml holds no write job.
        for scope, level in requested_union(WORKFLOWS / "pr-size-weekly.yml").items():
            self.assertLessEqual(
                LEVELS[level], LEVELS["read"], f"weekly requests {scope}: {level}"
            )


if __name__ == "__main__":
    unittest.main()
