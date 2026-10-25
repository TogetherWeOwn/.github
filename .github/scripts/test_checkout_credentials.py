#!/usr/bin/env python3
"""Checkout input and offline change-detection acceptance contracts.

Run: python3 -I .github/scripts/test_checkout_credentials.py
Only stdlib and local Git fixtures are used; no credentials or remote requests.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
TARGETS = {
    ("changes", "detect"): {"fetch-depth": "0"},
    ("ci", "lint"): {},
    ("ci", "test-web"): {},
    ("ci", "test-bot"): {},
    ("ci", "secret-scan"): {},
    ("ci", "pincheck"): {"fetch-depth": "0"},
    ("ci", "gate-selftest"): {},
    ("ci", "pr-size-selftest"): {},
    ("ci", "gradual-promote-selftest"): {},
    ("test-guard", "gate"): {"fetch-depth": "0"},
}
AREAS = {"web": ["web/**"], "bot": ["bot/**"], "docs": ["*.md", "docs/**"]}
FULL_RUN = ["package.json", "**/package.json", ".github/**", "shared/**"]


def steps_in(workflow, job):
    """Read the workflows' two-space job / six-space step block shape, not YAML.

    Scope every assertion to a real step so comments or a neighboring checkout
    cannot satisfy an input contract. General YAML validity belongs to actionlint.
    """
    text = (WORKFLOWS / f"{workflow}.yml").read_text(encoding="utf-8")
    jobs = text.split("\njobs:\n", 1)[1]
    match = re.search(rf"(?m)^  {re.escape(job)}:\s*$", jobs)
    if match is None:
        raise AssertionError(f"{workflow}: missing job {job}")
    block = re.split(r"(?m)^  [\w-]+:", jobs[match.end():], maxsplit=1)[0]
    starts = [m.start() for m in re.finditer(r"(?m)^      - ", block)]
    return [block[start:end] for start, end in zip(starts, starts[1:] + [len(block)])]


def checkout_steps(workflow, job):
    result = []
    for step in steps_in(workflow, job):
        uses = re.search(r"(?m)^\s*(?:- )?uses: (actions/checkout@[^\s#]+)", step)
        if uses is not None:
            inputs = dict(re.findall(r"(?m)^          ([\w-]+): ([^\n#]*?)(?:\s+#.*)?$", step))
            result.append((uses.group(1), inputs))
    return result


def detector_script():
    matches = [s for s in steps_in("changes", "detect") if re.search(r"(?m)^        id: match$", s)]
    if len(matches) != 1:
        raise AssertionError("expected exactly one changes detector step")
    header = re.search(r"(?m)^        run: \|[-+]?\n", matches[0])
    if header is None:
        raise AssertionError("changes detector must have a literal run block")
    lines = []
    for line in matches[0][header.end():].splitlines(keepends=True):
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line)
    script = textwrap.dedent("".join(lines))
    if not script.strip():
        raise AssertionError("changes detector run block is empty")
    return script


class CheckoutCredentialTests(unittest.TestCase):
    def test_ten_target_checkouts_explicitly_disable_persistence(self):
        self.assertEqual(len(TARGETS), 10)
        for workflow, job in TARGETS:
            with self.subTest(workflow=workflow, job=job):
                checkouts = checkout_steps(workflow, job)
                self.assertTrue(checkouts, "missing checkout")
                self.assertEqual(checkouts[0][1].get("persist-credentials"), "false")

    def test_existing_checkout_pins_refs_and_fetch_depths_are_preserved(self):
        for (workflow, job), expected in TARGETS.items():
            with self.subTest(workflow=workflow, job=job):
                checkouts = checkout_steps(workflow, job)
                self.assertEqual(len(checkouts), 2 if job == "gradual-promote-selftest" else 1)
                uses, inputs = checkouts[0]
                self.assertEqual(uses, CHECKOUT)
                self.assertEqual({k: v for k, v in inputs.items() if k != "persist-credentials"}, expected)
        self.assertEqual(checkout_steps("ci", "gradual-promote-selftest")[1:], [(CHECKOUT, {
            "repository": "TogetherWeOwn/two-web-next",
            "ref": "d09b4bee57ad7effdaa79d0765574c3a60e8068e",
            "path": "two-web-next",
            "persist-credentials": "false",
        })])
        self.assertEqual(checkout_steps("ci", "pr-attestation-selftest"), [(CHECKOUT, {
            "fetch-depth": "0", "persist-credentials": "false",
        })])

    def test_test_guard_api_token_remains_step_scoped(self):
        steps = steps_in("test-guard", "gate")
        token_steps = [s for s in steps if re.search(r"(?m)^          GH_TOKEN:", s)]
        self.assertEqual(len(token_steps), 1)
        self.assertRegex(token_steps[0], r"(?m)^          GH_TOKEN: \$\{\{ github.token \}\}$")
        self.assertIn("gh", token_steps[0])
        for step in steps:
            if "uses: actions/checkout@" in step:
                self.assertNotIn("GH_TOKEN:", step)


class OfflineChangesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="checkout-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        home = self.root / "home"
        home.mkdir()
        self.env = {
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.defpath]),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home),
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            # Reject every remote transport before any network/credential access.
            "GIT_ALLOW_PROTOCOL": "",
        }
        self.git("init", "--template=", "-q", "-b", "main")
        self.git("config", "user.name", "Acceptance Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.base = self.commit({"README.md": "base\n", "web/base.txt": "web seed\n"})
        self.git("remote", "add", "origin", "https://example.invalid/unavailable.git")
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        self.git("checkout", "--detach", "-q", self.base)

    def git(self, *args):
        run = subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                             text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, f"git {args}: {run.stderr}")
        return run.stdout.strip()

    def commit(self, paths):
        for name, content in paths.items():
            path = self.repo / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        self.git("add", "--all")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture change")
        return self.git("rev-parse", "HEAD")

    def detect(self, *, base_ref="", event_base="", areas=None, full_globs=None):
        output = self.root / "output"
        output.write_text("", encoding="utf-8")
        trace = self.root / "trace"
        trace.write_text("", encoding="utf-8")
        env = {**self.env, "RUNNER_TEMP": str(self.root), "GITHUB_OUTPUT": str(output),
               "GIT_TRACE": str(trace), "BASE_REF": base_ref, "EVENT_BASE": event_base,
               "AREAS_JSON": json.dumps(AREAS if areas is None else areas),
               "FULL_RUN_JSON": json.dumps(FULL_RUN if full_globs is None else full_globs),
               "DOCS_AREAS_JSON": '["docs"]'}
        run = subprocess.run(["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", detector_script()],
                             cwd=self.repo, env=env, text=True, capture_output=True, timeout=10)
        values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
        execution = trace.read_text(encoding="utf-8")
        self.assertNotRegex(execution, r"built-in: git (?:fetch|pull|push|ls-remote)\b",
                            "detector must use pre-fetched refs, not remote operations")
        return run, values, execution

    def assert_outputs(self, expected_areas, full_run=False, docs_only=False, **inputs):
        run, values, trace = self.detect(**inputs)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertRegex(trace, r"built-in: git merge-base\b")
        self.assertRegex(trace, r"built-in: git diff\b")
        self.assertEqual(set(values), {"areas", "full_run", "docs_only"})
        self.assertEqual(json.loads(values["areas"]), expected_areas)
        self.assertEqual(values["full_run"], str(full_run).lower())
        self.assertEqual(values["docs_only"], str(docs_only).lower())

    def test_no_changes_is_not_docs_only(self):
        self.assert_outputs({"web": False, "bot": False, "docs": False})

    def test_root_and_nested_non_ascii_docs_are_docs_only(self):
        self.commit({"README.md": "updated\n", "docs/指南/über uns.md": "docs\n"})
        self.assert_outputs({"web": False, "bot": False, "docs": True}, docs_only=True)

    def test_non_ascii_whitespace_and_newline_paths_preserve_areas(self):
        self.commit({"web/café/one\nfile.ts": "web\n", "bot/日本語\tfile.rs": "bot\n"})
        self.assert_outputs({"web": True, "bot": True, "docs": False})

    def test_classified_root_and_nested_full_run_files_force_everything(self):
        areas = {**AREAS, "web": ["web/**", "package.json", "**/package.json", ".github/**"]}
        for name in ("package.json", "packages/ui/package.json", ".github/workflows/ci.yml"):
            with self.subTest(path=name):
                self.git("checkout", "--detach", "-q", self.base)
                self.commit({name: "{}\n"})
                self.assert_outputs({"web": True, "bot": False, "docs": False}, full_run=True, areas=areas)

    def test_docs_full_run_match_is_not_docs_only(self):
        self.commit({"docs/force.md": "docs\n"})
        self.assert_outputs({"web": False, "bot": False, "docs": True}, full_run=True,
                            full_globs=["docs/force.md"])

    def test_unclassified_root_path_fails_safe_to_full_run(self):
        self.commit({"unmapped file.txt": "unknown\n"})
        self.assert_outputs({"web": False, "bot": False, "docs": False}, full_run=True)

    def test_renamed_source_still_selects_its_original_area(self):
        self.commit({"web/base.txt": None, "docs/moved.md": "web seed\n"})
        self.assert_outputs({"web": True, "bot": False, "docs": True})

    def test_default_main_uses_merge_base_not_divergent_base_tip(self):
        head = self.commit({"web/feature.ts": "feature\n"})
        self.git("checkout", "--detach", "-q", self.base)
        base_tip = self.commit({"bot/base-only.rs": "base only\n"})
        self.git("update-ref", "refs/remotes/origin/main", base_tip)
        self.git("checkout", "--detach", "-q", head)
        self.assert_outputs({"web": True, "bot": False, "docs": False})

    def test_explicit_and_event_alternate_refs_use_prefetched_history(self):
        alternate = self.commit({"bot/alternate-base.rs": "base only\n"})
        self.git("update-ref", "refs/remotes/origin/release/next", alternate)
        self.commit({"web/feature.ts": "feature\n"})
        for inputs in ({"base_ref": "release/next", "event_base": "missing"},
                       {"event_base": "release/next"}):
            with self.subTest(**inputs):
                self.assert_outputs({"web": True, "bot": False, "docs": False}, **inputs)

    def test_missing_explicit_or_event_ref_never_falls_back_to_main(self):
        self.commit({"docs/change.md": "docs\n"})
        for inputs in ({"base_ref": "missing", "event_base": "main"}, {"event_base": "missing"}):
            with self.subTest(**inputs):
                run, values, trace = self.detect(**inputs)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("origin/missing", run.stderr)
                self.assertRegex(trace, r"built-in: git merge-base\b")
                self.assertEqual(values, {}, "failure must not publish an empty-change success")

    def test_missing_default_base_fails_closed(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        run, values, trace = self.detect()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("origin/main", run.stderr)
        self.assertRegex(trace, r"built-in: git merge-base\b")
        self.assertEqual(values, {})

    def test_unrelated_base_history_fails_closed(self):
        tree = self.git("write-tree")
        unrelated = self.git("-c", "commit.gpgsign=false", "commit-tree", tree, "-m", "unrelated root")
        self.git("update-ref", "refs/remotes/origin/main", unrelated)
        run, values, trace = self.detect()
        self.assertNotEqual(run.returncode, 0)
        self.assertRegex(trace, r"built-in: git merge-base\b")
        self.assertNotRegex(trace, r"built-in: git diff\b")
        self.assertEqual(values, {})

    def test_diff_error_is_not_reported_as_no_changes(self):
        self.commit({"web/change.ts": "changed\n"})
        self.git("config", "diff.algorithm", "invalid-fixture-algorithm")
        run, values, trace = self.detect()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("diff.algorithm", run.stderr)
        self.assertRegex(trace, r"built-in: git diff\b")
        self.assertEqual(values, {})


if __name__ == "__main__":
    unittest.main()
