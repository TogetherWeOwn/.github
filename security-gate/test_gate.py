#!/usr/bin/env python3
"""Offline behavior regressions; run with python3 -I security-gate/test_gate.py."""
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("trusted_gate", ROOT / "gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

VEX = {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "govulncheck/vex:" + "a" * 64,
       "author": "govulncheck", "timestamp": "2026-11-04T00:00:00Z", "version": 1, "statements": None,
       "tooling": "https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck"}
SARIF = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "scanner"}}, "results": []}]}
META = {"dependencies": [{"purl": "pkg:golang/example.org/library@v1.0.0", "version": "v1.0.0",
                           "upstream_commit": "a" * 40}]}


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.work = self.root / "work"
        self.work.mkdir()
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True).stdout

    def put(self, path, content="x"):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return gate.inventory(self.repo)

    def test_nested_discovery_and_workspace_lock(self):
        for path in ("go.mod", "nested/go.mod", "Cargo.toml", "Cargo.lock",
                     "nested/ws/Cargo.toml", "nested/ws/Cargo.lock", ".github/workflows/a.yaml",
                     "actions/local/action.yml"):
            self.put(path)
        found = gate.discover(self.commit())
        self.assertEqual(found["go"], ["go.mod", "nested/go.mod"])
        self.assertEqual(found["cargo"], ["Cargo.lock", "nested/ws/Cargo.lock"])
        self.assertEqual(len(found["workflows"]), 2)

    def test_orphan_cargo_lock_fails(self):
        self.put("nested/Cargo.lock")
        with self.assertRaisesRegex(gate.GateError, "without tracked Cargo.toml"):
            gate.discover(self.commit())

    def test_untracked_and_deleted_files_do_not_count(self):
        self.put("README.md")
        entries = self.commit()
        self.put("go.mod")
        self.assertEqual(gate.discover(entries)["go"], [])
        self.put("vendor/x/file")
        self.commit()
        self.git("rm", "-qr", "vendor")
        self.git("commit", "-qm", "remove tree")
        self.assertEqual(gate.validate_vendor(self.repo, gate.inventory(self.repo)), [])

    def test_worktree_tampering_does_not_change_metadata(self):
        self.put("vendor/file")
        self.put("vendor/VENDORED.json", json.dumps(META))
        entries = self.commit()
        (self.repo / "vendor/VENDORED.json").unlink()
        (self.repo / "vendor/VENDORED.json").symlink_to("/definitely/not/read")
        self.assertEqual(gate.validate_vendor(self.repo, entries), ["vendor"])

    def test_every_nested_vendor_tree_needs_own_metadata(self):
        self.put("src/vendor/VENDORED.json", json.dumps(META))
        self.put("src/vendor/lib/third_party/file")
        entries = self.commit()
        with self.assertRaisesRegex(gate.GateError, "src/vendor/lib/third_party/VENDORED.json"):
            gate.validate_vendor(self.repo, entries)
        self.put("src/vendor/lib/third_party/VENDORED.json", json.dumps(META))
        self.assertEqual(len(gate.validate_vendor(self.repo, self.commit())), 2)

    def test_missing_vendor_metadata(self):
        self.put("third_party/library/file")
        with self.assertRaisesRegex(gate.GateError, "missing third_party/VENDORED.json"):
            gate.validate_vendor(self.repo, self.commit())

    def test_bad_metadata_shapes_fail(self):
        invalid = [None, [], {}, {"dependencies": []}, {"dependencies": [None]},
                   {"dependencies": [{"purl": "pkg:npm/a", "version": "1", "upstream_commit": "main"}]}]
        for key, value in (("purl", "https://example.org"), ("purl", 42), ("version", " "),
                           ("version", None), ("upstream_commit", "abc"), ("extra", "field")):
            data = copy.deepcopy(META)
            data["dependencies"][0][key] = value
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data):
                self.put("vendor/VENDORED.json", json.dumps(data))
                entries = self.commit()
                with self.assertRaises(gate.GateError):
                    gate.validate_vendor(self.repo, entries)

    def test_symlink_metadata_and_directory_fail(self):
        for path in ("vendor", "vendor/VENDORED.json", ".github/workflows/a.yml", "go.mod"):
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to("/outside-checkout")
                with self.assertRaisesRegex(gate.GateError, "symlink/submodule"):
                    self.commit()
                self.git("rm", "--cached", path)
                target.unlink()

    def test_gitlink_cannot_hide_vendored_or_modules(self):
        self.put("README.md")
        self.commit()
        oid = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{oid},third_party/lib")
        self.git("commit", "-qm", "gitlink")
        with self.assertRaisesRegex(gate.GateError, "symlink/submodule"):
            gate.inventory(self.repo)

    def test_snapshot_uses_git_data_not_pr_executable_files(self):
        for path in ("go.mod", "json.py", "sitecustomize.py", "scripts/build", ".cargo/config.toml",
                     "nested/.cargo/config", "rust-toolchain.toml"):
            self.put(path, "raise RuntimeError('PR code must not execute')")
        entries = self.commit()
        self.put("go.mod", "untracked mutation")
        dest = gate.snapshot(self.repo, entries, self.work / "data")
        self.assertEqual((dest / "go.mod").read_text(), "raise RuntimeError('PR code must not execute')")
        self.assertFalse((dest / ".cargo/config.toml").exists())
        self.assertFalse((dest / "nested/.cargo/config").exists())
        self.assertFalse((dest / "rust-toolchain.toml").exists())
        self.assertEqual((dest / "scripts/build").stat().st_mode & 0o111, 0)

    def test_inventory_limits_fail_closed(self):
        self.put("go.mod")
        self.commit()
        with patch.object(gate, "MAX_FILES", 0), self.assertRaises(gate.GateError):
            gate.inventory(self.repo)
        with patch.object(gate, "MAX_BYTES", 0), self.assertRaises(gate.GateError):
            gate.inventory(self.repo)
        with patch.object(gate, "MAX_MODULES", 0), self.assertRaises(gate.GateError):
            gate.discover(gate.inventory(self.repo))

    def test_nonapplicable_scans_preserve_explicit_evidence(self):
        self.put("README.md")
        entries = self.commit()
        found = gate.discover(entries)
        self.assertEqual(gate.dependency_scans(self.repo, entries, found, self.work, self.evidence,
                                               self.root, [])[0]["status"], "not-applicable")
        self.assertEqual(gate.workflow_scans(self.repo, entries, found, self.work, self.evidence,
                                             self.root)[0]["status"], "not-applicable")

    def test_go_targets_pass_as_data_and_all_modules_are_scanned(self):
        self.put("go.mod")
        self.put("nested/go.mod")
        entries = self.commit()
        build = [{"goos": "linux", "goarch": "arm64", "tags": ["production", "netgo"]},
                 {"goos": "windows", "goarch": "amd64", "tags": []}]
        with patch.object(gate, "run_scan", return_value=(0, VEX)) as scan, \
                patch.dict(os.environ, {"GITHUB_TOKEN": "never-forward", "GOFLAGS": "bad"}):
            results = gate.dependency_scans(self.repo, entries, gate.discover(entries), self.work,
                                            self.evidence, self.root, build)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r["status"] == "passed" for r in results))
        for call in scan.call_args_list:
            command, cwd, _, _, env = call.args
            self.assertIn("-scan=symbol", command)
            self.assertIn("-format=openvex", command)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertEqual(env["GOTOOLCHAIN"], "local")
            self.assertEqual(env["GOFLAGS"], "")
            self.assertTrue(cwd.is_relative_to(self.work))
        self.assertIn("-tags=production,netgo", scan.call_args_list[0].args[0])

    def test_cargo_uses_controlled_advisory_policy_and_workspace(self):
        self.put("nested/Cargo.lock")
        self.put("nested/Cargo.toml")
        entries = self.commit()
        with patch.object(gate, "run_scan", return_value=(0, SARIF)) as scan:
            result = gate.dependency_scans(self.repo, entries, gate.discover(entries), self.work,
                                           self.evidence, self.root, [])
        self.assertEqual(result[0]["status"], "passed")
        command = scan.call_args.args[0]
        self.assertEqual(command[-2:], ["check", "advisories"])
        for flag in ("--workspace", "--locked", "--all-features", str(self.work / "deny.toml")):
            self.assertIn(flag, command)

    def test_failure_does_not_skip_remaining_language_scanners(self):
        for path in ("go.mod", "Cargo.toml", "Cargo.lock"):
            self.put(path)
        entries = self.commit()
        with patch.object(gate, "run_scan", side_effect=[gate.GateError("missing output"), (0, SARIF)]):
            result = gate.dependency_scans(self.repo, entries, gate.discover(entries), self.work,
                                           self.evidence, self.root,
                                           [{"goos": "linux", "goarch": "amd64", "tags": []}])
        self.assertEqual([r["status"] for r in result], ["failed", "passed"])

    def test_policy_runs_both_tools_online_with_trusted_config(self):
        self.put(".github/workflows/ci.yml")
        self.put(".github/pinact.yaml", "ignore_actions: ['.*']")
        self.put("zizmor.yml", "ignore everything")
        entries = self.commit()
        with patch.object(gate, "run_scan", side_effect=[(0, []), (0, SARIF)]) as scan, \
                patch.dict(os.environ, {"GITHUB_TOKEN": "present", "ZIZMOR_OFFLINE": "true"}):
            results = gate.workflow_scans(self.repo, entries, gate.discover(entries), self.work,
                                          self.evidence, self.root)
        self.assertEqual([r["status"] for r in results], ["passed", "passed"])
        zizmor, pinact = [c.args for c in scan.call_args_list]
        self.assertIn("--no-ignores", zizmor[0])
        self.assertIn("--no-config", zizmor[0])
        self.assertNotIn("ZIZMOR_OFFLINE", zizmor[4])
        self.assertEqual(zizmor[4]["GITHUB_TOKEN"], "present")
        self.assertIn("--check", pinact[0])
        self.assertIn("--verify-comment", pinact[0])
        self.assertEqual((self.work / "pinact.yaml").read_text(), "version: 3\n")
        self.assertFalse((self.work / "workflow-data/.github/pinact.yaml").exists())

    def test_policy_missing_token_fails(self):
        self.put(".github/workflows/ci.yml")
        entries = self.commit()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(gate.GateError):
            gate.workflow_scans(self.repo, entries, gate.discover(entries), self.work, self.evidence,
                                self.root)

    def test_policy_infrastructure_error_not_green(self):
        self.put(".github/workflows/ci.yml")
        entries = self.commit()
        with patch.dict(os.environ, {"GITHUB_TOKEN": "present"}), \
                patch.object(gate, "run_scan", side_effect=[(1, []), (0, {})]):
            results = gate.workflow_scans(self.repo, entries, gate.discover(entries), self.work,
                                          self.evidence, self.root)
        self.assertEqual([r["status"] for r in results], ["failed", "failed"])

    def test_missing_empty_truncated_and_nonzero_scanner_results(self):
        for index, program in enumerate(("pass", "print('{')", "print('{}')", "print('[]'); raise SystemExit(2)")):
            name = f"scanner-{index}"
            if index < 2:
                with self.assertRaises((gate.GateError, ValueError)):
                    gate.run_scan([sys.executable, "-I", "-c", program], self.work, self.evidence, name)
            else:
                rc, _ = gate.run_scan([sys.executable, "-I", "-c", program], self.work, self.evidence, name)
                self.assertEqual(rc, 0 if index == 2 else 2)
            self.assertTrue((self.evidence / (name + ".status.json")).exists())
            self.assertTrue((self.evidence / (name + ".stderr")).exists())

    def test_timeout_kills_process_group_and_preserves_evidence(self):
        with patch.object(gate, "SCAN_SECONDS", 0.05), \
                patch.object(gate.os, "killpg", wraps=os.killpg) as kill, \
                self.assertRaisesRegex(gate.GateError, "timeout"):
            gate.run_scan([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                          self.work, self.evidence, "timeout")
        kill.assert_called_once()
        self.assertFalse(json.loads((self.evidence / "timeout.status.json").read_text())["complete"])

    def test_large_cache_file_is_not_limited_as_evidence(self):
        program = "from pathlib import Path; Path('cache').write_bytes(b'x' * (17 * 1024 * 1024)); print('{}')"
        rc, data = gate.run_scan([sys.executable, "-I", "-c", program], self.work, self.evidence, "cache")
        self.assertEqual((rc, data), (0, {}))
        self.assertEqual((self.work / "cache").stat().st_size, 17 * 1024 * 1024)

    def test_stdout_and_stderr_are_bounded_even_on_fast_exit(self):
        for stream in (1, 2):
            with self.subTest(stream=stream), patch.object(gate, "MAX_FILE_BYTES", 1024):
                program = f"import os; os.write(1, b'{{}}'); os.write({stream}, b'x' * 65536)"
                name = f"large-{stream}"
                with self.assertRaisesRegex(gate.GateError, "output"):
                    gate.run_scan([sys.executable, "-I", "-c", program], self.work, self.evidence, name)
                status = json.loads((self.evidence / (name + ".status.json")).read_text())
                self.assertFalse(status["complete"])
                for suffix in (".json", ".stderr"):
                    self.assertLessEqual((self.evidence / (name + suffix)).stat().st_size, 1024)

    def test_change_detection_docs_only_and_empty_diff(self):
        self.put("README.md")
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.put("docs/guide.md")
        self.commit()
        self.assertEqual(gate.changes(self.repo, base, "HEAD")["full_run"], False)
        self.assertTrue(gate.changes(self.repo, base, "HEAD")["docs_only"])
        self.assertTrue(gate.changes(self.repo, "HEAD", "HEAD")["full_run"])

    def test_cli_change_outputs_preserve_both_boolean_lines(self):
        self.put("README.md")
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.put("docs/guide.md")
        self.commit()
        output = self.root / "outputs"
        env = dict(os.environ, GITHUB_OUTPUT=str(output), BASE_SHA=base, HEAD_SHA="HEAD")
        process = subprocess.run([sys.executable, "-I", str(ROOT / "gate.py"), "changes", "--repo",
                                  str(self.repo), "--work", str(self.work), "--evidence", str(self.evidence),
                                  "--tools", str(self.root)], env=env, capture_output=True, check=False, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(output.read_text(), "full_run=false\ndocs_only=true\n")
        self.assertEqual(json.loads((self.evidence / "changes.json").read_text())[0]["status"], "passed")

    def test_change_detection_full_run_for_sources_dependencies_workflows_and_unknown(self):
        self.put("README.md")
        self.commit()
        for path in ("src/main.go", "go.mod", "nested/Cargo.lock", ".github/workflows/ci.yml",
                     "docs/package.json", "docs/tool.py", "unknown", "src/deleted.ts", " README.md"):
            base = self.git("rev-parse", "HEAD").decode().strip()
            self.put(path)
            self.commit()
            with self.subTest(path=path):
                change = gate.changes(self.repo, base, "HEAD")
                self.assertTrue(change["full_run"])
                self.assertFalse(change["docs_only"])

    def test_change_detection_deleted_or_renamed_source_is_not_docs_only(self):
        self.put("src/main.go")
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.git("mv", "src/main.go", "README.md")
        self.commit()
        change = gate.changes(self.repo, base, "HEAD")
        self.assertEqual(set(change["paths"]), {"src/main.go", "README.md"})
        self.assertTrue(change["full_run"])
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.git("rm", "README.md")
        self.git("commit", "-qm", "delete docs")
        self.assertTrue(gate.changes(self.repo, base, "HEAD")["docs_only"])

    def test_change_detection_uses_merge_base_and_fails_on_git_errors(self):
        self.put("README.md")
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.git("checkout", "-qb", "base-tip")
        self.put("src/base-only.go")
        self.commit()
        base_tip = self.git("rev-parse", "HEAD").decode().strip()
        self.git("checkout", "-q", base)
        self.put("docs/guide.md")
        self.commit()
        self.assertTrue(gate.changes(self.repo, base_tip, "HEAD")["docs_only"])
        with self.assertRaises(gate.GateError):
            gate.changes(self.repo, "0" * 40, "HEAD")
        with patch.object(gate, "git", side_effect=[base.encode(), gate.GateError("diff failed")]), \
                self.assertRaisesRegex(gate.GateError, "diff failed"):
            gate.changes(self.repo, base, "HEAD")

    def test_osv_output_paths_cannot_be_prepopulated_by_pr(self):
        self.put("new-results.json", '{"results": []}')
        with self.assertRaisesRegex(gate.GateError, "reserved OSV"):
            self.commit()

    def test_osv_artifact_evidence_must_be_complete_even_after_success(self):
        files = {"osv-old/old-results.json": {"results": []},
                 "osv-new/new-results.json": {"results": []},
                 "osv-sarif/results.sarif": SARIF}
        for path, data in files.items():
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data))
        self.assertEqual(gate.osv_evidence(self.work)["status"], "passed")
        for path, data in files.items():
            with self.subTest(path=path):
                target = self.work / path
                target.unlink()
                with self.assertRaises(gate.GateError):
                    gate.osv_evidence(self.work)
                target.write_text('{}')
                with self.assertRaises(gate.GateError):
                    gate.osv_evidence(self.work)
                target.write_text(json.dumps(data))

    def test_osv_artifact_invalid_nested_results_fail(self):
        for path, data in {"osv-old/old-results.json": {"results": []},
                           "osv-new/new-results.json": {"results": [{"source": {}, "packages": [{}]}]},
                           "osv-sarif/results.sarif": SARIF}.items():
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data))
        with self.assertRaisesRegex(gate.GateError, "incomplete OSV new package"):
            gate.osv_evidence(self.work)

    def test_cli_inspection_failure_is_nonzero_with_evidence(self):
        self.put("vendor/file")
        self.commit()
        process = subprocess.run([sys.executable, "-I", str(ROOT / "gate.py"), "inspect", "--repo",
                                  str(self.repo), "--work", str(self.work), "--evidence", str(self.evidence),
                                  "--tools", str(self.root)], capture_output=True, timeout=30, check=False)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads((self.evidence / "inspect.json").read_text())[0]["status"], "failed")


