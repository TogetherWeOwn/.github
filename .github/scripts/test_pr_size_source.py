"""Offline source-trust contracts for both reusable PR-size workflows.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_pr_size*.py'
The existing CI discovery includes this file without any workflow changes.

For a cross-repository caller or fork PR, github.repository, github.sha and
PR head/base values describe caller-owned code, not this central script.
Only the reviewed central repository and immutable script snapshot may supply
executable code; github.repository remains the API target, not the checkout.
The approved snapshot is on main and carries the unchanged advisory script.
Changing that pin or script requires explicitly reviewing the new snapshot.

Like test_pr_size_workflows.py, the reader supports only these workflows'
block layout (two-space indents, scalar mappings and single-line commands),
not arbitrary YAML. Shape changes fail closed instead of guessing YAML or
GitHub expression semantics. Negative controls mutate strings in memory only.
"""

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CENTRAL_REPOSITORY = "TogetherWeOwn/.github"
REVIEWED_SHA = "fcfe691790ac925e8c4cf5e191a5004d4d991392"
SCRIPT = ".github/scripts/pr_size.py"
SCRIPT_SHA256 = "e0170a1700c312318de84aadd88e17de7ba13b4a024e1b8fa563335c1c180c61"
MODES = {"pr-size.yml": "label", "pr-size-weekly.yml": "weekly"}


