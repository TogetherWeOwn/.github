#!/usr/bin/env python3
"""Verify both changes.yml callers carry HEAD's source; needs full Git history.

Install the hash-pinned requirements-pincheck.txt before running with python -I.
"""

from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ".github/workflows/changes.yml"
CALLERS = (".github/workflows/ci.yml", "workflow-templates/starter-ci.yml")
REPIN = "repin uses: to a fetchable commit carrying HEAD's changes.yml"


class UniqueSafeLoader(yaml.SafeLoader):
    """Reject ambiguous mappings instead of silently taking the last key."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        keys = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                raise ValueError("workflow mapping keys must be scalars")
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                raise ValueError("duplicate workflow mapping key")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def load_workflow(text):
    workflow = yaml.load(text, Loader=UniqueSafeLoader)
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be a mapping")
    return workflow


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, timeout=30)


def check(caller, current):
    error = f"expected one active changes.yml uses: with a 40-character immutable SHA; {REPIN}"
    try:
        workflow = load_workflow((ROOT / caller).read_text(encoding="utf-8"))
    except (yaml.YAMLError, ValueError) as exc:
        # Parser exceptions can include source text; keep diagnostics structural.
        raise ValueError(f"{error}; invalid or duplicate workflow YAML") from exc
    jobs = workflow.get("jobs")
    changes = jobs.get("changes") if isinstance(jobs, dict) else None
    uses = changes.get("uses") if isinstance(changes, dict) else None
    match = re.fullmatch(
        r"TogetherWeOwn/\.github/\.github/workflows/changes\.yml@([0-9a-f]{40})",
        uses,
    ) if isinstance(uses, str) else None
    if match is None:
        raise ValueError(error)
    pin = match.group(1)
    # Same-PR and other branch commits are valid; do not require main ancestry.
    if git("cat-file", "-e", f"{pin}^{{commit}}").returncode:
        git("fetch", "origin", pin)
    if git("cat-file", "-e", f"{pin}^{{commit}}").returncode:
        raise ValueError(f"pin {pin} is not fetchable as a commit; {REPIN}")
    source = git("show", f"{pin}:{SOURCE}")
    if source.returncode or source.stdout != current:
        raise ValueError(f"pin {pin}: {SOURCE} differs from HEAD or is missing; {REPIN}")
    print(f"changes-pincheck: {caller}: pin {pin} carries HEAD's {SOURCE}")


def main():
    try:
        current = git("show", f"HEAD:{SOURCE}")
        if current.returncode:
            raise ValueError(f"cannot read HEAD:{SOURCE}; restore the source before repinning")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"::error::changes-pincheck: {exc}")
        return 1
    for caller in CALLERS:
        try:
            check(caller, current.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"::error::changes-pincheck: {caller}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
