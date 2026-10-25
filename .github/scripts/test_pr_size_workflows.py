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
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "workflow-templates" / "pr-size.yml"
WORKFLOWS = ROOT / ".github" / "workflows"

LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}
USES_RE = re.compile(r"workflows/(?P<file>[\w.-]+\.yml)@(?P<ref>[\w./-]+)")


def parse_workflow(path):
    """Return (top_permissions, {job: {"permissions": {...}|None, "uses": str|None}})."""
    top = {}
    jobs = {}
    section = None  # None | "top-permissions" | "jobs"
    job = None
    in_job_permissions = False
    for line in path.read_text(encoding="utf-8").splitlines():
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
    def test_every_template_caller_grants_what_its_reusable_requests(self):
        top, jobs = parse_workflow(TEMPLATE)
        callers = {
            name: job for name, job in jobs.items() if job["uses"] and USES_RE.search(job["uses"])
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

    def test_weekly_reusable_requests_read_only(self):
        # CI-STANDARD promises the weekly tally runs read-only; that is only
        # possible because pr-size-weekly.yml holds no write job.
        for scope, level in requested_union(WORKFLOWS / "pr-size-weekly.yml").items():
            self.assertLessEqual(
                LEVELS[level], LEVELS["read"], f"weekly requests {scope}: {level}"
            )


if __name__ == "__main__":
    unittest.main()
