#!/usr/bin/env python3
"""Run with python3 -I .github/scripts/test_pr_attestation.py (stdlib only)."""

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/pr_attestation.py"
spec = importlib.util.spec_from_file_location("pr_attestation", SCRIPT)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

BASE = "b" * 40
HEAD = "a" * 40
SECOND = "c" * 40
GROUP = "d" * 40
REPO = "example/project"
TEST_FILE = "tests/test_guard.py"


def body(tier="T1", test_file=TEST_FILE):
    tests = ("- none | reason=Documentation-only; no behavior changed." if tier == "T2" else
             f"- C1 | test={test_file}::test_guard | failed_before=yes | proof=python3 -m unittest; red before, green after")
    return (f"## Attestation\n\nrisk_tier: {tier}\n\n### Claims\n"
            "- C1: Rejects invalid input without changing valid input behavior.\n\n"
            f"### Tests\n{tests}\n\n### High-risk checklist\n"
            + "\n".join(f"- [x] D{i}: Checked relevant cases; remaining paths not applicable to this diff." for i in range(1, 9)))


def file(path=TEST_FILE, status="modified", **extra):
    return {"filename": path, "status": status, **extra}


def pr(n=1, head=HEAD, text=None, count=1, state="open", ref="main"):
    return {"number": n, "head": {"sha": head}, "body": text if text is not None else body(),
            "base": {"sha": BASE, "ref": ref, "repo": {"full_name": REPO}},
            "changed_files": count, "state": state}


def event(n=1, head=HEAD):
    return {"pull_request": {"number": n, "head": {"sha": head}, "body": "stale event body"}}


def group_event():
    return {"merge_group": {"base_sha": BASE, "head_sha": GROUP, "base_ref": "refs/heads/main",
                            "head_ref": "refs/heads/gh-readonly-queue/main/pr-1-abcdef"}}


class FakeAPI(a.GitHub):
    def __init__(self, responses):
        self.repo = REPO
        self.responses = responses
        self.requests = []

    def get(self, suffix):
        self.requests.append(suffix)
        if suffix not in self.responses:
            raise AssertionError(f"Unexpected API request: {suffix}")
        result = self.responses[suffix]
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


def api_for(p=None, files=None):
    p = p or pr()
    files = files or [file()]
    return FakeAPI({f"pulls/{p['number']}": p,
                    f"pulls/{p['number']}/files?per_page=100&page=1": files})


def group_api():
    suffix = f"compare/{BASE}...{GROUP}"
    responses = {
        f"{suffix}?per_page=1&page=1": {"total_commits": 3},
        f"{suffix}?per_page=100&page=1": {"commits": [{"sha": HEAD}, {"sha": SECOND}, {"sha": GROUP}]},
        f"commits/{HEAD}/pulls?per_page=100&page=1": [{"number": 1}],
        f"commits/{SECOND}/pulls?per_page=100&page=1": [{"number": 2}],
        f"commits/{GROUP}/pulls?per_page=100&page=1": [{"number": 2}],
        "pulls/1": pr(), "pulls/2": pr(2, SECOND, body("T0"), count=2),
        "pulls/1/files?per_page=100&page=1": [file()],
        "pulls/2/files?per_page=100&page=1": [file(), file("src/auth/check.rs")],
    }
    return FakeAPI(responses)


