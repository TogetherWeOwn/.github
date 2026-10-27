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


def plain_mapping_keys(lines):
    """Fail closed on quoted, spaced, duplicate or merge keys, not parse YAML."""
    keys = []
    for line in lines:
        match = re.fullmatch(r" *([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t].*)?", line)
        if match is None:
            return False
        keys.append(match.group(1))
    return len(keys) == len(set(keys))


def check(caller, current):
    # Support only the shipped plain-key, two-space mapping shape. Validate
    # boundaries before selecting uses; an unsupported neighbor must not leak in.
    text = (ROOT / caller).read_text(encoding="utf-8")
    roots = re.findall(r"(?m)^[^\s#].*$", text)
    sections = re.findall(r"(?ms)^jobs:\n(.*?)(?=^[^\s#]|\Z)", text)
    jobs = sections[0] if len(sections) == 1 and plain_mapping_keys(roots) else ""
    headers = re.findall(r"(?m)^  [^\s#].*$", jobs)
    blocks = re.findall(r"(?ms)^  changes:[ \t]*(?:#.*)?\n(.*?)(?=^  [^\s#]|\Z)", jobs)
    block = blocks[0] if len(blocks) == 1 and plain_mapping_keys(headers) else ""
    fields = re.findall(r"(?m)^    [^\s#].*$", block)
    uses = re.findall(r"(?m)^    uses: (.+)$", block) if plain_mapping_keys(fields) else []
    match = re.fullmatch(
        r"(?P<quote>['\"]?)TogetherWeOwn/\.github/\.github/workflows/changes\.yml@"
        r"(?P<pin>[0-9a-f]{40})(?P=quote)(?:[ \t]+#.*)?[ \t]*",
        uses[0],
    ) if len(uses) == 1 else None
    if match is None:
        raise ValueError(f"expected one active changes.yml uses: with a 40-character immutable SHA "
                         f"and plain two-space mapping keys; {REPIN}")
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
