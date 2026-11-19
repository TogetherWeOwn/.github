"""Exercise the exact inline trusted workflow, without a provider binding."""

import json
import runpy
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "workflows/qerith-gitleaks-required.yml"
HOSTED = "--hosted" in sys.argv
body = SOURCE.read_text().split("          python3 -I - <<'PY'\n", 1)[1]
with tempfile.TemporaryDirectory() as tmp:
    script = Path(tmp) / "inline.py"
    script.write_text(textwrap.dedent(body.split("\n          PY\n", 1)[0]))
    driver = runpy.run_path(str(script))

HEAD, BASE = "a" * 40, "b" * 40
REPO = {"id": 123, "full_name": "example/fixture"}
QUEUE = "refs/heads/gh-readonly-queue/main/pr-1"


def event_inputs(kind, head=HEAD, base=BASE):
    env = {
        "GITHUB_EVENT_NAME": kind,
        "GITHUB_REPOSITORY": REPO["full_name"],
        "GITHUB_REPOSITORY_ID": str(REPO["id"]),
        "GITHUB_REF": QUEUE if kind == "merge_group" else "refs/pull/1/merge",
        "GITHUB_SHA": head if kind == "merge_group" else "c" * 40,
    }
    if kind == "merge_group":
        return env, {
            "action": "checks_requested", "repository": REPO,
            "merge_group": {"head_sha": head, "base_sha": base,
                            "head_ref": QUEUE, "base_ref": "refs/heads/main"},
        }
    return env, {
        "action": "synchronize", "repository": REPO,
        "pull_request": {"head": {"sha": head}, "base": {"sha": base, "ref": "main", "repo": REPO}},
    }


class Candidate(unittest.TestCase):
    def test_event_identity(self):
        for kind in ("pull_request", "merge_group"):
            with self.subTest(kind=kind):
                self.assertEqual(driver["candidate"](*event_inputs(kind)), (kind, REPO["full_name"], HEAD, BASE))

    def test_rejects_incomplete_mismatched_or_unsupported_identity(self):
        for kind, mutation in (
            ("pull_request", lambda e, p: p["pull_request"]["head"].clear()),
            ("pull_request", lambda e, p: p["pull_request"]["base"].update(sha="main")),
            ("pull_request", lambda e, p: e.update(GITHUB_REPOSITORY_ID="321")),
            ("pull_request", lambda e, p: p["pull_request"]["base"].update(repo={"id": 456})),
            ("merge_group", lambda e, p: e.update(GITHUB_SHA=BASE)),
            ("merge_group", lambda e, p: e.update(GITHUB_REF="refs/heads/main")),
            ("merge_group", lambda e, p: p.update(action="destroyed")),
            ("merge_group", lambda e, p: p["merge_group"].update(base_sha=None)),
            ("push", lambda e, p: None),
        ):
            with self.subTest(kind=kind, case=mutation.__code__.co_firstlineno):
                env, event = event_inputs(kind)
                mutation(env, event)
                with self.assertRaisesRegex(driver["Rejected"], "candidate identity"):
                    driver["candidate"](env, event)