class ParserTests(unittest.TestCase):
    def issues(self, text, files=None):
        return a.validate(text, files or [file()])["issues"]

    def test_complete_standard(self):
        report = a.validate(body(), [file()])
        self.assertEqual(report["verdict"], "valid")
        self.assertEqual(report["failed_before"], {"yes": 1})

    def test_high_risk(self):
        self.assertEqual(a.validate(body("T0"), [file(), file("sql/grants.sql")])["verdict"], "valid")

    def test_docs_only_without_tests(self):
        self.assertEqual(a.validate(body("T2"), [file("docs/guide.md")])["verdict"], "valid")

    def test_no_red_before_claim_is_not_fabricated(self):
        for state in ("no", "not-run"):
            with self.subTest(state=state):
                report = a.validate(body().replace("failed_before=yes", f"failed_before={state}"), [file()])
                self.assertEqual(report["verdict"], "valid")
                self.assertEqual(report["failed_before"], {state: 1})

    def test_unfilled_template_is_invalid(self):
        text = (ROOT / ".github/pull_request_template.md").read_text()
        self.assertIn("risk_tier_invalid", self.issues(text))
        self.assertIn("claims_empty", self.issues(text))

    def test_missing_null_large_duplicate_attestation(self):
        for text in ("", None, "x" * 65537, body() + "\n" + body()):
            with self.subTest(text=str(text)[:20]):
                self.assertTrue(self.issues(text))

    def test_comments_fences_and_quotes_cannot_attest(self):
        for text in (f"<!--{body()}-->", f"```markdown\n{body()}\n```", f"~~~~\n{body()}\n~~~~",
                     "\n".join("> " + x for x in body().splitlines()),
                     "\n".join("    " + x for x in body().splitlines())):
            with self.subTest(text=text[:20]):
                self.assertIn("attestation_missing_or_duplicate", self.issues(text))

    def test_heading_end_and_comments(self):
        text = "<!-- description -->\n" + body() + "\n## Risks\nUnrelated section\n"
        self.assertEqual(self.issues(text), [])

    def test_duplicate_and_missing_sections(self):
        self.assertIn("section_unknown_or_duplicate", self.issues(body() + "\n### Claims\n- C2: Duplicate section."))
        self.assertIn("section_missing", self.issues(body().replace("### Tests", "### Evidence")))

    def test_tier_strict(self):
        for value in ("T3", "T1 T0", "high-risk", "T1\nrisk_tier: T0"):
            self.assertIn("risk_tier_invalid", self.issues(body().replace("risk_tier: T1", "risk_tier: " + value)))

    def test_claims_strict_and_duplicate(self):
        self.assertIn("claim_shape_invalid", self.issues(body().replace("- C1:", "- behavior:")))
        self.assertIn("claim_duplicate", self.issues(body().replace("### Tests", "- C1: Same id.\n### Tests")))

    def test_real_prose_with_brackets_and_placeholder_words_is_meaningful(self):
        replacements = (
            "Rejects invalid input without changing valid input behavior.",
            "python3 -m unittest; red before, green after",
            "Checked relevant cases; remaining paths not applicable to this diff.",
            "test_guard |",
        )
        for value in ("parse returns Option<String>", "returns Result<Vec<u8>, Error> on bad input",
                      "asserts a < b and c > d", "latency <= 5ms and > 0", "renders <div>",
                      "Removes TODO markers", "Documents TBD and REPLACE_ME tokens"):
            for old in replacements:
                with self.subTest(value=value, field=old):
                    new = value + " |" if old == "test_guard |" else value
                    self.assertEqual(self.issues(body().replace(old, new)), [])
            with self.subTest(value=value, field="no_tests_reason"):
                text = body("T2").replace("Documentation-only; no behavior changed.", value)
                self.assertEqual(self.issues(text, [file("README.md")]), [])

    def test_bare_placeholder_values_remain_invalid(self):
        fields = (
            ("Rejects invalid input without changing valid input behavior.", "claim_shape_invalid"),
            ("python3 -m unittest; red before, green after", "test_shape_invalid"),
            ("Checked relevant cases; remaining paths not applicable to this diff.", "checklist_shape_invalid"),
            ("test_guard |", "test_path_invalid"),
        )
        for value in ("TODO", "tbd", "REPLACE_ME", "<describe behavior>", "  <proof>  "):
            for old, issue in fields:
                with self.subTest(value=value, field=old):
                    new = value + " |" if old == "test_guard |" else value
                    self.assertIn(issue, self.issues(body().replace(old, new)))
            with self.subTest(value=value, field="no_tests_reason"):
                text = body("T2").replace("Documentation-only; no behavior changed.", value)
                self.assertIn("no_tests_reason_invalid", self.issues(text, [file("README.md")]))

    def test_tests_cover_every_claim(self):
        self.assertIn("claim_without_test", self.issues(body().replace("### Tests", "- C2: Other behavior.\n### Tests")))
        self.assertIn("test_claim_unknown", self.issues(body().replace("- C1 |", "- C2 |")))

    def test_test_line_shape(self):
        for old, new in (("failed_before=yes", "failed_before=maybe"), (" | proof=", " | evidence="),
                         ("test_guard |", " |"), ("proof=python3 -m unittest; red before, green after", "proof=TODO")):
            self.assertTrue(self.issues(body().replace(old, new)))

    def test_test_file_must_be_in_diff_and_exist_at_head(self):
        self.assertIn("test_file_not_in_head_diff", self.issues(body(), [file("src/main.rs")]))
        self.assertIn("test_file_not_in_head_diff", self.issues(body(), [file(status="removed")]))

    def test_inline_source_tests_accepted(self):
        self.assertEqual(self.issues(body(test_file="src/lib.rs"), [file("src/lib.rs")]), [])

    def test_test_path_canonical(self):
        for path in ("/tmp/test.py", "../test.py", "tests/../x.py", "tests//test.py", "tests\\test.py"):
            self.assertIn("test_path_invalid", self.issues(body(test_file=path)))

    def test_renamed_test_only_new_path_exists(self):
        files = [file("tests/new.py", "renamed", previous_filename=TEST_FILE)]
        self.assertIn("test_file_not_in_head_diff", self.issues(body(), files))
        self.assertEqual(self.issues(body(test_file="tests/new.py"), files), [])

    def test_high_risk_checklist_complete_and_checked(self):
        self.assertIn("high_risk_checklist_unchecked", self.issues(body("T0").replace("[x] D2", "[ ] D2")))
        self.assertIn("checklist_incomplete", self.issues(body().replace("D8:", "D9:")))
        self.assertIn("checklist_duplicate", self.issues(body() + "\n- [x] D1: Duplicate."))
        self.assertEqual(self.issues(body().replace("[x] D2", "[ ] D2")), [])

    def test_no_tests_requires_docs_and_reason(self):
        self.assertIn("no_tests_only_allowed_for_docs", self.issues(body("T2").replace("risk_tier: T2", "risk_tier: T1")))
        self.assertIn("no_tests_reason_invalid", self.issues(body("T2").replace("reason=Documentation-only; no behavior changed.", "reason=TODO")))

    def test_shell_text_is_only_data(self):
        text = body().replace("proof=python3 -m unittest; red before, green after", "proof=$(touch /tmp/NEVER_EXECUTE) <script>alert(1)</script>")
        report = a.validate(text, [file()])
        output = a.summary({"verdict": report["verdict"], "observations": [{**report, "pr": 1}]})
        self.assertNotIn("NEVER_EXECUTE", output)
        self.assertNotIn("<script>", output)


