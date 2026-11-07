"""Offline fixtures execute the exact read-only pilot and advisory label heredocs.

Full verification runs in GitHub Actions (pr-standards selftest), never locally.
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/pr-standards-required.yml"
LABEL_WORKFLOW = ROOT / ".github/workflows/sensitive-path-label.yml"
PILOT = "TogetherWeOwn/paperclip-plugin-sandbox-boat"
SECTIONS = ("Testing", "Risk", "How to revert", "What these checks would miss")
BODY = "\n\n".join(f"## {name}\n\nN/A — source-only fixture, no runtime change." for name in SECTIONS)


def script(path, delimiter):
    match = re.search(rf"<<'{delimiter}'\n(.*?)\n\s*{delimiter}\n", path.read_text(), re.S)
    assert match, f"Missing {delimiter} heredoc"
    return textwrap.dedent(match[1])


SCRIPT = script(WORKFLOW, "PILOT_PY")
LABEL_SCRIPT = script(LABEL_WORKFLOW, "LABEL_PY")


def changed(line, path="src/unit.test.ts", previous="", removed=""):
    diff = "@@ -1,3 +1,4 @@\n"
    if previous:
        diff += f" {previous}\n"
    if removed:
        diff += f"-{removed}\n"
    diff += "\n".join("+" + part for part in line.splitlines()) + "\n"
    return {"filename": path, "status": "modified", "changes": 1, "patch": diff}


def run(body=BODY, files=(), repo=PILOT, head_repo=None, draft="false", author="contributor",
        event="pull_request", label=False, raw=None):
    with tempfile.TemporaryDirectory() as directory:
        summary = Path(directory) / "summary.md"
        env = {"PATH": os.environ.get("PATH", ""), "EVENT": event, "BODY": body,
               "AUTHOR": author, "DRAFT": draft, "REPO": repo,
               "HEAD_REPO": repo if head_repo is None else head_repo,
               "HEAD_SHA": "a" * 40, "PR_NUMBER": "7", "GH_TOKEN": "",
               "FILES_JSON": raw if raw is not None else json.dumps(list(files)),
               "GITHUB_STEP_SUMMARY": str(summary)}
        result = subprocess.run([sys.executable, "-I", "-c", LABEL_SCRIPT if label else SCRIPT],
                                env=env, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout + result.stderr, summary.read_text() if summary.exists() else ""


class Contract(unittest.TestCase):
    def test_all_sections_and_na_with_reason_pass(self):
        self.assertEqual(run()[0], 0)

    def test_each_missing_empty_or_placeholder_section_fails(self):
        for section in SECTIONS:
            for value in (None, "", "<!-- fill me -->", "-", "<reason>", "TODO", "TBD", "N/A"):
                body = "\n\n".join(f"## {name}\n{value if name == section else 'Evidence recorded.'}"
                                     for name in SECTIONS if name != section or value is not None)
                with self.subTest(section=section, value=value):
                    code, out, summary = run(body=body)
                    self.assertEqual(code, 1, out)
                    self.assertIn("::error title=PR contract", out)
                    self.assertIn(section, summary)

    def test_comments_and_fenced_headings_do_not_supply_contract(self):
        for body in (f"<!-- {BODY} -->", f"```md\n{BODY}\n```", f"~~~md\n{BODY}\n~~~"):
            self.assertEqual(run(body=body)[0], 1)

    def test_body_text_is_not_logged(self):
        code, out, summary = run(body="sensitive-body-never-log")
        self.assertEqual(code, 1)
        self.assertNotIn("sensitive-body-never-log", out + summary)

    def test_warn_only_contexts(self):
        for kwargs in ({"repo": "TogetherWeOwn/other"}, {"draft": "true"},
                       {"author": "dependabot[bot]"}, {"author": "renovate[bot]"},
                       {"head_repo": "contributor/fork"}, {"head_repo": ""}):
            with self.subTest(kwargs=kwargs):
                code, out, _ = run(body="", files=[changed("test.only('focus', () => {});")], **kwargs)
                self.assertEqual(code, 0, out)
                self.assertIn("::warning", out)
                self.assertNotIn("::error", out)

    def test_non_pr_events_do_not_fetch_or_enforce(self):
        code, out, _ = run(body="", raw="bad-json", event="merge_group")
        self.assertEqual(code, 0, out)


class AddedLineSkips(unittest.TestCase):
    def test_every_unreasoned_marker_fails(self):
        cases = [("it.skip('test name', () => {});", "unit.test.ts"),
                 ("test.skip('case', () => {});", "unit.spec.js"),
                 ("describe.skip('suite', () => {});", "tests/unit.ts"),
                 ("xit('case', () => {});", "__tests__/unit.tsx"),
                 ("xdescribe('suite', () => {});", "unit.spec.mts"),
                 ("test.only('case', () => {});", "unit.test.cts"),
                 ("@pytest.mark.skip", "test_unit.py"),
                 ("@pytest.mark.skipif(True)", "unit_test.py"),
                 ("@pytest.mark.skip(reason='   ')", "tests/unit.py"),
                 ("pytest.skip()", "test_unit.py"),
                 ("pytest.skip('')", "test_unit.py"),
                 ("#[ignore]", "src/lib.rs"),
                 ('#[ignore = ""]', "tests/unit.rs"),
                 ("t.Skip()", "unit_test.go")]
        for line, path in cases:
            with self.subTest(line=line):
                code, out, _ = run(files=[changed(line, path)])
                self.assertEqual(code, 1, out)
                self.assertIn("::error title=Test skip lint", out)
                self.assertIn("file=" + path, out)
                self.assertNotIn(line, out)

    def test_reasoned_language_forms_pass(self):
        cases = [("test.skip('case', () => {}); // skip-reason: platform issue", "unit.test.ts"),
                 ("@pytest.mark.skip(reason='platform issue')", "test_unit.py"),
                 ("@pytest.mark.skipif(True, reason='platform issue')", "test_unit.py"),
                 ("pytest.skip('platform issue')", "test_unit.py"),
                 ("pytest.skip(reason='platform issue')", "test_unit.py"),
                 ('#[ignore = "platform issue"]', "src/lib.rs"),
                 ('t.Skip("platform issue")', "unit_test.go")]
        for line, path in cases:
            with self.subTest(line=line):
                result = run(files=[changed(line, path)])
                self.assertEqual(result[0], 0, result[1])

    def test_previous_context_or_added_comment_can_justify_skip(self):
        for previous in ("// skip-reason: waiting for platform fix", "/* skip-reason: tracked issue */"):
            self.assertEqual(run(files=[changed("test.skip('case', () => {});", previous=previous)])[0], 0)
        self.assertEqual(run(files=[changed("// skip-reason: tracked issue\nit.skip('case', () => {});")])[0], 0)

    def test_empty_comment_or_string_lookalike_is_not_a_reason(self):
        for previous in ("// skip-reason: ", "/* skip-reason: */", 'const message = "// skip-reason: fake";'):
            self.assertEqual(run(files=[changed("it.skip('case', () => {});", previous=previous)])[0], 1)

    def test_only_is_not_excused_by_reason(self):
        self.assertEqual(run(files=[changed("test.only('case', () => {}); // skip-reason: tracked")])[0], 1)

    def test_deleted_or_context_markers_are_not_errors(self):
        for file in ({"filename": "unit.test.ts", "status": "removed"},
                     changed("test('valid', () => {});", previous="it.only('old', fn);"),
                     changed("test('valid', () => {});", removed="it.only('old', fn);")):
            code, out, _ = run(files=[file])
            self.assertEqual(code, 0, out)
            self.assertNotIn("::error", out)

    def test_non_test_files_are_not_linted(self):
        self.assertEqual(run(files=[changed("it.only('sample');", "docs/example.ts")])[0], 0)

    def test_comment_and_string_marker_examples_are_not_code(self):
        for line in ("// it.only('example')", 'const marker = "test.only(";',
                     'expect(message).toContain("it.skip(");', "/* test.only('x') */"):
            result = run(files=[changed(line)])
            self.assertEqual(result[0], 0, result[1])

    def test_multiline_reason_arguments_pass(self):
        for line, path in (("pytest.skip(\n    'platform issue'\n)", "test_unit.py"),
                           ("@pytest.mark.skipif(\n    True,\n    reason='platform issue'\n)", "test_unit.py"),
                           ('t.Skip(\n    "platform issue"\n)', "unit_test.go"),
                           ('#[ignore =\n    "platform issue"\n]', "src/lib.rs")):
            result = run(files=[changed(line, path)])
            self.assertEqual(result[0], 0, result[1])

    def test_missing_patch_fails_closed_only_for_test_diff(self):
        file = {"filename": "unit.test.ts", "status": "modified", "changes": 20}
        self.assertEqual(run(files=[file])[0], 1)
        self.assertEqual(run(files=[file], draft="true")[0], 0)
        self.assertEqual(run(files=[{**file, "filename": "image.png"}])[0], 0)

    def test_renamed_tests_are_scanned(self):
        file = {**changed("test.only('focus');", "unit.ts"), "previous_filename": "unit.test.ts", "status": "renamed"}
        self.assertEqual(run(files=[file])[0], 1)

    def test_deleted_tests_and_lowered_coverage_are_summary_warnings(self):
        for file in ({"filename": "unit.test.ts", "status": "removed"},
                     changed("// replacement", removed="it('old case', fn);"),
                     changed("coverage: { lines: 70 }", "vitest.config.ts", removed="coverage: { lines: 90 }")):
            code, out, summary = run(files=[file])
            self.assertEqual(code, 0, out)
            self.assertIn("::warning", out)
            self.assertIn("advisory", summary)
            self.assertNotIn("::error", out)

    def test_bad_file_response_fails_closed_without_echo(self):
        code, out, _ = run(raw="not-json-sensitive-data")
        self.assertEqual(code, 1)
        self.assertIn("PR diff unavailable", out)
        self.assertNotIn("not-json-sensitive-data", out)


class ExactHeadFiles(unittest.TestCase):
    def namespace(self):
        result = {"__name__": "pilot_fixture"}
        exec(SCRIPT.split('if os.environ.get("EVENT") != "pull_request":')[0], result)
        return result

    def test_complete_exact_head_listing(self):
        with patch.dict(os.environ, {"HEAD_SHA": "a" * 40}, clear=True):
            namespace = self.namespace()
            state = {"head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}, "changed_files": 1}
            file = changed("test('valid', fn);")
            replies = iter([state, [file], state])
            namespace["api"] = lambda suffix: next(replies)
            self.assertEqual(namespace["files"](), [file])

    def test_stale_or_incomplete_diff_is_refused(self):
        state = {"head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}, "changed_files": 1}
        for after in ({**state, "head": {"sha": "c" * 40}},
                      {**state, "base": {"sha": "c" * 40}},
                      {**state, "changed_files": 2}):
            with self.subTest(after=after), patch.dict(os.environ, {"HEAD_SHA": "a" * 40}, clear=True):
                namespace = self.namespace()
                replies = iter([state, [changed("test('valid', fn);")], after])
                namespace["api"] = lambda suffix: next(replies)
                with self.assertRaises(ValueError):
                    namespace["files"]()
        with patch.dict(os.environ, {"HEAD_SHA": "f" * 40}, clear=True):
            namespace = self.namespace()
            namespace["api"] = lambda suffix: state
            with self.assertRaises(ValueError):
                namespace["files"]()

    def test_api_failure_is_not_a_clean_diff(self):
        with patch.dict(os.environ, {"HEAD_SHA": "a" * 40}, clear=True):
            namespace = self.namespace()
            def unavailable(suffix):
                raise OSError("fixture unavailable")
            namespace["api"] = unavailable
            with self.assertRaises(OSError):
                namespace["files"]()


class SensitivePaths(unittest.TestCase):
    def test_each_sensitive_path_gets_advisory_label(self):
        for path in (".github/workflows/ci.yml", ".coderabbit.yaml", ".devin.json", "review-rules/test.yml",
                     ".github/CODEOWNERS", "rulesets/main.json", "src/auth/session.ts", "src/client_token.ts",
                     "src/secrets.ts", "migrations/001.sql", "db/migrations/001.sql", "drizzle/schema.ts",
                     "wrangler.production.toml", "tools/deploy.sh", "install.sh", ".github/release.yml"):
            with self.subTest(path=path):
                code, out, _ = run(files=[{"filename": path}], label=True)
                self.assertEqual(code, 0, out)
                self.assertIn("label applied", out)

    def test_non_sensitive_paths_and_empty_diff_do_not_label(self):
        for files in ([], [{"filename": "README.md"}], [{"filename": "src/editor.ts"}]):
            code, out, _ = run(files=files, label=True)
            self.assertEqual(code, 0, out)
            self.assertNotIn("label applied", out)

    def test_rename_out_of_sensitive_path_is_still_sensitive(self):
        result = run(files=[{"filename": "src/client.ts", "previous_filename": "src/auth/client.ts"}], label=True)
        self.assertIn("label applied", result[1])

    def test_fork_and_nonpilot_get_annotations_not_writes(self):
        for kwargs in ({"head_repo": "contributor/fork"}, {"repo": "TogetherWeOwn/other"}):
            code, out, _ = run(files=[{"filename": "src/auth/session.ts"}], label=True, **kwargs)
            self.assertEqual(code, 0, out)
            self.assertIn("::warning", out)
            self.assertIn("no label write", out)
            self.assertNotIn("label applied", out)

    def test_label_errors_never_fail_gate(self):
        code, out, _ = run(label=True, raw="bad-json")
        self.assertEqual(code, 0, out)
        self.assertIn("::warning", out)


class ShapeAndCompatibility(unittest.TestCase):
    def test_read_only_required_workflow_never_executes_pr_code(self):
        text = WORKFLOW.read_text()
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("actions/checkout", text)
        self.assertNotIn(": write", text)
        self.assertEqual(text.count("\n    name: pr-standards\n"), 1)
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", text)

    def test_label_has_only_pr_write_and_no_checkout(self):
        text = LABEL_WORKFLOW.read_text()
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("actions/checkout", text)
        self.assertIn("  pull-requests: write", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("issues: write", text)
        self.assertIn("  workflow_call:", text)
        self.assertIn("  pull_request:", text)

    def test_template_contract_has_no_duplicate_legacy_headings(self):
        template = (ROOT / ".github/pull_request_template.md").read_text()
        for section in SECTIONS:
            self.assertEqual(template.count("## " + section + "\n"), 1)
        self.assertNotIn("## Verification", template)
        self.assertNotIn("## Risks", template)
        self.assertIn("## Attestation", template)

    def test_attestation_parser_ignores_renamed_outer_sections(self):
        # Parser is independent of Verification/Risks; no runtime change needed.
        namespace = {"__name__": "attestation_fixture", "__file__": str(ROOT / ".github/scripts/pr_attestation.py")}
        exec(compile((ROOT / ".github/scripts/pr_attestation.py").read_text(), "attestation", "exec"), namespace)
        attestation = ("## Attestation\nrisk_tier: T2\n### Claims\n- C1: Documentation only.\n"
                       "### Tests\n- none | reason=Documentation-only; no behavior changed.\n"
                       "### High-risk checklist\n" + "\n".join(f"- [ ] D{i}: N/A, docs only." for i in range(1, 9)))
        before = namespace["parse_attestation"](attestation + "\n## Risks\nLow.\n")
        after = namespace["parse_attestation"]("## Testing\nNo runtime.\n" + attestation + "\n## Risk\nLow.\n## How to revert\nRevert.\n")
        self.assertEqual(before, after)

    def test_pr_lint_accepts_new_and_legacy_testing_heading(self):
        lint = script(ROOT / "workflow-templates/pr-lint.yml", "PYEOF")
        for heading in ("Testing", "Verification"):
            env = {"PATH": os.environ.get("PATH", ""), "EVENT": "pull_request", "TITLE": "ci: test contract",
                   "BODY": f"## {heading}\nExact-head Actions evidence recorded for this source-only fixture.",
                   "AUTHOR": "contributor", "PR_STANDARDS_MODE": "error"}
            result = subprocess.run([sys.executable, "-I", "-c", lint], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
