"""Every workflow in this repo must run on GitHub-hosted runners.

The org has no self-hosted runners. A required workflow routed to a
self-hosted label never starts, so its check never reports and every merge
in the repos it covers is blocked. These tests fail if any workflow or
workflow template here names a self-hosted label, or picks its runner from
a repository/org variable that could hold one. A caller that pins one of
this repo's reusables by sha runs that older revision, so the runner lines
and input defaults of every pinned revision are checked as well. A pin may
name a commit that is on no branch, so a missing one is fetched by sha.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_hosted_runners.py'
Also wired into the `pr-standards selftest` job in .github/workflows/ci.yml.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIRS = [ROOT / ".github" / "workflows", ROOT / "workflow-templates"]

# `self-hosted` is the label GitHub gives every self-hosted runner; the rest
# are the retired pool's own labels and the variable that routed to it.
SELF_HOSTED = re.compile(r"self[-_ ]?hosted|two-(?:isolated|ephemeral)|CI_OVERFLOW_RUNNER", re.I)
RUNS_ON = re.compile(r"^\s*runs-on:")
MAPPING_KEY = re.compile(r"^\s*[\w-]+:(?:\s|$)")
# Runner routing in a pinned revision: runs-on values and input defaults.
ROUTING = re.compile(r"^\s*(?:runs-on|default):")
PINNED = re.compile(r"TogetherWeOwn/\.github/(\.github/workflows/[\w.-]+\.ya?ml)@([0-9a-f]{40})")


def workflow_files():
    return sorted(p for d in WORKFLOW_DIRS for p in d.glob("*.y*ml"))


def keyed_values(text, key):
    """Yield (line number, lines) for each `key:` entry, with its folded,
    literal, sequence or flow continuation lines (anything indented deeper).
    An entry whose value is a nested mapping (a reusable input named
    `runs-on`) is skipped; its `default:` is an entry of its own."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not key.match(line):
            continue
        indent = len(line) - len(line.lstrip())
        block = [line]
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt)
        rest = [l for l in block[1:] if l.strip()]
        if not line.split(":", 1)[1].strip() and rest and MAPPING_KEY.match(rest[0]):
            continue
        yield i + 1, block


def violations(text):
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if SELF_HOSTED.search(line):
            found.append((n, "names a self-hosted label", line.strip()))
    for n, block in keyed_values(text, RUNS_ON):
        if any("vars." in line for line in block):
            found.append((n, "takes its runner from a variable", block[0].strip()))
    return found


def routing_violations(text):
    found = []
    for n, block in keyed_values(text, ROUTING):
        found += [(n + k - 1, why, line) for k, why, line in violations("\n".join(block))]
    return found


def pinned_reusables():
    pins = set()
    for p in workflow_files():
        pins.update(PINNED.findall(p.read_text()))
    return sorted(pins)


def show(sha, path):
    def run(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)

    if run("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        run("fetch", "--quiet", "origin", sha)
    return run("show", f"{sha}:{path}")


class Scanner(unittest.TestCase):
    def test_flags_self_hosted_labels(self):
        for line in [
            "    runs-on: [self-hosted, linux]",
            "    runs-on: ${{ fromJSON(github.event.repository.private && '[\"self-hosted\",\"two-selfhosted\"]' || '[\"ubuntu-latest\"]') }}",
            "      runs-on-json: '[\"two-isolated\"]'",
            "    # Private repos: run on the self-hosted pool.",
        ]:
            self.assertTrue(violations(line), line)

    def test_flags_runner_from_variable(self):
        self.assertTrue(violations("    runs-on: ${{ fromJSON(vars.CI_RUNNER) }}"))
        folded = "    runs-on: >-\n      ${{ fromJSON(vars.CI_RUNNER) }}\n    timeout-minutes: 5\n"
        self.assertTrue(violations(folded))

    def test_pinned_revision_checks_routing_only(self):
        old = (
            "        description: private callers pass their self-hosted pool\n"
            "        default: '[\"ubuntu-latest\"]'\n"
            "    runs-on: ${{ fromJSON(inputs.runs-on-json) }}\n"
        )
        self.assertEqual(routing_violations(old), [])
        self.assertTrue(routing_violations(
            "    runs-on: ${{ fromJSON(github.event.repository.private && '[\"self-hosted\"]' || '[\"ubuntu-latest\"]') }}"
        ))
        self.assertTrue(routing_violations("        default: '[\"self-hosted\",\"linux\"]'"))
        folded = "        default: >-\n          [\"self-hosted\", \"linux\"]\n        type: string\n"
        self.assertEqual([n for n, _, _ in routing_violations(folded)], [2])
        sequence = "    runs-on:\n      - self-hosted\n      - linux\n"
        self.assertEqual([n for n, _, _ in routing_violations(sequence)], [2])
        named_input = (
            "      runs-on:\n"
            "        description: e.g. self-hosted for a private caller\n"
            "        default: '[\"ubuntu-latest\"]'\n"
        )
        self.assertEqual(routing_violations(named_input), [])

    def test_hosted_runners_pass(self):
        for line in [
            "    runs-on: ubuntu-latest",
            "    runs-on: ${{ fromJSON(inputs.runs-on-json) }}",
            "      runs-on-json: '[\"ubuntu-latest\"]'",
        ]:
            self.assertEqual(violations(line), [], line)


class Repo(unittest.TestCase):
    def test_workflows_found(self):
        names = {p.name for p in workflow_files()}
        self.assertIn("pr-standards-required.yml", names)
        self.assertIn("test-guard.yml", names)

    def test_no_workflow_uses_self_hosted_runners(self):
        problems = [
            f"{p.relative_to(ROOT)}:{n}: {why}: {line}"
            for p in workflow_files()
            for n, why, line in violations(p.read_text())
        ]
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_pinned_reusables_route_to_hosted_runners(self):
        pins = pinned_reusables()
        self.assertTrue(pins, "no pinned reusables found")
        problems = []
        for path, sha in pins:
            shown = show(sha, path)
            if shown.returncode != 0:
                problems.append(f"{path}@{sha}: pinned commit not fetchable")
                continue
            problems += [f"{path}@{sha}:{n}: {why}: {line}" for n, why, line in routing_violations(shown.stdout)]
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
