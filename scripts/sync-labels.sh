#!/usr/bin/env bash
# Sync the org core label set to one or more repos. Idempotent: creates
# missing labels and aligns color/description on existing ones. Never
# deletes or renames a label, so repo-specific labels are untouched.
#
# Usage:
#   scripts/sync-labels.sh [--dry-run] OWNER/REPO [OWNER/REPO ...]
#
# Requires: gh (authenticated with repo admin/labels scope).
# Rerun when a repo is created or the core set below changes.
set -euo pipefail

dry_run=0
repos=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) repos+=("$arg") ;;
  esac
done

if [ "${#repos[@]}" -eq 0 ]; then
  echo "usage: $0 [--dry-run] OWNER/REPO [OWNER/REPO ...]" >&2
  exit 2
fi

# name|color|description — GitHub default 9 (standard colors/descriptions)
# plus the org additions. `security` is triage routing only: vulnerability
# details still belong in a private advisory, never a labelled issue.
labels=(
  "bug|d73a4a|Something isn't working"
  "documentation|0075ca|Improvements or additions to documentation"
  "duplicate|cfd3d7|This issue or pull request already exists"
  "enhancement|a2eeef|New feature or request"
  "good first issue|7057ff|Good for newcomers"
  "help wanted|008672|Extra attention is needed"
  "invalid|e4e669|This doesn't seem right"
  "question|d876e3|Further information is requested"
  "wontfix|ffffff|This will not be worked on"
  "security|b60205|Security-sensitive; details belong in a private advisory"
  "dependencies|0366d6|Pull requests that update a dependency file"
  "ci|1d76db|Continuous integration and workflow changes"
)

GH="${GH:-gh}"

run() {
  if [ "$dry_run" -eq 1 ]; then
    echo "dry-run: $*"
  else
    "$@" >/dev/null
  fi
}

failures=0
for repo in "${repos[@]}"; do
  echo "== $repo"
  for entry in "${labels[@]}"; do
    IFS='|' read -r name color description <<< "$entry"
    encoded=$(printf '%s' "$name" | jq -sRr @uri 2>/dev/null || printf '%s' "$name" | sed 's/ /%20/g')
    # Only HTTP 404 means "missing". Any other probe failure (auth, rate
    # limit, network, bad repo) is a failure for that label, never a create.
    if probe=$("$GH" api "repos/$repo/labels/$encoded" 2>&1); then
      action=update
    elif printf '%s' "$probe" | grep -q "HTTP 404"; then
      action=create
    else
      echo "  FAIL (lookup): $name: $probe" >&2
      failures=$((failures+1))
      continue
    fi
    if [ "$action" = update ]; then
      run "$GH" api -X PATCH "repos/$repo/labels/$encoded" \
        -f color="$color" -f description="$description" \
        && echo "  ok (update): $name" || { echo "  FAIL: $name" >&2; failures=$((failures+1)); }
    else
      run "$GH" api -X POST "repos/$repo/labels" \
        -f name="$name" -f color="$color" -f description="$description" \
        && echo "  ok (create): $name" || { echo "  FAIL: $name" >&2; failures=$((failures+1)); }
    fi
  done
done

if [ "$failures" -ne 0 ]; then
  echo "$failures label operation(s) failed" >&2
  exit 1
fi
echo "done: ${#labels[@]} core labels synced to ${#repos[@]} repo(s); nothing deleted"