def block(text, indent, key):
    """Read exactly one block and stop at its next sibling/ancestor."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line == " " * indent + key + ":"]
    if len(starts) != 1:
        raise AssertionError(f"expected one {key} block at indent {indent}")
    start = starts[0] + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#"):
            if len(line) - len(line.lstrip()) <= indent:
                break
        end += 1
    return "\n".join(lines[start:end])


def scalars(text, indent):
    """Read direct mapping keys, rejecting duplicates and unfamiliar shapes."""
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) != indent:
            continue
        match = re.fullmatch(r"\s*([\w-]+):(?:\s+(.*))?", line)
        if not match:
            raise AssertionError(f"unsupported mapping: {line!r}")
        key, value = match.groups()
        if key in result:
            raise AssertionError(f"duplicate mapping key: {key}")
        value = re.sub(r"\s+#.*$", "", value or "").strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]):
            value = value[1:-1]
        result[key] = value
    return result


def steps(text, mode):
    job = block(block(text, 0, "jobs"), 2, mode)
    raw = block(job, 4, "steps")
    # Normalize the first '- key:' line to the following keys' indentation.
    return ["        " + part for part in re.split(r"(?m)^      - ", raw)[1:]]


def checkout_options(text, mode):
    return scalars(block(steps(text, mode)[0], 8, "with"), 10)


def assert_trusted_source(test, text, mode):
    options = checkout_options(text, mode)
    # Full identity, not merely a 40-hex shape: an unreviewed commit or a
    # caller's otherwise-valid SHA is not an approved script snapshot.
    for field, expected in (("repository", CENTRAL_REPOSITORY), ("ref", REVIEWED_SHA)):
        with test.subTest(field=field):
            test.assertEqual(options.get(field), expected)


def source_fixture(text):
    """Known-good source fields for controls, even before the fix is present."""
    for field, value in (("repository", CENTRAL_REPOSITORY), ("ref", REVIEWED_SHA)):
        text, count = re.subn(
            rf"(?m)^          {field}: .*$", f"          {field}: {value}", text
        )
        if count != 1:
            raise AssertionError(f"expected one checkout {field}")
    return text


class CentralSourceContract(unittest.TestCase):
    def test_external_callers_and_fork_prs_cannot_select_executable_source(self):
        for filename, mode in MODES.items():
            with self.subTest(workflow=filename):
                assert_trusted_source(self, (WORKFLOWS / filename).read_text(), mode)

    def test_only_the_central_sparse_checkout_is_executed_without_saved_credentials(self):
        for filename, mode in MODES.items():
            with self.subTest(workflow=filename):
                text = (WORKFLOWS / filename).read_text()
                checkout, execution = steps(text, mode)
                attributes = scalars(checkout, 8)
                self.assertRegex(attributes["uses"], r"^actions/checkout@[0-9a-f]{40}$")
                self.assertLessEqual(set(attributes), {"name", "uses", "with"})
                options = checkout_options(text, mode)
                self.assertEqual(options["path"], ".pr-size-central")
                self.assertEqual(options["sparse-checkout"], SCRIPT)
                self.assertEqual(options["persist-credentials"], "false")
                self.assertEqual(
                    set(options),
                    {"repository", "ref", "path", "sparse-checkout", "persist-credentials"},
                )
                attributes = scalars(execution, 8)
                self.assertEqual(set(attributes), {"name", "env", "run"})
                self.assertEqual(attributes["run"], f"python3 .pr-size-central/{SCRIPT} {mode}")

    def test_caller_context_is_data_for_the_api_not_a_script_selector(self):
        for filename, mode in MODES.items():
            with self.subTest(workflow=filename):
                text = (WORKFLOWS / filename).read_text()
                env = scalars(block(steps(text, mode)[1], 8, "env"), 10)
                expected = {"GH_TOKEN": "${{ github.token }}", "REPO": "${{ github.repository }}"}
                if mode == "label":
                    expected.update({
                        "PR_NUMBER": "${{ inputs.pr-number != '' && inputs.pr-number || github.event.pull_request.number }}",
                        "PR_SIZE_THRESHOLD": "400",
                        "PR_SIZE_EXCLUDE_EXTRA": "${{ inputs.exclude-extra }}",
                    })
                self.assertEqual(env, expected)
                inputs = scalars(block(block(text, 0, "on"), 4, "inputs"), 6)
                allowed = {"runs-on", "pr-number", "exclude-extra"} if mode == "label" else {"runs-on"}
                self.assertEqual(set(inputs), allowed)

    def test_label_write_and_weekly_read_scopes_remain_separate(self):
        for filename, mode in MODES.items():
            with self.subTest(workflow=filename):
                text = (WORKFLOWS / filename).read_text()
                self.assertEqual(set(scalars(block(text, 0, "jobs"), 2)), {mode})
                self.assertEqual(scalars(block(text, 0, "permissions"), 2), {"contents": "read"})
                job = block(block(text, 0, "jobs"), 2, mode)
                level = "write" if mode == "label" else "read"
                self.assertEqual(
                    scalars(block(job, 4, "permissions"), 6),
                    {"contents": "read", "issues": level, "pull-requests": level},
                )

    def test_script_bytes_still_match_the_reviewed_advisory_snapshot(self):
        self.assertEqual(hashlib.sha256((ROOT / SCRIPT).read_bytes()).hexdigest(), SCRIPT_SHA256)


class SourceNegativeControls(unittest.TestCase):
    def test_reviewed_source_fixture_is_accepted(self):
        for filename, mode in MODES.items():
            with self.subTest(workflow=filename):
                text = source_fixture((WORKFLOWS / filename).read_text())
                assert_trusted_source(self, text, mode)

    def test_caller_or_fork_repository_is_rejected(self):
        for filename, mode in MODES.items():
            good = source_fixture((WORKFLOWS / filename).read_text())
            for repository in (
                "${{ job.workflow_repository }}",
                "${{ github.repository }}",
                "${{ inputs.repository }}",
                "${{ github.event.pull_request.head.repo.full_name }}",
                "${{ github.event.pull_request.base.repo.full_name }}",
                "example/caller",
                "contributor/fork",
            ):
                with self.subTest(workflow=filename, repository=repository):
                    bad = good.replace(f"repository: {CENTRAL_REPOSITORY}", f"repository: {repository}")
                    with self.assertRaises(AssertionError):
                        # A plain TestCase makes each rejection observable to
                        # assertRaises rather than swallowing it in a subTest.
                        assert_trusted_source(unittest.TestCase(), bad, mode)

    def test_mutable_caller_or_unreviewed_ref_is_rejected(self):
        for filename, mode in MODES.items():
            good = source_fixture((WORKFLOWS / filename).read_text())
            for ref in (
                "${{ job.workflow_sha }}",
                "${{ github.sha }}",
                "${{ github.workflow_sha }}",
                "${{ inputs.ref }}",
                "${{ github.event.pull_request.head.sha }}",
                "${{ github.event.pull_request.head.ref }}",
                "${{ github.event.pull_request.base.sha }}",
                "${{ github.event.pull_request.base.ref }}",
                "main",
                "refs/heads/main",
                "v1",
                "refs/tags/v1",
                "refs/pull/7/head",
                "fcfe691",
                "a" * 40,
                "",
            ):
                with self.subTest(workflow=filename, ref=ref):
                    bad = good.replace(f"ref: {REVIEWED_SHA}", f"ref: {ref}")
                    with self.assertRaises(AssertionError):
                        assert_trusted_source(unittest.TestCase(), bad, mode)


if __name__ == "__main__":
    unittest.main()