class ShapeTests(unittest.TestCase):
    def test_path_traversal_and_controls_fail(self):
        for path in ("../vendor/x", "/vendor/x", "a/../x", "./a", "a//b", "a\\b", "x\ny", ".git/config"):
            with self.subTest(path=path), self.assertRaises(gate.GateError):
                gate.safe_path(path)
        self.assertEqual(str(gate.safe_path("src/日本語/go.mod")), "src/日本語/go.mod")

    def test_target_input_is_bounded_and_not_shell(self):
        for value in ("null", "{}", "[]", '[{"goos":"linux;id","goarch":"amd64","tags":[]}]',
                      '[{"goos":"linux","goarch":"amd64","tags":["$(id)"]}]',
                      '[{"goos":"linux","goarch":"amd64","tags":[],"command":"id"}]'):
            with self.subTest(value=value), self.assertRaises(gate.GateError):
                gate.targets(value)
        self.assertEqual(len(gate.targets('[{"goos":"linux","goarch":"amd64","tags":[]}]')), 1)

    def test_openvex_complete_clean_and_not_affected(self):
        gate.vex_clean(VEX)
        gate.vex_clean({k: v for k, v in VEX.items() if k != "statements"})
        data = copy.deepcopy(VEX)
        data["statements"] = [{"vulnerability": {"name": "GO-123"}, "products": [{}], "status": "not_affected"}]
        gate.vex_clean(data)
        data["statements"][0]["status"] = "affected"
        with self.assertRaises(gate.GateError):
            gate.vex_clean(data)

    def test_openvex_missing_incomplete_or_unknown_fails(self):
        for data in ({}, dict(VEX, statements="bad"), dict(VEX, statements=[{}]),
                     {k: v for k, v in VEX.items() if k != "tooling"}):
            with self.subTest(data=data), self.assertRaises(gate.GateError):
                gate.vex_clean(data)

    def test_sarif_missing_incomplete_or_findings_fail(self):
        gate.sarif_clean(SARIF)
        for data in ({}, {"version": "2.1.0", "runs": []},
                     {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "x"}}}]},
                     {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "x"}}, "results": [{}]}]}):
            with self.subTest(data=data), self.assertRaises(gate.GateError):
                gate.sarif_clean(data)

    def test_download_checksum_failure_precedes_execution(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(gate.urllib.request, "urlopen") as download:
            download.return_value.__enter__.return_value.read.return_value = b"wrong binary"
            with self.assertRaisesRegex(gate.GateError, "checksum mismatch"):
                gate.install_tools(["pinact"], Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_archive_does_not_extract_untrusted_paths(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            member = tarfile.TarInfo("../../outside")
            member.size = 4
            tar.addfile(member, io.BytesIO(b"evil"))
        archive = buf.getvalue()
        tools = {"fake": {"url": "https://example.invalid/tool", "sha256": gate.hashlib.sha256(archive).hexdigest(),
                          "member": "fake"}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(gate, "TOOLS", tools), \
                patch.object(gate.urllib.request, "urlopen") as download:
            download.return_value.__enter__.return_value.read.return_value = archive
            with self.assertRaisesRegex(gate.GateError, "invalid tool archive"):
                gate.install_tools(["fake"], Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