class RiskTests(unittest.TestCase):
    def test_every_t0_family_root_nested_and_descendants(self):
        for path in ("migrations/01.sql", "sql/schema.sql", "auth/login.py", "src/auth/check.rs",
                     "session.rs", "src/session_store/deep/token.rs", "billing/payment.ts",
                     ".github/workflows/deploy.yml", "wrangler.jsonc", "crates/cutover/src/lib.rs",
                     "voice/main.rs", "src/voice/handler.rs", "crypto/vault", "crypto/vault/key.rs"):
            with self.subTest(path=path):
                self.assertEqual(a.classify({path}), "T0")

    def test_nested_wrangler_and_payment_permission_modules_require_t0(self):
        for path in ("apps/api/wrangler.jsonc", "workers/api/wrangler.toml",
                     "workers/stream/wrangler.json", "apps/api/wrangler.production.toml",
                     "src/payments/charge.ts", "src/payment.ts", "src/permissions.ts",
                     "src/permission/check.rs", "app/PaymentController.php",
                     "app/PermissionsPolicy.php", "lib/payment_processor.py"):
            with self.subTest(path=path):
                report = a.validate(body().replace("[x]", "[ ]"), [file(), file(path)])
                self.assertEqual(report["path_tier"], "T0")
                self.assertEqual(report["verdict"], "invalid")
                self.assertIn("risk_tier_understated", report["issues"])
                self.assertIn("high_risk_checklist_unchecked", report["issues"])
                self.assertEqual(a.validate(body("T0"), [file(), file(path)])["verdict"], "valid")

    def test_payment_permission_and_wrangler_boundaries(self):
        for path in ("src/prepayments.ts", "src/permissionless.rs", "src/permissionship.py",
                     "docs/payment-guide.md", "docs/permissions.md", "docs/wrangler.md",
                     "apps/api/wranglerish.jsonc", "apps/api/not-wrangler.toml",
                     "apps/api/wrangler.jsonc.example"):
            with self.subTest(path=path):
                self.assertFalse(a.is_t0(path))

    def test_risk_prefix_boundary(self):
        for path in ("authors.py", "src/authority.rs", "billings/demo.ts", "docs/vault_notes.md",
                     "src/voices/handler.rs", "docs/wrangler.md", "migrations_archive/01.sql",
                     "src/roleplay.ts", "src/grandfather.rs", "src/voicemail.rs", "src/dumpster.py",
                     "src/ingestion_notes.txt", "docs/auth-guide.md", "docs/cryptography.rst",
                     "app/Services/AuthorController.php", "packages/sqlite/client.py"):
            with self.subTest(path=path):
                self.assertFalse(a.is_t0(path))

    def test_docs_is_conservative(self):
        for path in ("README.md", "docs/guide.rst", "docs/sub/README.txt"):
            self.assertEqual(a.classify({path}), "T2")
        for path in ("docs/script.py", ".github/pull_request_template.md", "src/readme.md", "foo.yml",
                     "docs/sub/notes.txt"):
            self.assertEqual(a.classify({path}), "T1")

    def test_build_manifests_cannot_claim_docs_only_without_tests(self):
        for path in ("requirements.txt", "CMakeLists.txt", "docs/requirements.txt",
                     "docs/build/CMakeLists.txt", "docs/requirements-dev.txt",
                     "docs/constraints.txt", "docs/notes.txt"):
            with self.subTest(path=path):
                report = a.validate(body("T2"), [file(path)])
                self.assertEqual(report["path_tier"], "T1")
                self.assertEqual(report["verdict"], "invalid")
                self.assertIn("risk_tier_understated", report["issues"])

    def test_allowlisted_text_documentation_can_claim_no_tests(self):
        for path in ("README.txt", "LICENSE.txt", "NOTICE.txt", "docs/README.txt",
                     "docs/reference/CHANGELOG.txt"):
            with self.subTest(path=path):
                self.assertEqual(a.validate(body("T2"), [file(path)])["verdict"], "valid")

    def test_real_critical_modules_reject_standard_attestations(self):
        # Paths verified against target-repository trees, not just idealized auth/ layouts.
        for path in (
            "workers/api/src/auth.rs", "workers/api/src/vault.rs",
            "workers/stream/src/grant.rs", "apps/web/src/lib/billing/plans.ts",
            "crates/bot/src/gateway.rs", "crates/bot/src/gateway_backoff.rs",
            "crates/bot/src/voice_cold_authority_tests.rs", "crates/core/src/secret.rs",
            "crates/core/src/backup/dump.rs", "crates/core/src/database_roles.rs",
            "src/member-erasure.ts", "src/roles.ts", "bin/db-backup.sh",
            "src/db/connection.ts", "drizzle/1022_web-sessions-oauth-journeys.sql",
            "database/migrations/0001_01_01_000000_create_users_table.php",
            "app/Http/Controllers/Settings/SessionController.php",
            "app/Http/Controllers/Settings/TwoFactorAuthenticationController.php",
            "app/Services/Webhooks/RotateWebhookSecret.php",
        ):
            with self.subTest(path=path):
                report = a.validate(body().replace("[x] D2", "[ ] D2"), [file(), file(path)])
                self.assertEqual(report["path_tier"], "T0")
                self.assertEqual(report["verdict"], "invalid")
                self.assertIn("risk_tier_understated", report["issues"])
                self.assertIn("high_risk_checklist_unchecked", report["issues"])

    def test_all_specified_critical_families_have_nested_fixtures(self):
        for path in ("packages/api/migrations/01.sql", "lib/crypto/cipher.rs",
                     "lib/vault/key.rs", "lib/secret_keys.rs", "src/backup.rs",
                     "tools/dump_data.py", "src/grants.rs", "src/role_policy.ts",
                     "src/erasure/handler.rs", "src/ingest/body.rs", "src/ingest.rs",
                     "src/authz.rs", "src/oauth/token.rs", "src/session_keys.rs",
                     "packages/api/sql/schema.sql", "src/billing/charge.rs",
                     "crypto/vault_notes.md", "migrations_backup/01.sql", "src/oauth_notes.rs"):
            with self.subTest(path=path):
                self.assertEqual(a.classify({path}), "T0")

    def test_understatement_and_overstatement(self):
        self.assertIn("risk_tier_understated", a.validate(body(), [file(), file("auth/login.rs")])["issues"])
        self.assertIn("risk_tier_understated", a.validate(body("T2"), [file("src/lib.rs")])["issues"])
        self.assertEqual(a.validate(body("T0"), [file()])["verdict"], "valid")

    def test_rename_away_from_risk_and_delete_are_risky(self):
        for changed in (file("docs/moved.md", "renamed", previous_filename="auth/login.md"),
                        file("migrations/01.sql", "removed")):
            self.assertEqual(a.validate(body(), [file(), changed])["path_tier"], "T0")

    def test_empty_duplicate_and_malformed_files_are_error(self):
        for files in ([], {}, [None], [file(), file()], [file("../x")], [file(status="unknown")],
                      [file(status="renamed")]):
            with self.subTest(files=files), self.assertRaises(a.ObservationError):
                a.validate(body(), files)


