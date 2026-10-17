#!/usr/bin/env bash
# Fixture test for the org Semgrep starter pack.
#
# Every live sink must fire at least once in its own language, and the
# disabled template rules must stay silent. Run from CI (`semgrep selftest`)
# or locally with semgrep >= 1.128.1 on PATH:
#   bash semgrep-rules/tests/test_rules.sh
set -euo pipefail

pack_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

semgrep scan --config "$pack_dir/generic-dangerous-functions.yaml" \
  --json --quiet "$pack_dir/fixtures" > "$tmp_dir/sinks.json"
semgrep scan --config "$pack_dir" \
  --json --quiet "$pack_dir/fixtures" > "$tmp_dir/all.json"

python3 - "$tmp_dir/sinks.json" "$tmp_dir/all.json" <<'PY'
import json
import sys

sinks = json.load(open(sys.argv[1]))["results"]
all_results = json.load(open(sys.argv[2]))["results"]


def rule_of(finding):
    return finding["check_id"].split(".")[-1]


hits = {}
for finding in sinks:
    key = (rule_of(finding), finding["path"].split("/")[-1])
    hits[key] = hits.get(key, 0) + 1

# (rule, fixture file): minimum findings. Each sink line must fire.
expected = {
    ("tier0-no-eval", "sink-eval.js"): 1,
    ("tier0-no-eval-py", "sink-eval.py"): 1,
    ("tier0-no-shell-exec-interpolation", "sink-shell.js"): 1,
    ("tier0-no-shell-exec-py", "sink-shell.py"): 1,
}

failed = False
for (rule, fixture), minimum in sorted(expected.items()):
    got = hits.get((rule, fixture), 0)
    ok = got >= minimum
    print(f"{'ok' if ok else 'MISS'}: {rule} on {fixture}: {got}, want >={minimum}")
    failed |= not ok

templates_fired = sorted(
    {rule_of(finding) for finding in all_results if rule_of(finding).startswith("tier0-template-")}
)
if templates_fired:
    print(f"MISS: disabled templates fired: {', '.join(templates_fired)}")
    failed = True
else:
    print("ok: disabled template rules silent")

sys.exit(1 if failed else 0)
PY
