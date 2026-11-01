"""Behaviour tests for the org-required `pr-standards` check.

The check's Python lives inline in .github/workflows/pr-standards-required.yml
so the org ruleset needs no checkout of this repo. These tests cut the heredoc
out of the workflow and run it with the same environment variables the
workflow step sets, so they test the exact code GitHub runs.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_pr_standards_required.py'
Also wired into the `pr-standards selftest` job in .github/workflows/ci.yml.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "pr-standards-required.yml"


def load_script():
    text = WORKFLOW.read_text()
    m = re.search(r"<<'PYEOF'\n(.*?)\n\s*PYEOF\n", text, re.S)
    assert m, "heredoc not found in the workflow"
    return textwrap.dedent(m.group(1))


def enforce_since():
    m = re.search(r'ENFORCE_SINCE: "([^"]+)"', WORKFLOW.read_text())
    return m.group(1)


SCRIPT = load_script()
NEW = "2026-11-05T10:00:00Z"
OLD = "2026-10-20T10:00:00Z"
CLEAN_BODY = "## Verification\n\n- python3 -m unittest: 12 tests OK\n"


def run(title="fix(auth): refuse expired sudo sessions", body=CLEAN_BODY,
        branch="fix/sudo-window", commits=(), created=NEW, event="pull_request",
        author="someone", fork="false", count=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "ENFORCE_SINCE": enforce_since(),
        "EVENT": event, "TITLE": title, "BODY": body, "BRANCH": branch,
        "AUTHOR": author, "CREATED_AT": created, "PR_NUMBER": "7",
        "REPO_FORK": fork, "COMMITS_URL": "", "GH_TOKEN": "",
        "COMMIT_COUNT": str(len(commits) if count is None else count),
        "COMMITS_JSON": json.dumps([[f"{i:040x}", s] for i, s in enumerate(commits)]),
    }
    p = subprocess.run([sys.executable, "-I", "-c", SCRIPT], env=env,
                       capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout + p.stderr


class InternalReferences(unittest.TestCase):
    def assertFails(self, result, needle):
        code, out = result
        self.assertEqual(code, 1, out)
        self.assertIn("::error", out)
        self.assertIn(needle, out)

    def test_clean_pr_passes(self):
        code, out = run()
        self.assertEqual(code, 0, out)
        self.assertIn("PR standards OK", out)

    def test_card_id_in_title_fails(self):
        self.assertFails(run(title="fix(quota): pin templates TWO-123"), "PR title")
        self.assertFails(run(title="TWO-123: pin quota templates"), "TWO-123")

    def test_other_prefixes_and_case_fail(self):
        for t in ("fix: tog-41001 verdicts", "fix: PAP-224 thing", "fix: PAPA-42 thing", "fix: two_801 thing"):
            with self.subTest(t=t):
                self.assertFails(run(title=t), "internal tracker id")

    def test_card_id_in_body_fails(self):
        self.assertFails(run(body=CLEAN_BODY + "\nRefs: TWO-801\n"), "PR body")

    def test_instance_link_and_host_fail(self):
        self.assertFails(run(body=CLEAN_BODY + "See https://paperclip.example.org/TWO/issues/x"),
                         "instance link")
        self.assertFails(run(body=CLEAN_BODY + "deployed on staging.infextion.net"), "internal host")

    def test_html_comments_and_github_links_are_ignored(self):
        body = ("<!-- Do not paste ids like PAPA-123 / PAP-224 -->\n" + CLEAN_BODY +
                "Fixes #12, see https://github.com/TogetherWeOwn/x/pull/3\n")
        code, out = run(body=body)
        self.assertEqual(code, 0, out)

    def test_lookalikes_pass(self):
        body = CLEAN_BODY + "CVE-2024-1234, SHA-256, UTF-8, ISO-8601, two-step, network-2 and v2-120."
        code, out = run(body=body)
        self.assertEqual(code, 0, out)

    def test_local_and_private_addresses_fail(self):
        for b in ("see http://localhost:8080/x", "runs on 127.0.0.1:3100", "host 192.168.1.10 is up",
                  "db at 10.0.4.7", "proxy 172.20.0.3", "tailnet 100.101.2.3",
                  "open http://localhost", "ping 127.0.0.1"):
            with self.subTest(b=b):
                self.assertFails(run(body=CLEAN_BODY + b + "\n"), "address")

    def test_versions_and_public_ips_pass(self):
        for b in ("bump to 10.2.3", "lib 1.10.0.4", "DNS 8.8.8.8", "172.32.0.1 is public",
                  "upgrade to 10.256.300.999", "bind to localhost in dev"):
            with self.subTest(b=b):
                code, out = run(body=CLEAN_BODY + b + "\n")
                self.assertEqual(code, 0, out)

    def test_addresses_only_warn_on_bot_and_fork_prs(self):
        notes = CLEAN_BODY + "Fix HMR on http://localhost:5173/\n"
        for kw in ({"author": "dependabot[bot]", "title": "build(deps): bump vite"}, {"fork": "true"}):
            with self.subTest(kw=kw):
                code, out = run(body=notes, commits=["fix: bind dev server to 0.0.0.0:8080"], **kw)
                self.assertEqual(code, 0, out)
                self.assertIn("::warning title=Internal reference in the PR body", out)
        self.assertFails(run(body=CLEAN_BODY + "TWO-5\n", author="dependabot[bot]",
                             title="build(deps): bump vite"), "tracker id")

    def test_github_lookalike_host_is_not_exempt(self):
        self.assertFails(run(body=CLEAN_BODY + "https://notgithub.com/TWO-123\n"), "tracker id")
        self.assertEqual(run(body=CLEAN_BODY + "https://github.com/o/TWO-123-repo\n")[0], 0)

    def test_new_pr_branch_with_id_fails(self):
        self.assertFails(run(branch="two-801-quota-0.2.4"), "branch name")

    def test_new_pr_commit_subject_with_id_fails(self):
        self.assertFails(run(commits=["fix: clean", "TWO-777: quota 0.2.3"]), "commit message")

    def test_new_pr_commit_body_with_id_fails(self):
        self.assertFails(run(commits=["fix: clean subject\n\nRefs: TWO-801"]), "commit message")

    def test_truncated_commit_list_fails(self):
        self.assertFails(run(commits=["fix: clean"] * 3, count=300), "Commit list")

    def test_legacy_pr_branch_and_commits_only_warn(self):
        code, out = run(branch="two-801-quota", commits=["TWO-801: quota"], created=OLD)
        self.assertEqual(code, 0, out)
        self.assertIn("::warning title=Internal reference in the branch name", out)
        self.assertIn("::warning title=Internal reference in a commit message", out)

    def test_legacy_pr_title_still_fails(self):
        self.assertFails(run(title="TWO-801: quota", created=OLD), "PR title")


class TitleFormat(unittest.TestCase):
    def test_non_conventional_title_fails(self):
        code, out = run(title="Add a thing")
        self.assertEqual(code, 1, out)
        self.assertIn("not a Conventional Commits header", out)

    def test_long_and_period_fail(self):
        self.assertEqual(run(title="fix: " + "x" * 100)[0], 1)
        self.assertEqual(run(title="fix: a thing.")[0], 1)

    def test_fork_and_bot_titles_only_warn(self):
        self.assertEqual(run(title="Add a thing", fork="true")[0], 0)
        self.assertEqual(run(title="Bump x from 1 to 2", author="dependabot[bot]")[0], 0)

    def test_fork_still_rejects_ids(self):
        self.assertEqual(run(title="fix: TWO-9 thing", fork="true")[0], 1)


class Events(unittest.TestCase):
    def test_merge_group_passes(self):
        code, out = run(event="merge_group", title="")
        self.assertEqual(code, 0, out)


class WorkflowShape(unittest.TestCase):
    def test_bare_check_name_and_triggers(self):
        text = WORKFLOW.read_text()
        self.assertRegex(text, r"\n    name: pr-standards\n")
        self.assertIn("types: [opened, edited, synchronize, reopened, ready_for_review]", text)
        self.assertIn("merge_group:", text)
        self.assertNotRegex(text, r"\n  pull_request:\n    paths")


if __name__ == "__main__":
    unittest.main()