class History(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "source"
        self.work = self.root / "work"
        self.work.mkdir()
        self.git("init", "-q", "--initial-branch=main", self.source)
        (self.source / "fixture.txt").write_text("base\n")
        self.commit("Base")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        target = self.source if self.source.is_dir() else self.root
        return driver["run"](
            self.root, driver["GIT"], "-C", target, "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid", *args,
        ).decode()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def fetch(self, kind):
        head = self.git("rev-parse", "HEAD")
        event, _, candidate, base = driver["candidate"](*event_inputs(kind, head, self.base))
        return driver["fetch"](self.work, str(self.source), event, candidate, base)

    def commit_after_edit(self):
        (self.source / "fixture.txt").write_text("ordinary content\n")
        self.commit("Edit")

    def policy(self, text=None):
        policy, ignore = self.work / "policy.toml", self.work / "empty.ignore"
        policy.write_text(driver["POLICY"] if text is None else text)
        ignore.touch()
        return policy, ignore


class Offline(History):
    def test_fetch_keeps_exact_history_without_materializing_source(self):
        self.commit_after_edit()
        history = self.fetch("merge_group")
        self.assertFalse((history / "fixture.txt").exists())
        self.assertEqual(driver["run"](self.root, driver["GIT"], "-C", history, "rev-list", "--count", "HEAD"), b"2")

    def test_fetch_rejects_unrelated_merge_group_base(self):
        self.git("switch", "--orphan", "unrelated")
        self.commit_after_edit()
        with self.assertRaises(subprocess.CalledProcessError):
            self.fetch("merge_group")

    def test_success_requires_completed_patch_stream(self):
        policy, ignore = self.policy()
        rules = {"runs": [{"tool": {"driver": {"rules": [{"id": "synthetic"}]}}}]}
        (self.work / "rules.sarif").write_text(json.dumps(rules))
        (self.work / "history.json").write_text("[]")
        with patch.object(subprocess, "check_output", return_value=b""), patch.object(subprocess, "run") as scanner:
            scanner.return_value.returncode = 0
            with self.assertRaisesRegex(driver["Rejected"], "Incomplete history scan"):
                driver["scan"](self.work, self.source, "fake-scanner", policy, ignore)

class Hosted(History):
    @classmethod
    def setUpClass(cls):
        if not HOSTED:
            raise unittest.SkipTest("requires the hosted pinned scanner download")
        cls.scanner = driver["download"](Path(cls.enterClassContext(tempfile.TemporaryDirectory())))

    def test_clean_pr_history(self):
        self.commit_after_edit()
        driver["scan"](self.work, self.fetch("pull_request"), self.scanner, *self.policy())

    def test_no_diff_finding_in_pr_history(self):
        (self.source / ".gitattributes").write_text("fixture.txt -diff\n")
        self.fake_value()
        leaked = self.commit("Add synthetic finding")
        # The latest tree is clean and its ignore/config files must not win.
        (self.source / "fixture.txt").unlink()
        (self.source / ".gitleaksignore").write_text(f"{leaked}:fixture.txt:github-pat:1\n")
        (self.source / ".gitleaks.toml").write_text("[extend]\nuseDefault = false\n")
        self.commit("Remove synthetic finding and add candidate policy")
        self.assert_finding("pull_request", leaked)

    def test_merge_resolution_only_finding_in_group_history(self):
        self.git("switch", "-qc", "side")
        (self.source / "fixture.txt").write_text("side edit\n")
        self.commit("Side edit")
        self.git("switch", "-q", "main")
        self.commit_after_edit()
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "--no-edit", "side")
        self.assertTrue(self.git("ls-files", "-u", "--", "fixture.txt"))
        self.fake_value()
        self.assert_finding("merge_group", self.commit("Resolve with synthetic finding"))

    def test_missing_scanner_fails(self):
        self.commit_after_edit()
        with self.assertRaises(FileNotFoundError):
            driver["scan"](self.work, self.fetch("pull_request"), self.work / "missing", *self.policy())

    def test_ruleless_policy_fails(self):
        self.commit_after_edit()
        with self.assertRaisesRegex(driver["Rejected"], "no effective rules"):
            driver["scan"](self.work, self.fetch("pull_request"), self.scanner,
                           *self.policy('[[allowlists]]\npaths = ["fixture.txt"]\n'))

    def fake_value(self):
        token = "gh" + "p_FAKE" + "0123456789abcdef" * 2
        (self.source / "fixture.txt").write_text(f"{token} # gitleaks:allow\n")

    def assert_finding(self, kind, commit):
        with self.assertRaisesRegex(driver["Rejected"], "Gitleaks reported findings"):
            driver["scan"](self.work, self.fetch(kind), self.scanner, *self.policy())
        findings = json.loads((self.work / "history.json").read_text())
        self.assertEqual([(f["Commit"], f["File"], f["RuleID"]) for f in findings],
                         [(commit, "fixture.txt", "github-pat")])
        self.assertNotIn("p_FAKE", (self.work / "history.json").read_text())


if __name__ == "__main__":
    if HOSTED:
        sys.argv.remove("--hosted")
    unittest.main()
