#!/bin/bash
# Regression tests for the test-guard reusable gate (TEST-GUARD.md).
#
# Builds synthetic git repos plus a fake `gh`, runs the gate's embedded
# python under `python3 -I -` exactly like production, and asserts
# verdicts. Fails non-zero on any mismatch. Runs in ~30s, stdlib only.
#
# Usage: test-guard-selftest.sh  (from anywhere; finds the reusable next
# to this script at ../workflows/test-guard.yml)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WF="$HERE/../workflows/test-guard.yml"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0

# 1. Extract the embedded gate exactly as the workflow runs it.
python3 - "$WF" "$T/guard.py" <<'EOF'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r"python3 -I - <<'PYEOF'\n(.*?)\n          PYEOF", src, re.S)
if not m:
    print("selftest: embedded gate not found"); sys.exit(99)
out = []
for ln in m.group(1).split('\n'):
    if not (ln.startswith('          ') or ln.strip() == ''):
        print("selftest: bad indent"); sys.exit(99)
    out.append(ln[10:] if ln.strip() else '')
open(sys.argv[2], 'w').write('\n'.join(out) + '\n')
EOF
[ $? -eq 0 ] || { echo "selftest: extraction failed"; exit 1; }

# 2. Fake `gh`: canned issue-events / pulls per GH_STUB_MODE.
mkdir -p "$T/fakebin"
cat > "$T/fakebin/gh" <<'EOF'
#!/bin/sh
EV='[{"event":"labeled","label":{"name":"tests-by-test-author"},"actor":{"login":"qa-flow"}}]'
EV_A='[{"event":"labeled","label":{"name":"tests-by-test-author"},"actor":{"login":"Rick7C2"}}]'
EV2A='[{"event":"unrelated","actor":{"login":"x"}}]'
EV2B='[{"event":"labeled","label":{"name":"tests-by-test-author"},"actor":{"login":"qa-flow"}}]'
PULLS='[{"number":7,"user":{"login":"Rick7C2"}}]'
PULL='{"number":7,"user":{"login":"Rick7C2"},"labels":[{"name":"tests-by-test-author"}]}'
path="$2"
case "$path" in *"/events"*) kind=events;; *"/pulls"*) kind=pulls;; *) kind=other;; esac
case "$GH_STUB_MODE" in
  paged) if [ "$kind" = events ]; then printf '%s%s' "$EV2A" "$EV2B"; elif [ "$kind" = pulls ]; then echo "$PULL"; else echo '[]'; fi;;
  other) if [ "$kind" = events ]; then echo "$EV"; elif [ "$kind" = pulls ]; then echo "$PULL"; else echo '[]'; fi;;
  author) if [ "$kind" = events ]; then echo "$EV_A"; elif [ "$kind" = pulls ]; then echo "$PULL"; else echo '[]'; fi;;
  *) echo '{"message":"boom"}'; exit 1;;
esac
EOF
chmod +x "$T/fakebin/gh"

TA=2c980cec-9df5-40a3-80c5-00b3b9448fbb
GLOBS='["**/*.test.*","**/*.spec.*","**/__tests__/**","tests/**","**/tests/**","test/**","**/test/**","*_test.go","**/tests/*.rs"]'