class APITests(unittest.TestCase):
    def test_pr_uses_fresh_body_from_api_not_event(self):
        self.assertEqual(a.evaluate(api_for(), "pull_request", event())["verdict"], "valid")

    def test_api_missing_attestation_never_uses_event_body(self):
        api = api_for(pr(text="no attestation"))
        ev = event()
        ev["pull_request"]["body"] = body()
        self.assertEqual(a.evaluate(api, "pull_request", ev)["verdict"], "invalid")

    def test_stale_event_head(self):
        with self.assertRaisesRegex(a.ObservationError, "event_head_stale"):
            a.evaluate(api_for(pr(head=SECOND)), "pull_request", event())

    def test_incomplete_file_listing_is_error(self):
        result = a.evaluate(api_for(pr(count=2)), "pull_request", event())
        self.assertEqual(result["verdict"], "error")
        self.assertEqual(result["observations"][0]["issues"], ["pr_files_incomplete"])

    def test_files_pagination_second_page(self):
        api = api_for(pr(count=101))
        api.responses["pulls/1/files?per_page=100&page=1"] = [file(f"src/{i}.rs") for i in range(100)]
        api.responses["pulls/1/files?per_page=100&page=2"] = [file()]
        self.assertEqual(a.evaluate(api, "pull_request", event())["verdict"], "valid")
        self.assertIn("pulls/1/files?per_page=100&page=2", api.requests)

    def test_snapshot_change_is_error(self):
        api = api_for()
        original_get = api.get
        calls = 0
        def get(suffix):
            nonlocal calls
            data = original_get(suffix)
            if suffix == "pulls/1":
                calls += 1
                if calls > 1:
                    data["head"]["sha"] = SECOND
            return data
        api.get = get
        result = a.evaluate(api, "pull_request", event())
        self.assertEqual(result["observations"][0]["issues"], ["pr_snapshot_changed"])

    def test_merge_group_checks_all_prs(self):
        api = group_api()
        result = a.evaluate(api, "merge_group", group_event())
        self.assertEqual(result["verdict"], "valid")
        self.assertEqual([r["pr"] for r in result["observations"]], [1, 2])
        api.responses["pulls/1"]["body"] = "missing"
        self.assertEqual(a.evaluate(api, "merge_group", group_event())["verdict"], "invalid")

    def test_merge_group_association_pagination(self):
        api = group_api()
        key = f"commits/{HEAD}/pulls?per_page=100&page=1"
        api.responses[key] = [{"number": 2}] * 100
        api.responses[key.replace("&page=1", "&page=2")] = [{"number": 1}]
        result = a.evaluate(api, "merge_group", group_event())
        self.assertEqual(result["verdict"], "valid")
        self.assertIn(key.replace("&page=1", "&page=2"), api.requests)

    def test_merge_group_compare_pagination(self):
        api = group_api()
        extras = [f"{i:040x}" for i in range(1, 100)]
        suffix = f"compare/{BASE}...{GROUP}"
        api.responses[f"{suffix}?per_page=1&page=1"] = {"total_commits": 102}
        api.responses[f"{suffix}?per_page=100&page=1"] = {"commits": [{"sha": s} for s in extras + [HEAD]]}
        api.responses[f"{suffix}?per_page=100&page=2"] = {"commits": [{"sha": SECOND}, {"sha": GROUP}]}
        for s in extras:
            api.responses[f"commits/{s}/pulls?per_page=100&page=1"] = []
        self.assertEqual(a.evaluate(api, "merge_group", group_event())["verdict"], "valid")

    def test_merge_group_leading_pr_cannot_be_silently_dropped(self):
        api = group_api()
        api.responses[f"commits/{HEAD}/pulls?per_page=100&page=1"] = []
        with self.assertRaisesRegex(a.ObservationError, "leading_pr_unresolved"):
            a.evaluate(api, "merge_group", group_event())

    def test_merge_group_stale_nonleading_pr_is_not_silently_dropped(self):
        api = group_api()
        api.responses["pulls/2"]["head"]["sha"] = "e" * 40
        with self.assertRaisesRegex(a.ObservationError, "candidate_head_unresolved"):
            a.evaluate(api, "merge_group", group_event())

    def test_merge_group_closed_nonleading_pr_is_not_silently_dropped(self):
        api = group_api()
        api.responses["pulls/2"]["state"] = "closed"
        with self.assertRaisesRegex(a.ObservationError, "merge_group_candidate_state_or_base_stale"):
            a.evaluate(api, "merge_group", group_event())

    def test_merge_group_retargeted_nonleading_pr_is_not_silently_dropped(self):
        api = group_api()
        api.responses["pulls/2"]["base"]["ref"] = "release"
        with self.assertRaisesRegex(a.ObservationError, "merge_group_candidate_state_or_base_stale"):
            a.evaluate(api, "merge_group", group_event())

    def test_merge_group_unrelated_closed_or_other_base_pr_can_be_excluded(self):
        for state, ref in (("closed", "main"), ("open", "release")):
            with self.subTest(state=state, ref=ref):
                api = group_api()
                api.responses[f"commits/{HEAD}/pulls?per_page=100&page=1"].append({"number": 3})
                api.responses["pulls/3"] = pr(3, "e" * 40, state=state, ref=ref)
                result = a.evaluate(api, "merge_group", group_event())
                self.assertEqual(result["verdict"], "valid")
                self.assertEqual([r["pr"] for r in result["observations"]], [1, 2])

    def test_merge_group_retarget_during_file_fetch_is_stale_even_at_same_sha(self):
        api = group_api()
        original_get = api.get
        calls = 0
        def get(suffix):
            nonlocal calls
            data = original_get(suffix)
            if suffix == "pulls/2":
                calls += 1
                if calls > 1:
                    data["base"]["ref"] = "release"
            return data
        api.get = get
        result = a.evaluate(api, "merge_group", group_event())
        self.assertEqual(result["verdict"], "error")
        self.assertEqual(result["observations"][1]["issues"], ["pr_snapshot_changed"])

    def test_merge_group_incomplete_compare_is_error(self):
        api = group_api()
        api.responses[f"compare/{BASE}...{GROUP}?per_page=1&page=1"] = {"total_commits": 4}
        with self.assertRaisesRegex(a.ObservationError, "commits_incomplete"):
            a.evaluate(api, "merge_group", group_event())

    def test_merge_group_empty_is_error(self):
        api = group_api()
        for s in (HEAD, SECOND, GROUP):
            api.responses[f"commits/{s}/pulls?per_page=100&page=1"] = []
        with self.assertRaisesRegex(a.ObservationError, "prs_unresolved"):
            a.evaluate(api, "merge_group", group_event())

    def test_merge_group_wrong_base_is_stale(self):
        api = group_api()
        api.responses["pulls/1"]["base"]["ref"] = "release"
        with self.assertRaisesRegex(a.ObservationError, "candidate_state_or_base_stale"):
            a.evaluate(api, "merge_group", group_event())

    def test_bad_event_and_shapes(self):
        for name, ev in (("push", {}), ("pull_request", []), ("pull_request", {}), ("merge_group", {})):
            with self.subTest(name=name, ev=ev), self.assertRaises(a.ObservationError):
                a.evaluate(api_for(), name, ev)
        for value in (None, {}, "1", True, -1):
            with self.assertRaises(a.ObservationError):
                a.number(value)
        for field, value in (("changed_files", 3001), ("changed_files", True), ("head", [])):
            p = pr()
            p[field] = value
            with self.assertRaises(a.ObservationError):
                a.pull(api_for(p), 1)

    def test_malformed_pagination_and_api_errors(self):
        for response in ({"message": "rate limited"}, [None], a.ObservationError("api_request_failed")):
            api = api_for()
            api.responses["pulls/1/files?per_page=100&page=1"] = response
            self.assertEqual(a.evaluate(api, "pull_request", event())["verdict"], "error")

    def test_pagination_bound_is_not_clean(self):
        api = FakeAPI({"list?per_page=100&page=1": [{}] * 100})
        with self.assertRaisesRegex(a.ObservationError, "pagination_limit"):
            api.pages("list", max_pages=1)

    def test_redirect_never_forwards_token(self):
        with self.assertRaisesRegex(a.ObservationError, "redirect_refused"):
            a.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/")

    def test_http_failure_is_fixed_diagnostic(self):
        api = a.GitHub("never-print-token", REPO)
        with mock.patch.object(api.opener, "open", side_effect=OSError("sensitive-detail")):
            with self.assertRaisesRegex(a.ObservationError, "^api_request_failed$"):
                api.get("pulls/1")

    def test_budget_exhaustion_is_explicit(self):
        api = a.GitHub("unused", REPO)
        api.calls = 600
        with self.assertRaisesRegex(a.ObservationError, "budget_exceeded"):
            api.get("pulls/1")


