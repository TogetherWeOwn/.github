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


def load_replies_script():
    text = WORKFLOW.read_text()
    m = re.search(r"<<'PYEOF2'\n(.*?)\n\s*PYEOF2\n", text, re.S)
    assert m, "review-replies heredoc not found in the workflow"
    return textwrap.dedent(m.group(1))


def enforce_since(name="ENFORCE_SINCE"):
    m = re.search(rf'{name}: "([^"]+)"', WORKFLOW.read_text())
    return m.group(1)


SCRIPT = load_script()
REPLIES_SCRIPT = load_replies_script()
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


REPO = "TogetherWeOwn/example"
THREAD_URL = "https://github.com/TogetherWeOwn/example/pull/7#discussion_r1"


def thread(*replies, first_login="devin-ai-integration"):
    comments = [{"login": first_login, "body": "a finding", "url": THREAD_URL}]
    comments += [{"login": login, "body": body, "url": THREAD_URL}
                 for login, body in replies]
    return comments


def run_replies(threads=(), created=NEW, event="pull_request", author="someone",
                draft="false", head_repo=REPO, raw=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "REVIEW_ENFORCE_SINCE": enforce_since("REVIEW_ENFORCE_SINCE"),
        "EVENT": event, "AUTHOR": author, "CREATED_AT": created,
        "DRAFT": draft, "PR_NUMBER": "7", "REPO": REPO, "HEAD_REPO": head_repo,
        "GH_TOKEN": "",
        "THREADS_JSON": raw if raw is not None else json.dumps(list(threads)),
    }
    p = subprocess.run([sys.executable, "-I", "-c", REPLIES_SCRIPT], env=env,
                       capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout + p.stderr


class ReviewReplies(unittest.TestCase):
    def assertPasses(self, result):
        code, out = result
        self.assertEqual(code, 0, out)
        self.assertIn("Review replies OK", out)

    def assertUnanswered(self, result, needle="Unanswered review finding"):
        code, out = result
        self.assertEqual(code, 1, out)
        self.assertIn("::error", out)
        self.assertIn(needle, out)
        self.assertIn(THREAD_URL, out)

    def test_no_devin_threads_passes(self):
        self.assertPasses(run_replies())
        self.assertPasses(run_replies([thread(("someone", "hi"), first_login="coderabbitai")]))

    def test_each_counted_first_line_passes(self):
        for body in ("Fixed in 3f24b0ce. `find_open` now pages.",
                     "Fixed in 0123456789abcdef0123456789abcdef01234567\nDetails.",
                     "Rejected: src/auth.py:42 already validates this at the head.",
                     "Rejected: see #12",
                     "Rejected: the caller validates this before dispatch above.",
                     "Duplicate of https://github.com/TogetherWeOwn/example/pull/7#discussion_r9",
                     "Duplicate of #12"):
            with self.subTest(body=body):
                self.assertPasses(run_replies([thread(("someone", body))]))

    def test_evidence_free_rejection_fails(self):
        for body in ("Rejected: no", "Rejected: not an issue", "Rejected: works for me"):
            with self.subTest(body=body):
                self.assertUnanswered(run_replies([thread(("someone", body))]))

    def test_reply_text_is_never_echoed(self):
        code, out = run_replies([thread(
            ("someone", "internal-host.example secret-token-123 rest of the line"))])
        self.assertEqual(code, 1, out)
        self.assertNotIn("secret-token-123", out)
        self.assertNotIn("internal-host.example", out)

    def test_format_drift_first_lines_fail(self):
        # The shapes a weekly sample of merged PRs actually carried: a real
        # reply whose first line the eval cannot count.
        for body in ("Fixed. The clause now judges sections per template.",
                     "Fixed in the latest push.",
                     "Confirmed - the counters miscount. Fixed in 2c098c70.",
                     "Valid. Fixed in e2aa1d83 by a different design.",
                     "✅ **Resolved**: the evidence now checks the build id.",
                     "Rejected, with evidence. The template says otherwise.",
                     "Duplicate of the POST finding above - same fix.",
                     "\nFixed in abc1234"):
            with self.subTest(body=body):
                self.assertUnanswered(run_replies([thread(("someone", body))]),
                                      "no reply starts with a counted first line")

    def test_no_reply_and_devin_followups_do_not_count(self):
        self.assertUnanswered(run_replies([thread()]), "no reply yet")
        self.assertUnanswered(
            run_replies([thread(("devin-ai-integration", "✅ **Resolved**: no longer applies."))]),
            "no reply yet")

    def test_one_good_reply_among_many_counts(self):
        self.assertPasses(run_replies([thread(
            ("someone", "Looking at this now."),
            ("devin-ai-integration", "✅ **Resolved**: no longer applies."),
            ("someone", "Fixed in abc1234"))]))

    def test_every_devin_thread_must_be_answered(self):
        code, out = run_replies([thread(("someone", "Fixed in abc1234")), thread()])
        self.assertEqual(code, 1, out)
        self.assertIn("Devin review threads: 2; answered in format: 1", out)

    def test_warning_only_contexts_pass(self):
        for kw in ({"draft": "true"}, {"created": OLD},
                   {"author": "dependabot[bot]"}, {"author": "renovate[bot]"},
                   {"head_repo": "someone/example"}, {"head_repo": ""}):
            with self.subTest(kw=kw):
                code, out = run_replies([thread()], **kw)
                self.assertEqual(code, 0, out)
                self.assertIn("::warning title=Unanswered review finding", out)

    def test_thread_list_failure_fails_closed(self):
        code, out = run_replies(raw="__error__")
        self.assertEqual(code, 1, out)
        self.assertIn("Could not list the review threads", out)
        code, out = run_replies(raw="__error__", draft="true")
        self.assertEqual(code, 0, out)
        self.assertIn("::warning title=Review replies", out)

    def test_merge_group_passes(self):
        code, out = run_replies([thread()], event="merge_group")
        self.assertEqual(code, 0, out)


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

    def test_private_addresses_fail(self):
        for b in ("host 192.168.1.10 is up", "db at 10.0.4.7", "proxy 172.20.0.3", "tailnet 100.101.2.3"):
            with self.subTest(b=b):
                self.assertFails(run(body=CLEAN_BODY + b + "\n"), "address")
        self.assertFails(run(commits=["fix: reach the db at 10.0.4.7"]), "address")

    def test_loopback_addresses_only_warn(self):
        for b in ("see http://localhost:8080/x", "runs on 127.0.0.1:3100", "open http://localhost",
                  "ping 127.0.0.1", "loopback test (localhost, 127.0.0.0/8, ::1)"):
            with self.subTest(b=b):
                code, out = run(body=CLEAN_BODY + b + "\n", commits=["fix: refuse non-loopback " + b])
                self.assertEqual(code, 0, out)
                self.assertIn("::warning title=Internal reference in the PR body", out)
                self.assertIn("::warning title=Internal reference in a commit message", out)

    def test_loopback_branch_only_warns(self):
        code, out = run(branch="fix/127.0.0.1-origins")
        self.assertEqual(code, 0, out)
        self.assertIn("::warning title=Internal reference in the branch name", out)
        self.assertFails(run(branch="fix/10.0.4.7-db"), "branch name")

    def test_versions_and_public_ips_pass(self):
        for b in ("bump to 10.2.3", "lib 1.10.0.4", "DNS 8.8.8.8", "172.32.0.1 is public",
                  "upgrade to 10.256.300.999", "bind to localhost in dev",
                  "docs at https://localhost.dev/docs"):
            with self.subTest(b=b):
                code, out = run(body=CLEAN_BODY + b + "\n")
                self.assertEqual(code, 0, out)

    def test_addresses_only_warn_on_bot_and_fork_prs(self):
        notes = CLEAN_BODY + "Fix HMR when the dev server is on 192.168.1.20\n"
        for kw in ({"author": "dependabot[bot]", "title": "build(deps): bump vite"}, {"fork": "true"}):
            with self.subTest(kw=kw):
                code, out = run(body=notes, commits=["fix: bind dev server to 10.0.0.2:8080"], **kw)
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

    def test_review_replies_step_shares_the_required_job(self):
        # Same job and bare check name: the check already reports on the
        # default branch everywhere, so no ruleset change is needed and no
        # never-reported check is made required.
        text = WORKFLOW.read_text()
        self.assertIn("Check automated review findings are answered in format", text)
        self.assertIn("REVIEW_ENFORCE_SINCE:", text)
        self.assertIn("recentComments: comments(last: 20)", text)
        self.assertEqual(text.count("\n    name: pr-standards\n"), 1)
        self.assertNotRegex(text, r"\n  review-replies:")


if __name__ == "__main__":
    unittest.main()
