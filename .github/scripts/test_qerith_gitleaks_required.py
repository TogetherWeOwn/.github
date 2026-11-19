"""Exercise the exact inline trusted workflow, without a provider binding."""

import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import textwrap
import unittest
from functools import partial
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
        group = {"head_sha": head, "base_sha": base, "head_ref": QUEUE, "base_ref": "refs/heads/main"}
        return env, {"action": "checks_requested", "repository": REPO, "merge_group": group}
    pr = {"head": {"sha": head}, "base": {"sha": base, "ref": "main", "repo": REPO}}
    return env, {"action": "synchronize", "repository": REPO, "pull_request": pr}


class Candidate(unittest.TestCase):
    def test_scan_attempts_do_not_share_concurrency(self):
        self.assertNotRegex(SOURCE.read_text(), r"(?m)^ *concurrency *:")

    def test_event_identity(self):
        for kind, action in (("pull_request", "synchronize"), ("pull_request", "edited"),
                             ("merge_group", "checks_requested")):
            env, event = event_inputs(kind)
            self.assertEqual(driver["candidate"](env, event | {"action": action}), (kind, REPO["full_name"], HEAD, BASE))

    def test_rejects_incomplete_mismatched_or_unsupported_identity(self):
        for kind, mutation in (
            ("pull_request", lambda e, p: p["pull_request"]["head"].clear()),
            ("pull_request", lambda e, p: p["pull_request"]["base"].update(sha="main")),
            ("pull_request", lambda e, p: p["pull_request"]["base"].update(ref="develop")),
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
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
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

    def commit(self, message, *args):
        self.git("add", "--all")
        self.git("commit", "-qm", message, *args)
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

    def test_rejects_unsupported_path_even_after_removal(self):
        path = self.source / "fixture\\"
        path.write_text("ordinary content\n")
        self.commit("Add fixture")
        path.unlink()
        self.commit("Remove fixture")
        with self.assertRaisesRegex(driver["Rejected"], "Unsupported historical path"):
            driver["scan"](self.work, self.fetch("pull_request"), self.work / "missing", *self.policy())

    def test_success_requires_completed_patch_stream(self):
        policy, ignore = self.policy()
        rules = {"runs": [{"tool": {"driver": {"rules": [{"id": "synthetic"}]}}}]}
        (self.work / "rules.sarif").write_text(json.dumps(rules))
        (self.work / "history.json").write_text("[]")
        with patch.object(subprocess, "check_output", return_value=b""), patch.object(subprocess, "run") as scanner:
            scanner.return_value.returncode = 0
            with self.assertRaisesRegex(driver["Rejected"], "Git producer did not complete"):
                driver["scan"](self.work, self.source, "fake-scanner", policy, ignore)

    def test_prior_clean_verdict_is_insufficient_without_parser_receipt(self):
        # This is all the old driver required at the scanner seam, without a malformed commit.
        (self.work / "git-done").write_text("complete\n")
        (self.work / "history.json").write_text("[]")
        (self.work / "rules.sarif").write_text(json.dumps({
            "runs": [{"tool": {"driver": {"rules": [{"id": "synthetic"}]}}}],
        }))
        with patch.object(subprocess, "check_output", return_value=b""), patch.object(subprocess, "run") as scanner:
            scanner.return_value.returncode = 0
            with self.assertRaisesRegex(driver["Rejected"], "parser did not complete"):
                driver["scan"](self.work, self.source, "fake-scanner", *self.policy())
        self.assertEqual((self.work / "history.log").read_bytes(), b"")


class Hosted(History):
    @classmethod
    def setUpClass(cls):
        if not HOSTED:
            raise unittest.SkipTest("requires the hosted pinned scanner build")
        cls.build = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        try:
            cls.src = driver["prepare_scanner"](cls.build)
            cls.scanner = driver["build_scanner"](cls.build, cls.src)
        except subprocess.CalledProcessError as error:
            # Build output contains only pinned sources, before any fixture Git fetch.
            print(error.output.decode(errors="replace"), file=sys.stderr)
            raise
        with cls.scanner.open("rb") as binary:
            print(f"hosted scanner sha256={hashlib.file_digest(binary, 'sha256').hexdigest()}", flush=True)

    def test_scanner_reports_pinned_release_version(self):
        self.assertEqual(driver["run"](self.work, self.scanner, "version").decode(), driver["VERSION"])

    def test_clean_pr_history(self):
        self.commit_after_edit()
        self.git("commit", "--allow-empty", "-qm", "Empty commit")
        path = self.source / "fixture.txt"
        path.write_text("ordinary content with no final newline")
        self.commit("Edit without final newline")
        path.chmod(0o755)
        self.commit("Mode-only change")
        driver["scan"](self.work, self.fetch("pull_request"), self.scanner, *self.policy())
        self.assertEqual((self.work / "parse-done").read_bytes(), b"complete\n")

    def faulting_scanner(self, reader):
        git_source = self.src / "sources/git.go"
        original = git_source.read_text()
        self.addCleanup(git_source.write_text, original)
        injected = driver["replace_once"](
            original, "gitdiff.ParseGitLog(ctx, output,",
            f"gitdiff.ParseGitLog(ctx, io.MultiReader({reader}, syntheticParserFault{{}}),",
        )
        git_source.write_text(injected + '''
          type syntheticParserFault struct{}
          func (syntheticParserFault) Read([]byte) (int, error) { return 0, io.ErrUnexpectedEOF }
        ''')
        return driver["build_scanner"](self.build, self.src, "gitleaks-fixture-fault")

    def test_reader_fault_after_successful_git_producer(self):
        self.commit_after_edit()
        history = self.fetch("pull_request")
        with self.assertRaisesRegex(driver["Rejected"], "parser did not complete"):
            driver["scan"](self.work, history, self.faulting_scanner("output"), *self.policy())
        self.assertEqual((self.work / "git-done").read_bytes(), b"complete\n")
        self.assertFalse((self.work / "parse-done").exists())
        self.assertIn(b"Git log parsing did not complete", (self.work / "history.log").read_bytes())

    def test_reader_fault_stops_active_git_producer(self):
        # More ordinary output than the pipe can hold, with the fault in the first read.
        (self.source / "fixture.txt").write_text("ordinary content\n" * 150_000)
        self.commit("Add ordinary fixture content")
        history = self.fetch("pull_request")
        scanner = self.faulting_scanner("io.LimitReader(output, 32)")
        with (
            patch.object(subprocess, "run", side_effect=partial(subprocess.run, timeout=15)),
            self.assertRaisesRegex(driver["Rejected"], "Git producer did not complete"),
        ):
            driver["scan"](self.work, history, scanner, *self.policy())
        self.assertFalse((self.work / "parse-done").exists())
        self.assertIn(b"Git log parsing did not complete", (self.work / "history.log").read_bytes())

    def test_unix_git_date_keeps_following_patch(self):
        self.fake_value()
        commit = self.commit("Use a wide Git author date", "--date=@253402300800 +0000")
        self.assert_finding("pull_request", commit)
        finding, = json.loads((self.work / "history.json").read_text())
        self.assertEqual(finding["Date"], "10000-01-01T00:00:00Z")

    def test_long_commit_message_keeps_following_patch(self):
        self.fake_value()
        self.assert_finding("pull_request", self.commit("ordinary text " * 5500))

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
    unittest.main(argv=[arg for arg in sys.argv if arg != "--hosted"])
