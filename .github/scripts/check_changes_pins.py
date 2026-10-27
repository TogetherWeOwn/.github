#!/usr/bin/env python3
"""Verify both changes.yml callers carry HEAD's source; needs full Git history."""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ".github/workflows/changes.yml"
CALLERS = (".github/workflows/ci.yml", "workflow-templates/starter-ci.yml")
REPIN = "repin uses: to a fetchable commit carrying HEAD's changes.yml"


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, timeout=30)


def check(caller, current):
    # Like the other workflow contracts, use the shipped two-space job shape.
    # Scope to the active changes job: a comment or neighboring job is not a pin.
    jobs = (ROOT / caller).read_text(encoding="utf-8").partition("\njobs:\n")[2]
    blocks = re.findall(r"(?ms)^  changes:[ \t]*\n(.*?)(?=^  [\w-]+:|\Z)", jobs)
    uses = re.findall(r"(?m)^    uses: (.+)$", blocks[0]) if len(blocks) == 1 else []
    match = re.fullmatch(
        r"(?P<quote>['\"]?)TogetherWeOwn/\.github/\.github/workflows/changes\.yml@"
        r"(?P<pin>[0-9a-f]{40})(?P=quote)[ \t]*(?:#.*)?",
        uses[0],
    ) if len(uses) == 1 else None
    if match is None:
        raise ValueError(f"expected one active changes.yml uses: with a 40-character immutable SHA; {REPIN}")
    pin = match.group("pin")
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
