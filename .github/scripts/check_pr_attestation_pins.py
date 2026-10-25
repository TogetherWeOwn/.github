"""Check the content at both attestation pins; requires fetch-depth: 0.

Local/CI developer test only: a stale pin is an error, not an attestation verdict.
Do not run against untrusted PR code with a write token.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check(pattern, workflow, target):
    matches = re.findall(pattern, (ROOT / workflow).read_text())
    if len(matches) != 1:
        raise ValueError(f"expected one full SHA in {workflow}")
    pin = matches[0]
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{pin}:{target}"], capture_output=True, check=False
    )
    if result.returncode:
        raise ValueError(f"pin {pin} is unavailable locally; fetch full branch history before checking")
    if result.stdout != (ROOT / target).read_bytes():
        raise ValueError(f"stale pin {pin}: {target} differs; repin to the commit carrying the current content")
    print(f"pin content verified: {target} @ {pin}")


def main():
    try:
        check(r"(?m)^          ref: ([a-f0-9]{40})$", ".github/workflows/pr-attestation.yml",
              ".github/scripts/pr_attestation.py")
        check(r"uses: TogetherWeOwn/\.github/\.github/workflows/pr-attestation\.yml@([a-f0-9]{40})(?:\s|$)",
              ".github/workflows/pr-attestation-required.yml", ".github/workflows/pr-attestation.yml")
    except ValueError as exc:
        print(f"::error::attestation-pincheck: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
