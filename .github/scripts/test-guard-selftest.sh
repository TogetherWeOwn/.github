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
mk k; cd "$T/k" && mkdir -p tests && echo x > tests/f.rs && git add . && cmt "test: evil merge

Paperclip-Agent: 69c12a25-80df-4c80-8026-f12651e653eb" "69c12a25-80df-4c80-8026-f12651e653eb"
# clean merge of a TA branch into an untrailered branch
mk m2; cd "$T/m2" && git checkout -qb side && mkdir tests && echo t > tests/n.test.ts && git add . && cmt "test: side" "$TA" && git checkout -q main && echo s2 > src2.ts && git add . && cmt "src: more" && git merge side -qm "Merge branch 'side'"
# conflict merge resolved by unknown merger over a TA side
mk m; cd "$T/m" && mkdir tests && printf 'a\nb\n' > tests/c.test.ts && git add . && cmt "test: base" && git update-ref refs/remotes/origin/main main && git checkout -qb side && printf 'a\nside\n' > tests/c.test.ts && git add . && cmt "test: side" "$TA" && git checkout -q main && printf 'a\nmain\n' > tests/c.test.ts && git add . && cmt "src: main" && git merge side >/dev/null 2>&1; printf 'a\nevil\n' > tests/c.test.ts && git add . && git commit -qm "Merge branch 'side'"

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

echo "test-guard selftest: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