class CLITests(unittest.TestCase):
    def test_runtime_error_warns_summary_and_exit_zero_under_isolated_python(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "json.py").write_text("raise RuntimeError('local module executed')")
            (p / "event.json").write_text("{malformed")
            env = {**os.environ, "GITHUB_EVENT_PATH": str(p / "event.json"), "GITHUB_EVENT_NAME": "pull_request",
                   "GITHUB_OUTPUT": str(p / "output"), "GITHUB_STEP_SUMMARY": str(p / "summary"),
                   "GH_TOKEN": "do-not-print-token", "PYTHONPATH": d}
            result = subprocess.run([sys.executable, "-I", str(SCRIPT)], cwd=d, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn('"verdict": "error"', result.stdout)
            self.assertIn("::warning::", result.stdout)
            self.assertNotIn("do-not-print-token", result.stdout + result.stderr)
            self.assertNotIn("local module executed", result.stdout + result.stderr)
            self.assertIn("would_fail=true", (p / "output").read_text())
            self.assertIn("NOT verified", (p / "summary").read_text())

    def test_stale_merge_group_warns_and_exits_zero(self):
        for state, ref in (("closed", "main"), ("open", "release")):
            with self.subTest(state=state, ref=ref), tempfile.TemporaryDirectory() as directory:
                api = group_api()
                api.responses["pulls/2"]["state"] = state
                api.responses["pulls/2"]["base"]["ref"] = ref
                output_file = Path(directory) / "output"
                summary_file = Path(directory) / "summary"
                with mock.patch.object(a, "GitHub", return_value=api), \
                     mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": "unused", "GITHUB_EVENT_NAME": "merge_group",
                                                  "GITHUB_OUTPUT": str(output_file),
                                                  "GITHUB_STEP_SUMMARY": str(summary_file)}), \
                     mock.patch.object(Path, "read_text", return_value=json.dumps(group_event())), \
                     mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(a.main(), 0)
                report = json.loads(output.getvalue().splitlines()[0])
                self.assertEqual(report["verdict"], "error")
                self.assertEqual(report["issues"], ["merge_group_candidate_state_or_base_stale"])
                self.assertEqual(report["observations"], [])
                self.assertIn("::warning::", output.getvalue())
                self.assertIn("verdict=error\nwould_fail=true", output_file.read_text())
                self.assertIn("**error**", summary_file.read_text())

    def test_invalid_attestation_is_warning_not_failure(self):
        with mock.patch.object(a, "GitHub", return_value=api_for(pr(text="missing"))), \
             mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": "unused", "GITHUB_EVENT_NAME": "pull_request"}), \
             mock.patch.object(Path, "read_text", return_value=json.dumps(event())), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(a.main(), 0)
            self.assertIn('"verdict": "invalid"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
