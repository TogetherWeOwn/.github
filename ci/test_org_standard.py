"""Read-only contracts for reviewed org ruleset source; no live GitHub writes."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIN = "97fc0e24038021dfaa1fc9da754c6c5d6429130f"


def load(name):
    return json.loads((ROOT / "rulesets" / name).read_text())


def property_spec(name, value):
    return {"name": name, "property_values": [value], "source": "custom"}


def matches(ruleset, properties):
    condition = ruleset["conditions"]["repository_property"]
    return all(properties.get(item["name"]) in item["property_values"]
               for item in condition["include"]) and not any(
        properties.get(item["name"]) in item["property_values"]
        for item in condition["exclude"])


class OrgStandardTests(unittest.TestCase):
    def test_baseline_envelopes(self):
        for name in ("baseline-review", "baseline-codeql", "baseline-ci"):
            with self.subTest(name=name):
                obj = load(name + ".json")
                self.assertEqual(obj["name"], name)
                self.assertEqual(obj["target"], "branch")
                self.assertEqual(obj["bypass_actors"], [])
                self.assertEqual(obj["conditions"]["ref_name"], {
                    "include": ["~DEFAULT_BRANCH"], "exclude": []})
                self.assertEqual(obj["enforcement"],
                                 "disabled" if name == "baseline-ci" else "evaluate")
                expected = [property_spec("governance", "standard")]
                if name == "baseline-codeql":
                    expected.append(property_spec("codeql", "required"))
                if name == "baseline-ci":
                    expected.append(property_spec("ci", "standard"))
                self.assertEqual(obj["conditions"]["repository_property"], {
                    "include": expected, "exclude": []})

    def test_review_only_requires_pr_and_pinned_devin(self):
        rules = load("baseline-review.json")["rules"]
        self.assertEqual([rule["type"] for rule in rules],
                         ["pull_request", "required_status_checks"])
        self.assertEqual(rules[0]["parameters"], {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews_on_push": False,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": True,
            "allowed_merge_methods": ["squash"],
        })
        self.assert_status(rules[1], "Devin Review", 811515)

    def assert_status(self, rule, context, app):
        self.assertEqual(rule, {"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": False,
            "do_not_enforce_on_create": False,
            "required_status_checks": [{"context": context, "integration_id": app}],
        }})

    def test_codeql_thresholds(self):
        self.assertEqual(load("baseline-codeql.json")["rules"], [{
            "type": "code_scanning", "parameters": {"code_scanning_tools": [{
                "tool": "CodeQL", "security_alerts_threshold": "high_or_higher",
                "alerts_threshold": "errors",
            }]}}])

    def test_ci_is_optional_and_pinned(self):
        obj = load("baseline-ci.json")
        self.assertEqual(len(obj["rules"]), 1)
        self.assert_status(obj["rules"][0], "ci-ok", 15368)

    def test_private_repos_keep_devin_without_codeql(self):
        standard = (ROOT / "STANDARD.md").read_text()
        for repo in ("kofra-cloud", "open-serverless-vault"):
            with self.subTest(repo=repo):
                self.assertIn("`" + repo + "`", standard)
                properties = {"governance": "standard", "codeql": "deferred"}
                self.assertTrue(matches(load("baseline-review.json"), properties))
                self.assertFalse(matches(load("baseline-codeql.json"), properties))
        self.assertIn("CodeQL stays disabled", standard)
        self.assertIn("Do not request/enable Code Security or attach", standard)

    def test_targeting_positive_and_negative_controls(self):
        review = load("baseline-review.json")
        codeql = load("baseline-codeql.json")
        ci = load("baseline-ci.json")
        public = {"governance": "standard", "codeql": "required", "ci": "standard"}
        self.assertTrue(all(matches(rule, public) for rule in (review, codeql, ci)))
        for governance in ("fork-contrib", "fixture", None):
            excluded = dict(public, governance=governance)
            self.assertFalse(any(matches(rule, excluded) for rule in (review, codeql, ci)))
        self.assertFalse(matches(codeql, {"governance": "standard"}))
        self.assertFalse(matches(ci, {"governance": "standard", "ci": "custom"}))
        self.assertFalse(matches(ci, {"governance": "standard"}))

    def test_snapshots_retain_existing_rules_and_pin(self):
        protection = load("exports/25234562.json")
        self.assertEqual(protection["id"], 25234562)
        self.assertEqual(protection["rules"], [{"type": "deletion"}, {"type": "non_fast_forward"}])
        self.assertEqual(protection["bypass_actors"], [{
            "actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}])
        workflow = load("exports/26327170.json")
        self.assertEqual(workflow["id"], 26327170)
        self.assertEqual(workflow["bypass_actors"], [])
        self.assertEqual(workflow["rules"], [{"type": "workflows", "parameters": {
            "do_not_enforce_on_create": True, "workflows": [{
                "repository_id": 1403503756,
                "path": ".github/workflows/pr-standards-required.yml", "sha": PIN}]}}])

    def test_legacy_section_anchors(self):
        expected = {
            "CI-STANDARD.md": {
                "ci-standard-only-run-what-the-change-affects-permanent-every-togetherweown-repo",
                "rules", "advisory-pr-size-labels", "banned", "verification-per-repo",
            },
            "PIPELINE-STANDARD.md": {
                "pipeline-standard-one-pr-one-review-one-merge-one-deploy-every-togetherweown-repo",
                "1-review", "2-merge", "2a-merge-queue-not-enabled", "3-deploy",
                "3a-promote-the-last-staging-verified-commit", "4-ci", "5-release",
                "5a-no-host-release-train",
                "6-self-blocking-anti-patterns-each-measured-on-2026-10-10",
            },
        }
        standard = (ROOT / "STANDARD.md").read_text()
        destinations = {
            re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
            for heading in re.findall(r"^#+ (.+)$", standard, re.MULTILINE)
        }
        for name, anchors in expected.items():
            with self.subTest(document=name):
                text = (ROOT / name).read_text()
                self.assertEqual(set(re.findall(r'<a id="([^"]+)"></a>', text)), anchors)
                links = re.findall(r'<a id="[^"]+"></a>\s+\[[^\]]+\]\(([^)]+)\)', text)
                self.assertEqual(len(links), len(anchors))
                for link in links:
                    path, _, fragment = link.partition("#")
                    self.assertEqual(path, "STANDARD.md")
                    if fragment:
                        self.assertIn(fragment, destinations)

    def test_canonical_docs_and_local_links(self):
        for old in ("CI-STANDARD.md", "PIPELINE-STANDARD.md"):
            text = (ROOT / old).read_text()
            self.assertIn("[STANDARD.md](STANDARD.md)", text)
            self.assertIn("do not add policy here", text)
        documents = ["STANDARD.md", "README.md", "NEW-REPO-CHECKLIST.md",
                     "CI-STANDARD.md", "PIPELINE-STANDARD.md", "rulesets/README.md"]
        for name in documents:
            text = (ROOT / name).read_text()
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if "://" not in link and not link.startswith("#"):
                    with self.subTest(document=name, link=link):
                        self.assertTrue((ROOT / name).parent.joinpath(link.split("#")[0]).exists())
        standard = (ROOT / "STANDARD.md").read_text()
        for phrase in ("Release Manager", "final-stage reviewer", "paperclip-ops",
                       "zero recurring always-on runtime cost", PIN):
            self.assertIn(phrase, standard)
        self.assertEqual(len(re.findall(r"^\d+\. \*\*", standard, re.MULTILINE)), 25)


if __name__ == "__main__":
    unittest.main()
