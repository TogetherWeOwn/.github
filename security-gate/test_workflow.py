#!/usr/bin/env python3
"""Static workflow contracts and execution tests for the embedded trusted driver."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/public-pr-security.yml"


def load(path):
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def settled(go="false", cargo="false", workflows="false"):
    return {"inspect": {"result": "success", "outputs": {"go": go, "cargo": cargo, "workflows": workflows}},
            "osv": {"result": "success"}, "osv-evidence": {"result": "success"},
            "workflow-policy": {"result": "success" if workflows == "true" else "skipped"},
            "dependencies": {"result": "success" if "true" in (go, cargo) else "skipped"}}


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load(WORKFLOW)
        cls.jobs = cls.workflow["jobs"]

    def test_only_reusable_public_pr_no_private_or_scheduled_scans(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        for name, job in self.jobs.items():
            condition = job["if"].removeprefix("always() && ")
            pieces = condition.split(" && ", 2)
            self.assertEqual(pieces[:2], ["github.event_name == 'pull_request'",
                                          "github.event.repository.private == false"], name)
            for private in (True, False):
                for event in ("pull_request", "pull_request_target", "push", "schedule", "workflow_dispatch"):
                    # Interpret the two exact mandatory conjuncts checked above.
                    enabled = (event == pieces[0].split(" == ")[1].strip("'")) and (
                        str(private).lower() == pieces[1].split(" == ")[1])
                    self.assertEqual(enabled, event == "pull_request" and not private, (name, private, event))
            if "runs-on" in job:
                self.assertEqual(job["runs-on"], "ubuntu-24.04")
                self.assertLessEqual(int(job["timeout-minutes"]), 30)
            self.assertNotIn("secrets", job)
        self.assertNotIn("secrets", self.workflow["on"]["workflow_call"])

    def test_sha_pins_and_no_privileged_triggers_or_runners(self):
        refs = []
        for job in self.jobs.values():
            if "uses" in job:
                refs.append(job["uses"])
            for step in job.get("steps", []):
                if "uses" in step:
                    refs.append(step["uses"])
                if "checkout@" in step.get("uses", ""):
                    self.assertEqual(step["with"]["persist-credentials"], "false")
                    self.assertEqual(step["with"]["submodules"], "false")
                    self.assertEqual(step["with"]["path"], "target")
                    self.assertEqual(step["with"]["ref"], "${{ github.sha }}")
                self.assertNotIn("continue-on-error", step)
            self.assertNotIn("continue-on-error", job)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
        self.assertEqual(self.workflow["permissions"], {})

    def test_maintained_osv_pr_diff_contract_and_permissions(self):
        job = self.jobs["osv"]
        self.assertEqual(job["uses"], "google/osv-scanner-action/.github/workflows/osv-scanner-reusable-pr.yml@b1d267aea5211fa648d9d442fa64b90dbdcbdd36")
        self.assertEqual(job["with"], {"fail-on-vuln": "true", "upload-sarif": "true",
                                       "checkout-submodules": "false", "runs-on": "ubuntu-24.04"})
        expected = {"actions": "read", "contents": "read", "security-events": "write"}
        self.assertEqual(job["permissions"], expected)
        caller = load(ROOT / "workflow-templates/public-pr-security.yml.example")
        self.assertEqual(caller["jobs"]["security"]["permissions"], expected)
        self.assertEqual(set(caller["on"]), {"pull_request"})
        self.assertEqual(caller["jobs"]["security"]["if"],
                         "github.event_name == 'pull_request' && github.event.repository.private == false")
        self.assertNotIn("secrets", caller["jobs"]["security"])

    def test_workflow_data_token_is_step_scoped_and_not_in_language_scans(self):
        for job_name in ("inspect", "dependencies"):
            job = self.jobs[job_name]
            self.assertEqual(job["permissions"], {"contents": "read"})
            for step in job["steps"]:
                self.assertNotIn("GITHUB_TOKEN", step.get("env", {}))
        policy = self.jobs["workflow-policy"]
        self.assertEqual(policy["permissions"], {"contents": "read"})
        token_steps = [s for s in policy["steps"] if "GITHUB_TOKEN" in s.get("env", {})]
        self.assertEqual(len(token_steps), 1)
        self.assertEqual(token_steps[0]["env"]["GATE_MODE"], "policy")
        self.assertEqual(token_steps[0]["env"]["GITHUB_TOKEN"], "${{ github.token }}")
        self.assertNotIn("${{ inputs.", token_steps[0]["run"])

    def test_all_language_installations_are_conditional_and_pinned(self):
        steps = self.jobs["dependencies"]["steps"]
        go_setup = next(s for s in steps if "setup-go@" in s.get("uses", ""))
        self.assertEqual(go_setup["if"], "needs.inspect.outputs.go == 'true'")
        self.assertEqual(go_setup["with"], {"go-version": "1.26.10", "cache": "false"})
        gov = next(s for s in steps if "go install " in s.get("run", ""))
        self.assertIn("govulncheck@v1.9.0", gov["run"])
        self.assertEqual(gov["env"]["GOSUMDB"], "sum.golang.org")
        rust = next(s for s in steps if "rustup toolchain" in s.get("run", ""))
        self.assertEqual(rust["if"], "needs.inspect.outputs.cargo == 'true'")
        self.assertIn("install 1.93.0 --profile minimal", rust["run"])
        cargo = next(s for s in steps if s.get("env", {}).get("INSTALL_TOOLS") == '["cargo-deny"]')
        self.assertEqual(cargo["if"], "needs.inspect.outputs.cargo == 'true'")
        target_input = self.workflow["on"]["workflow_call"]["inputs"]["go-targets-json"]
        self.assertEqual(json.loads(target_input["default"]), [{"goos": "linux", "goarch": "amd64", "tags": []}])
        scan = next(s for s in steps if s.get("env", {}).get("GATE_MODE") == "dependencies")
        self.assertEqual(scan["env"]["GO_TARGETS"], "${{ inputs.go-targets-json }}")

    def test_evidence_uploads_always_fail_on_missing(self):
        for name in ("inspect", "workflow-policy", "dependencies", "osv-evidence"):
            step = self.jobs[name]["steps"][-1]
            self.assertEqual(step["if"], "always()")
            self.assertIn("actions/upload-artifact@", step["uses"])
            self.assertEqual(step["with"]["if-no-files-found"], "error")
            self.assertEqual(step["with"]["retention-days"], "5")

    def test_generated_workflow_matches_tested_driver(self):
        result = subprocess.run(["python3", "-I", str(ROOT / "security-gate/render_workflow.py"), "--check"],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        driver = next(s for s in self.jobs["inspect"]["steps"] if s.get("id") == "inspect")["run"]
        self.assertIn((ROOT / "security-gate/gate.py").read_text().rstrip(), driver)
        self.assertTrue(driver.startswith('python3 -I - "$GATE_MODE"'))
        for job in self.jobs.values():
            for step in job.get("steps", []):
                if step.get("env", {}).get("GATE_MODE"):
                    self.assertEqual(step["run"], driver)

    def test_existing_required_ci_runs_both_regression_suites(self):
        ci = load(ROOT / ".github/workflows/ci.yml")
        run = "\n".join(s.get("run", "") for s in ci["jobs"]["gate-selftest"]["steps"])
        self.assertIn("python3 -I security-gate/test_gate.py", run)
        self.assertIn("python3 -I security-gate/test_workflow.py", run)
        self.assertIn("gate-selftest", ci["jobs"]["ci-ok"]["needs"])

    def aggregate(self, needs):
        step = self.jobs["security-ok"]["steps"][0]
        return subprocess.run(["bash", "-e", "-c", step["run"]],
                              env=dict(os.environ, NEEDS_JSON=json.dumps(needs)),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def test_aggregation_accepts_only_complete_applicable_successes(self):
        for go in ("false", "true"):
            for cargo in ("false", "true"):
                for workflows in ("false", "true"):
                    result = self.aggregate(settled(go, cargo, workflows))
                    self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.jobs["security-ok"]["needs"]), set(settled()))
        self.assertTrue(self.jobs["security-ok"]["if"].startswith("always() && "))

    def test_aggregation_fails_skipped_failed_cancelled_and_incomplete_jobs(self):
        for job in settled():
            for result in ("failure", "cancelled", "skipped", "timed_out", "unknown"):
                needs = settled("true", "true", "true")
                needs[job]["result"] = result
                self.assertNotEqual(self.aggregate(needs).returncode, 0, (job, result))
        for key in ("go", "cargo", "workflows"):
            needs = settled()
            del needs["inspect"]["outputs"][key]
            self.assertNotEqual(self.aggregate(needs).returncode, 0)
        needs = settled()
        needs["dependencies"]["result"] = "success"
        self.assertNotEqual(self.aggregate(needs).returncode, 0)

    def test_embedded_inspection_does_not_import_pr_python(self):
        driver = next(s for s in self.jobs["inspect"]["steps"] if s.get("id") == "inspect")["run"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target"
            repo.mkdir()
            for path in ("json.py", "sitecustomize.py", "README.md"):
                (repo / path).write_text("raise RuntimeError('must never execute PR code')")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            env = dict(os.environ, GITHUB_WORKSPACE=tmp, RUNNER_TEMP=str(root / "temp"), GATE_MODE="inspect")
            result = subprocess.run(["bash", "-e", "-c", driver], cwd=repo, env=env,
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / "temp/gate-evidence/inspect.json").exists())


if __name__ == "__main__":
    unittest.main()
