#!/usr/bin/env python3
"""Embed the reviewed driver in the reusable workflow; never load caller code."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / ".github/workflows/public-pr-security.yml"


def render():
    template = (ROOT / "workflow.yml.in").read_text()
    driver = (ROOT / "gate.py").read_text()
    return template.replace("@DRIVER@", "\n".join("          " + line if line else "" for line in driver.splitlines()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = render()
    if args.check:
        if OUTPUT.read_text() != content:
            raise SystemExit("Embedded gate driver is stale; run python3 -I security-gate/render_workflow.py")
        print("Embedded gate driver matches its tested source")
    else:
        OUTPUT.write_text(content)
