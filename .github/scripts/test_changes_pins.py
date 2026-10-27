#!/usr/bin/env python3
"""Execute the required pincheck in local Git fixtures, without credentials."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ".github/workflows/ci.yml"
TEMPLATE = "workflow-templates/starter-ci.yml"
SOURCE = ".github/workflows/changes.yml"
GUARD = ".github/workflows/test-guard.yml"
WRAPPER = ".github/workflows/test-guard-required.yml"
CHECKER = ".github/scripts/check_changes_pins.py"
USES = "TogetherWeOwn/.github/.github/workflows/changes.yml@"


def pincheck_commands():
    """Execute actual gate steps; dependency setup is separate from Git fixtures.

    Fixtures inherit the test interpreter's installed, hash-pinned dependency.
    The dependency-step contract below keeps setup mandatory in hosted CI.
    """
    workflow = yaml.safe_load((ROOT / CI).read_text(encoding="utf-8"))
    commands = [step["run"] for step in workflow["jobs"]["pincheck"]["steps"]
                if "run" in step and step.get("id") != "yaml-dependency"]
    if not commands:
        raise AssertionError("required pincheck has no executable run steps")
    return "\n".join(commands)


class ChangesPinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="changes-pincheck-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        home = self.root / "home"
        home.mkdir()
        self.env = {
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.defpath]),
            "HOME": str(home), "XDG_CONFIG_HOME": str(home), "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
        }
        self.git("init", "--template=", "-q", "-b", "main")
        self.git("config", "user.name", "Pincheck Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write(SOURCE, "old detector\n")
        self.write(GUARD, "current guard\n")
        self.stale = self.commit()
        self.write(SOURCE, "current detector\n")
        self.current = self.commit()
        self.write(CI, (ROOT / CI).read_text(encoding="utf-8"))
        self.write(TEMPLATE, (ROOT / TEMPLATE).read_text(encoding="utf-8"))
        self.repin(CI, self.current)
        self.repin(TEMPLATE, self.current)
        self.write(WRAPPER, f"jobs:\n  gate:\n    uses: TogetherWeOwn/.github/{GUARD}@{self.current}\n")
        # Before the fix the actual pincheck is inline and this file is absent.
        if (ROOT / CHECKER).is_file():
            self.write(CHECKER, (ROOT / CHECKER).read_text(encoding="utf-8"))
        self.commit()
        self.remote = self.root / "remote.git"
        self.git("clone", "--bare", "-q", str(self.repo), str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, f"git {args}: {result.stderr}")
        return result.stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", "--all")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture change")
        return self.git("rev-parse", "HEAD")

    def repin(self, caller, pin):
        text = (self.repo / caller).read_text(encoding="utf-8")
        text, count = re.subn(re.escape(USES) + r"[^\s#]+", USES + pin, text)
        self.assertEqual(count, 1)
        self.write(caller, text)

    def run_pincheck(self):
        # Unspecified Linux run steps use bash -e, without nounset/pipefail.
        return subprocess.run(["bash", "-e", "-c", pincheck_commands()],
                              cwd=self.repo, env=self.env,
                              text=True, capture_output=True, timeout=20)

    def assert_rejected(self, caller, reason):
        result = self.run_pincheck()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(caller, output)
        self.assertIn(reason, output)
        self.assertIn("repin", output)

    def test_stale_ci_pin_fails_actual_required_job(self):
        self.repin(CI, self.stale)
        self.assert_rejected(CI, "differs")

    def test_stale_template_pin_fails_actual_required_job(self):
        self.repin(TEMPLATE, self.stale)
        self.assert_rejected(TEMPLATE, "differs")

    def test_equal_content_pins_pass(self):
        result = self.run_pincheck()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_pin_fails_for_each_caller(self):
        for caller in (CI, TEMPLATE):
            with self.subTest(caller=caller):
                original = (self.repo / caller).read_text(encoding="utf-8")
                self.write(caller, re.sub(r"(?m)^    uses: .*changes\.yml@.*\n", "", original))
                self.assert_rejected(caller, "40-character")
                self.write(caller, original)

    def test_malformed_ref_fails_for_each_caller(self):
        for caller in (CI, TEMPLATE):
            for pin in ("main", self.current[:12], "g" * 40, self.current + "0"):
                with self.subTest(caller=caller, pin=pin):
                    self.repin(caller, pin)
                    self.assert_rejected(caller, "40-character")
            self.repin(caller, self.current)

    def test_unresolvable_pin_fails_for_each_caller(self):
        for caller in (CI, TEMPLATE):
            with self.subTest(caller=caller):
                self.repin(caller, "f" * 40)
                self.assert_rejected(caller, "not fetchable")
                self.repin(caller, self.current)

    def test_comment_cannot_supply_missing_active_pin(self):
        for caller in (CI, TEMPLATE):
            with self.subTest(caller=caller):
                original = (self.repo / caller).read_text(encoding="utf-8")
                self.write(caller, original.replace("    uses: " + USES, "    # uses: " + USES))
                self.assert_rejected(caller, "40-character")
                self.write(caller, original)

    def test_duplicate_pin_fails_for_each_caller(self):
        for caller in (CI, TEMPLATE):
            with self.subTest(caller=caller):
                original = (self.repo / caller).read_text(encoding="utf-8")
                line = f"    uses: {USES}{self.current}\n"
                self.write(caller, original.replace(line, line * 2))
                self.assert_rejected(caller, "40-character")
                self.write(caller, original)

    def test_non_commit_object_is_rejected(self):
        self.repin(CI, self.git("rev-parse", f"HEAD:{SOURCE}"))
        self.assert_rejected(CI, "not fetchable")

    def test_missing_source_at_pin_is_rejected(self):
        (self.repo / SOURCE).unlink()
        missing = self.commit()
        self.write(SOURCE, "current detector\n")
        self.commit()
        self.repin(CI, missing)
        self.assert_rejected(CI, "differs")

    def test_equal_source_on_fetchable_non_main_branch_passes(self):
        original = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "source-branch")
        self.write("branch-only.txt", "source commit outside main\n")
        source = self.commit()
        self.git("push", "-q", "origin", "source-branch")
        self.git("checkout", "-q", "main")
        self.git("branch", "-D", "source-branch")
        self.git("update-ref", "-d", "refs/remotes/origin/source-branch")
        self.assertEqual(self.git("merge-base", original, source), original)
        # Clone only main: the checker must fetch the source commit from origin.
        fresh = self.root / "fresh"
        self.git("clone", "-q", "--single-branch", "--no-local", "--branch", "main",
                 str(self.remote), str(fresh))
        self.repo = fresh
        absent = subprocess.run(["git", "cat-file", "-e", source], cwd=self.repo,
                                env=self.env, capture_output=True, timeout=10)
        self.assertNotEqual(absent.returncode, 0)
        for caller in (CI, TEMPLATE):
            self.repin(caller, source)
        result = self.run_pincheck()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", f"{source}^{{commit}}"), source)

    def test_worktree_source_cannot_hide_stale_pin(self):
        self.repin(CI, self.stale)
        self.write(SOURCE, "old detector\n")
        self.assert_rejected(CI, "differs")

    def test_quoted_pins_with_comments_pass(self):
        for caller in (CI, TEMPLATE):
            original = (self.repo / caller).read_text(encoding="utf-8")
            self.write(caller, original.replace(f"uses: {USES}{self.current}",
                       f"uses: '{USES}{self.current}' # source commit"))
        result = self.run_pincheck()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_neighboring_job_cannot_supply_changes_pin(self):
        for caller in (CI, TEMPLATE):
            with self.subTest(caller=caller):
                original = (self.repo / caller).read_text(encoding="utf-8")
                self.write(caller, original.replace("  changes:\n", "  other:\n"))
                self.assert_rejected(caller, "40-character")
                self.write(caller, original)

    def test_quoted_or_spaced_neighbor_cannot_supply_active_pin(self):
        for caller in (CI, TEMPLATE):
            original = (self.repo / caller).read_text(encoding="utf-8")
            for key in ('"uses"', "'uses'", "uses ", "uses\t"):
                for neighbor in ('"neighbor"', "'neighbor'", "neighbor ", "neighbor\t"):
                    with self.subTest(caller=caller, key=key, neighbor=neighbor):
                        self.write(caller, "name: fixture\non: workflow_dispatch\njobs:\n"
                                   f"  changes:\n    {key}: {USES}{self.stale}\n"
                                   f"  {neighbor}:\n    uses: {USES}{self.current}\n")
                        reason = "invalid or duplicate workflow YAML" if "\t" in key + neighbor else "differs"
                        self.assert_rejected(caller, reason)
            self.write(caller, original)

    def test_block_scalar_text_cannot_supply_active_pin(self):
        for caller in (CI, TEMPLATE):
            original = (self.repo / caller).read_text(encoding="utf-8")
            for scalar in ("|", "|-", ">", ">-"):
                with self.subTest(caller=caller, scalar=scalar):
                    self.write(caller, "name: fixture\non: workflow_dispatch\njobs:\n"
                               f"  changes:\n   uses: {USES}{self.stale}\n"
                               f"   name: {scalar}\n    uses: {USES}{self.current}\n")
                    self.assert_rejected(caller, "differs")
            self.write(caller, original)

    def test_duplicate_yaml_keys_cannot_hide_behind_valid_pin(self):
        for caller in (CI, TEMPLATE):
            original = (self.repo / caller).read_text(encoding="utf-8")
            common = f"  changes:\n    uses: {USES}{self.current}\n"
            variants = ["name: first\nname: second\njobs:\n" + common,
                        "jobs:\n" + common + "  other:\n    name: first\n  other:\n    name: second\n",
                        "jobs:\n" + common + "    name: first\n    name: second\n"]
            for variant in variants:
                with self.subTest(caller=caller, variant=variant[:50]):
                    self.write(caller, variant)
                    self.assert_rejected(caller, "40-character")
            self.write(caller, original)

    def test_hash_suffix_is_part_of_ref_not_a_comment(self):
        branch = self.current + "#mutable-ref"
        self.git("check-ref-format", "--branch", branch)
        self.git("update-ref", "refs/heads/" + branch, self.stale)
        self.assertEqual(self.git("rev-parse", branch), self.stale)
        for caller in (CI, TEMPLATE):
            with self.subTest(caller=caller):
                original = (self.repo / caller).read_text(encoding="utf-8")
                self.repin(caller, branch)
                self.assert_rejected(caller, "40-character")
                self.write(caller, original)

    def test_alternate_mapping_shapes_check_the_actual_pin(self):
        for caller in (CI, TEMPLATE):
            original = (self.repo / caller).read_text(encoding="utf-8")
            self.repin(caller, self.stale)
            stale = (self.repo / caller).read_text(encoding="utf-8")
            variants = [stale.replace("\njobs:\n", '\n"jobs":\n'),
                        stale.replace("  changes:\n", '  "changes":\n'),
                        stale.replace("    uses: ", '    "uses": '),
                        stale.replace("    uses: ", "    uses : ")]
            for variant in variants:
                with self.subTest(caller=caller, variant=variant[:80]):
                    self.write(caller, variant)
                    self.assert_rejected(caller, "differs")
            self.write(caller, original)

    def test_valid_quoted_keys_and_alternate_indentation_pass(self):
        for caller in (CI, TEMPLATE):
            self.write(caller, 'name: fixture\n"jobs":\n  "changes":\n'
                       f'   "uses" : {USES}{self.current}\n'
                       '   name: |\n    uses: this is scalar text, not a ref\n')
        result = self.run_pincheck()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unsafe_yaml_tags_are_rejected(self):
        for caller in (CI, TEMPLATE):
            original = (self.repo / caller).read_text(encoding="utf-8")
            self.write(caller, 'name: !!python/tuple [not, a, workflow, tag]\njobs:\n'
                       f'  changes:\n    uses: {USES}{self.current}\n')
            self.assert_rejected(caller, "invalid or duplicate workflow YAML")
            self.write(caller, original)

    def test_missing_head_source_reports_head_read_failure(self):
        (self.repo / SOURCE).unlink()
        self.commit()
        result = self.run_pincheck()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"cannot read HEAD:{SOURCE}", result.stdout)
        self.assertIn("restore", result.stdout)

    def load_checker(self):
        spec = importlib.util.spec_from_file_location("changes_pins", self.repo / CHECKER)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        checker.ROOT = self.repo
        return checker

    def test_git_timeouts_are_bounded_and_reported(self):
        checker = self.load_checker()
        real_run = subprocess.run
        for stage in ("show", "cat-file"):
            with self.subTest(stage=stage):
                def run(command, **kwargs):
                    self.assertEqual(kwargs.get("timeout"), 30)
                    if command[3] == stage:
                        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                    return real_run(command, env=self.env, **kwargs)

                output = io.StringIO()
                with mock.patch.object(checker.subprocess, "run", side_effect=run):
                    with redirect_stdout(output):
                        self.assertEqual(checker.main(), 1)
                self.assertIn("timed out after 30 seconds", output.getvalue())
                if stage == "cat-file":
                    self.assertIn(CI, output.getvalue())

    def test_failed_head_read_cannot_succeed_with_matching_stdout(self):
        checker = self.load_checker()
        real_run = subprocess.run
        current = (self.repo / SOURCE).read_bytes()

        def run(command, **kwargs):
            if command[-2:] == ["show", f"HEAD:{SOURCE}"]:
                return subprocess.CompletedProcess(command, 1, current, b"injected read error")
            return real_run(command, env=self.env, **kwargs)

        output = io.StringIO()
        with mock.patch.object(checker.subprocess, "run", side_effect=run):
            with redirect_stdout(output):
                self.assertEqual(checker.main(), 1)
        self.assertIn(f"cannot read HEAD:{SOURCE}", output.getvalue())

    def test_git_os_errors_are_reported_for_head_and_caller(self):
        checker = self.load_checker()
        real_run = subprocess.run
        for stage in ("show", "cat-file"):
            with self.subTest(stage=stage):
                def run(command, **kwargs):
                    if command[3] == stage:
                        raise OSError("injected Git execution failure")
                    return real_run(command, env=self.env, **kwargs)

                output = io.StringIO()
                with mock.patch.object(checker.subprocess, "run", side_effect=run):
                    with redirect_stdout(output):
                        self.assertEqual(checker.main(), 1)
                self.assertIn("injected Git execution failure", output.getvalue())
                if stage == "cat-file":
                    self.assertIn(CI, output.getvalue())

    def assert_mandatory_collection(self, text):
        jobs = yaml.safe_load(text)["jobs"]
        gate = jobs["gate-selftest"]
        self.assertNotIn("if", gate)
        self.assertNotIn("continue-on-error", gate)
        collectors = [step for step in gate["steps"]
                      if step.get("run") == "python3 -I .github/scripts/test_changes_pins.py"]
        self.assertEqual(len(collectors), 1)
        self.assertNotIn("if", collectors[0])
        self.assertNotIn("continue-on-error", collectors[0])
        aggregate = jobs["ci-ok"]
        self.assertIn("gate-selftest", aggregate["needs"])
        self.assertEqual(aggregate["if"], "always()")
        self.assertTrue(any("sys.exit(1)" in step.get("run", "") for step in aggregate["steps"]))

    def test_new_suite_is_always_collected_by_required_gate(self):
        self.assert_mandatory_collection((ROOT / CI).read_text(encoding="utf-8"))

    def test_collection_rejects_alternate_optional_key_spellings(self):
        original = (ROOT / CI).read_text(encoding="utf-8")
        run = "        run: python3 -I .github/scripts/test_changes_pins.py\n"
        for key, value in [("if ", "github.event_name == 'workflow_dispatch'"),
                           ('"if"', "github.event_name == 'workflow_dispatch'"),
                           ("'if'", "github.event_name == 'workflow_dispatch'"),
                           ("continue-on-error ", "true"),
                           ('"continue-on-error"', "true"),
                           ("'continue-on-error'", "true")]:
            for scope in ("step", "job"):
                with self.subTest(key=key, scope=scope):
                    if scope == "step":
                        variant = original.replace(run, run + f"        {key}: {value}\n")
                    else:
                        variant = original.replace("  gate-selftest:\n",
                                                   f"  gate-selftest:\n    {key}: {value}\n")
                    with self.assertRaises(AssertionError):
                        self.assert_mandatory_collection(variant)

    def test_stale_test_guard_still_fails(self):
        self.write(GUARD, "changed guard\n")
        self.commit()
        result = self.run_pincheck()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test-guard-pincheck", result.stdout)
        self.assertIn("repin", result.stdout)

    def test_missing_test_guard_pin_still_fails(self):
        self.write(WRAPPER, "jobs: {}\n")
        result = self.run_pincheck()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test-guard-pincheck: no pinned test-guard.yml", result.stdout)
        self.assertNotIn("changes-pincheck:", result.stdout)

    def test_yaml_dependency_is_required_before_both_consumers(self):
        jobs = yaml.safe_load((ROOT / CI).read_text(encoding="utf-8"))["jobs"]
        consumers = {"pincheck": "python3 -I .github/scripts/check_changes_pins.py",
                     "gate-selftest": "python3 -I .github/scripts/test_changes_pins.py"}
        for job, command in consumers.items():
            steps = jobs[job]["steps"]
            installs = [(index, step) for index, step in enumerate(steps)
                        if step.get("id") == "yaml-dependency"]
            self.assertEqual(len(installs), 1)
            index, install = installs[0]
            self.assertLess(index, next(i for i, step in enumerate(steps) if step.get("run") == command))
            self.assertNotIn("if", install)
            self.assertNotIn("continue-on-error", install)
            for required in ("-m venv", "--require-hashes", "--only-binary=:all:",
                             ".github/scripts/requirements-pincheck.txt", "$GITHUB_PATH"):
                self.assertIn(required, install["run"])
        requirements = (ROOT / ".github/scripts/requirements-pincheck.txt").read_text(encoding="utf-8")
        self.assertIn("PyYAML==6.0.3", requirements)
        self.assertEqual(len(re.findall(r"--hash=sha256:[0-9a-f]{64}", requirements)), 2)
        self.assertEqual(yaml.__version__, "6.0.3")

    def assert_mandatory_pincheck(self, text):
        jobs = yaml.safe_load(text)["jobs"]
        pincheck = jobs["pincheck"]
        self.assertNotIn("if", pincheck)
        self.assertNotIn("continue-on-error", pincheck)
        for step in pincheck["steps"]:
            self.assertNotIn("if", step)
            self.assertNotIn("continue-on-error", step)
        aggregate = jobs["ci-ok"]
        self.assertIn("pincheck", aggregate["needs"])
        self.assertEqual(aggregate["if"], "always()")
        self.assertNotIn("continue-on-error", aggregate)
        for step in aggregate["steps"]:
            self.assertNotIn("if", step)
            self.assertNotIn("continue-on-error", step)
        self.assertTrue(any("sys.exit(1)" in step.get("run", "") for step in aggregate["steps"]))

    def test_pincheck_failure_reaches_ci_ok(self):
        self.assert_mandatory_pincheck((ROOT / CI).read_text(encoding="utf-8"))

    def test_pincheck_rejects_skipped_or_optional_enforcement(self):
        original = (ROOT / CI).read_text(encoding="utf-8")
        for key, value in [("if", "github.event_name == 'workflow_dispatch'"),
                           ("if ", "github.event_name == 'workflow_dispatch'"),
                           ('"if"', "github.event_name == 'workflow_dispatch'"),
                           ("continue-on-error", "true"),
                           ("continue-on-error ", "true"),
                           ('"continue-on-error"', "true")]:
            for target in ("job", "Wrapper pin must carry the current reusable",
                           "Changes callers must carry the current reusable"):
                with self.subTest(key=key, target=target):
                    if target == "job":
                        variant = original.replace("  pincheck:\n",
                                                   f"  pincheck:\n    {key}: {value}\n")
                    else:
                        line = f"      - name: {target}\n"
                        variant = original.replace(line, line + f"        {key}: {value}\n")
                    with self.assertRaises(AssertionError):
                        self.assert_mandatory_pincheck(variant)


if __name__ == "__main__":
    unittest.main()