# mk <name>: repo with origin/main at a base commit.
mk() { mkdir -p "$T/$1" && cd "$T/$1" && git init -q . && git config user.email t@t && git config user.name t && git checkout -qb main && echo s > src.ts && git add . && git commit -qm base && git update-ref refs/remotes/origin/main main; }
# cmt <msg> [trailer]: commit staged changes.
cmt() { if [ $# -gt 1 ]; then git commit -qm "$1

Paperclip-Agent: $2"; else git commit -qm "$1"; fi; }
# ev <name> <labels-json>: event fixture (author Rick7C2, repo stub).
ev() { printf '{"pull_request":{"number":7,"user":{"login":"Rick7C2"},"labels":%s},"repository":{"full_name":"o/r"}}' "$2" > "$T/$1.json"; }
ev nolabel '[]'; ev label '[{"name":"tests-by-test-author"}]'

# run <repo> <mode> <event> <stub>: execute gate, print "verdict exit".
run() {
  cd "$T/$1" && GH_TOKEN=dummy GH_STUB_MODE="$4" GITHUB_EVENT_PATH="$T/$3.json" MODE="$2" \
    TEST_GLOBS_JSON="$GLOBS" MARKER_LABEL=tests-by-test-author TEST_AUTHOR_IDS="$TA" \
    EVENT_BASE=main GITHUB_REPOSITORY=o/r GITHUB_OUTPUT="$T/out" GITHUB_STEP_SUMMARY="$T/sum" \
    PATH="$T/fakebin:$PATH" python3 -I - < "$T/guard.py" >/dev/null 2>&1
  ec=$?; echo "$(grep -E '^verdict=' "$T/out" | tail -1 | cut -d= -f2) $ec"
}
# check <name> <actual> <want-verdict> <want-exit>
check() { if [ "$2" = "$3 $4" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL: $1 got [$2] want [$3 $4]"; fi; }

# --- fixtures ---
mk a; cd "$T/a" && echo x > src2.ts && git add . && cmt "feat: src"
mk b; cd "$T/b" && mkdir test && echo x > test/a.test.ts && git add . && cmt "test: edit"
mk c; cd "$T/c" && mkdir test && echo x > test/a.test.ts && git add . && cmt "test: ta edit" "$TA"
# clean merge of a TA branch into an untrailered branch
mk m2; cd "$T/m2" && git checkout -qb side && mkdir tests && echo t > tests/n.test.ts && git add . && cmt "test: side" "$TA" && git checkout -q main && echo s2 > src2.ts && git add . && cmt "src: more" && git merge side -qm "Merge branch 'side'"
# conflict merge resolved by unknown merger over a TA side
mk m; cd "$T/m" && mkdir tests && printf 'a\nb\n' > tests/c.test.ts && git add . && cmt "test: base" && git update-ref refs/remotes/origin/main main && git checkout -qb side && printf 'a\nside\n' > tests/c.test.ts && git add . && cmt "test: side" "$TA" && git checkout -q main && printf 'a\nmain\n' > tests/c.test.ts && git add . && cmt "src: main" && git merge side >/dev/null 2>&1; printf 'a\nevil\n' > tests/c.test.ts && git add . && git commit -qm "Merge branch 'side'"
# rename-out: test file renamed out of the globs (rename detection must
# not hide the deleted test path)
mk j; cd "$T/j" && mkdir tests && echo t > tests/old.test.ts && git add . && cmt "test: base" && git update-ref refs/remotes/origin/main main && git mv tests/old.test.ts src-moved.js && git commit -qm "refactor: move test helper"
# non-ASCII path (quotepath evasion without -z + quotepath=false):
# file exists at base, edited on top, so the diff exercises raw bytes.
mk l; cd "$T/l" && mkdir tests && printf 'x' > "tests/caf\xc3\xa9.py" && git add . && git commit -qm "test: base" && git update-ref refs/remotes/origin/main main && printf 'y' >> "tests/caf\xc3\xa9.py" && git add . && cmt "test: unicode edit"
# forged TA trailer first, real Builder trailer last (last-wins)
mk k; cd "$T/k" && mkdir -p tests && echo x > tests/f.rs && git add . && git commit -qm "test: forged then real" -m "Paperclip-Agent: $TA" -m "Paperclip-Agent: 69c12a25-80df-4c80-8026-f12651e653eb"
# newline-in-path injection probe (assertions on output below, not verdict)
mk q; cd "$T/q" && mkdir tests && EVIL="$(printf 'tests/evil\n::error::INJECTED.py')" && touch "$EVIL" && git add . && cmt "test: evil"
# config neuter attempt: PR-head config narrows globs, base has none —
# the gate must judge by the base policy and still flag the tests
mk i; cd "$T/i" && mkdir tests && echo t > tests/a.rs && git add . && cmt "test: base" && git update-ref refs/remotes/origin/main main && mkdir -p .github && printf '{"testGlobs":["nothing-matches/**"]}' > .github/test-guard.json && echo t2 >> tests/a.rs && git add . && cmt "test: tweak"
# legacy pre-hook QA co-author, no trailer at all
mk g2; cd "$T/g2" && mkdir tests && echo t > tests/qa.rs && git add . && git commit -qm "test: legacy" -m "Co-authored-by: QA & Release Engineer (Paperclip agent) <qa@paperclip.local>"
# master-default repo for the merge_group base fallback (origin/master only)
rm -rf "$T/mm" && mkdir -p "$T/mm" && cd "$T/mm" && git init -q . && git config user.email t@t && git config user.name t && git checkout -qb master && echo s > src.ts && git add . && git commit -qm base && git update-ref refs/remotes/origin/master master && mkdir tests && echo t > tests/a.test.ts && git add . && cmt "test: ta" "$TA"
MMSHA="$(git -C "$T/mm" rev-parse HEAD)"
printf '{"merge_group":{"head_sha":"%s","base_ref":"refs/heads/master"},"repository":{"full_name":"o/r"}}' "$MMSHA" > "$T/mg.json"

# --- assertions (warn mode unless noted) ---
check "clean" "$(run a warn nolabel fail)" clean 0
check "unknown-no-label" "$(run b warn nolabel fail)" builder-touched 0
check "unknown-no-label-enforce" "$(run b enforce nolabel fail)" builder-touched 1
check "ta" "$(run c warn nolabel fail)" attributed 0
check "label-single-page" "$(run b warn label other)" attributed 0
check "label-multi-page" "$(run b warn label paged)" attributed 0
check "label-self-applied" "$(run b warn label author)" error 0
check "builder-beats-label" "$(run k warn label other)" builder-touched 0
check "unneeded-label-ignored" "$(run c warn label author)" attributed 0
check "clean-merge-keeps-provenance" "$(run m2 warn nolabel fail)" attributed 0
check "conflict-resolution-caught" "$(run m warn nolabel fail)" builder-touched 0
check "rename-out-flagged" "$(run j warn nolabel fail)" builder-touched 0
check "unicode-raw" "$(run l warn nolabel fail)" builder-touched 0
check "forgery-last-wins" "$(run k warn nolabel fail)" builder-touched 0
check "legacy-qa" "$(run g2 warn nolabel fail)" attributed 0
check "config-neuter-defeated" "$(run i warn nolabel fail)" builder-touched 0
# merge_group on a master-default repo with the PR lookup failing is
# covered by falling back to the payload base_ref (refs/heads/master).
# run() cannot send merge_group events (no PR number for the API), so
# this one is inline: no token, lookup unavailable, payload base only.
cd "$T/mm" && GH_TOKEN= GITHUB_EVENT_PATH="$T/mg.json" MODE=warn \
  TEST_GLOBS_JSON="$GLOBS" MARKER_LABEL=tests-by-test-author TEST_AUTHOR_IDS="$TA" \
  EVENT_BASE= GITHUB_REPOSITORY=o/r GITHUB_OUTPUT="$T/out" GITHUB_STEP_SUMMARY="$T/sum" \
  PATH="$T/fakebin:$PATH" python3 -I - < "$T/guard.py" >/dev/null 2>&1
check "merge-group-payload-base" "$(grep -E '^verdict=' "$T/out" | tail -1 | cut -d= -f2) $?" attributed 0
# injection: verdict is incidental; the assertion is on OUTPUT — no raw
# workflow-command line may escape (a mutant with esc() as identity
# would emit a bare `::error::INJECTED` line).
cd "$T/q" && GH_TOKEN=dummy GH_STUB_MODE=fail GITHUB_EVENT_PATH="$T/nolabel.json" MODE=warn \
  TEST_GLOBS_JSON="$GLOBS" MARKER_LABEL=tests-by-test-author TEST_AUTHOR_IDS="$TA" \
  EVENT_BASE=main GITHUB_REPOSITORY=o/r GITHUB_OUTPUT="$T/out" GITHUB_STEP_SUMMARY="$T/sum" \
  PATH="$T/fakebin:$PATH" python3 -I - < "$T/guard.py" >/dev/null 2>&1
if grep -q '^::error::' "$T/sum"; then FAIL=$((FAIL+1)); echo "FAIL: injection raw ::error:: line in summary"; else PASS=$((PASS+1)); fi
if grep -q '%0A' "$T/sum"; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL: injection path not escaped"; fi

echo "test-guard selftest: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
