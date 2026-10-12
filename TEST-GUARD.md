# test-guard: Builder/Test-Author separation gate

Reusable workflow: `.github/workflows/test-guard.yml`. The Builder must
not modify acceptance tests written by the Test Author (SDLC factory
design, section (c); evidence: AgentCoder). Enforcement is deterministic
(git trailer + CI gate), not a new session.

## How it works

On each PR the gate diffs against the merge base and matches changed
files against the test globs. If test files changed, it passes only when
ownership is proven: the PR carries the marker label (default
`tests-by-test-author`, set by the Test Author/QA flow) **or** every
test-touching commit carries a Test Author `Paperclip-Agent` trailer.
Otherwise it warns (`warn` mode, the default) or fails (`enforce` mode).

A commit is Test Author-attributed when its LAST `Paperclip-Agent:`
trailer value matches an id in `test-author-ids` (the host commit-msg
hook appends trailers from the run env and overwrites pre-existing
ones, so the last line is the hook's), or carries a legacy test/QA
`(Paperclip agent)` co-author (pre-hook history only).
Trailers are advisory: a deliberately forged lone trailer passes the
gate. The boundary is the hook — platform-set env, overwrite semantics,
audit log — not the string. Deliberate forgery means tampering with the
run env, which is auditable, not a role mix-up, which is what this gate
catches.

## Attribution (live)

One host-side choke point, no per-agent config: the host
`core.hooksPath` commit-msg hook appends `Paperclip-Agent: <agent-id>`
and `Paperclip-Run: <run-id>` to every agent commit. Idempotent,
fail-open, never invokes git (clear of the per-run shim recursion trap).
Source: company ops `ops/worker-vps/git-hooks/commit-msg`.

- Test Author id: `2c980cec-9df5-40a3-80c5-00b3b9448fbb`
- Builder ids: `2fe4d7f9-…`, `69c12a25-…`, `7179efed-…` (any non-Test-Author
  trailer, or none, counts as builder-touched)

If the Test Author agent is ever replaced, update `test-author-ids` —
never the workflow.

## Default test globs

The defaults:

```
**/*.test.*, **/*.spec.*, **/__tests__/**,
tests/**, **/tests/**, test/**, **/test/**,
*_test.go, **/tests/*.rs
```

## Caller (add as a NON-required check; renames strand open PRs)

```yaml
name: test-guard

on:
  # labeled/unlabeled: the marker label is usually set after the PR
  # exists; without these the gate never re-evaluates a fresh label.
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled, unlabeled]

permissions:
  contents: read

concurrency:
  group: test-guard-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: true

jobs:
  # Keep id and name byte-identical: a future org ruleset requires `test-guard`.
  test-guard:
    name: test-guard
    uses: TogetherWeOwn/.github/.github/workflows/test-guard.yml@<pinned-sha>
```

No secrets, no paid minutes beyond a ~30s ubuntu-latest run.

## Per-repo overrides (`.github/test-guard.json`)

```json
{
  "testGlobs": ["tests/**", "spec/**"],
  "mode": "warn",
  "markerLabel": "tests-by-test-author",
  "testAuthorTokens": ["test-author"],
  "testAuthorIds": ["2c980cec-9df5-40a3-80c5-00b3b9448fbb"]
}
```

All keys optional (`testGlobs`, `mode`, `markerLabel`,
`testAuthorIds`). Present keys beat the workflow inputs. Trust,
stated precisely: the file is read from the BASE ref at the hard-coded
path `.github/test-guard.json`, so a PR cannot neuter its gate via the
config file. But on `pull_request` events the caller workflow comes
from the PR itself, so a PR-edited caller (mode/globs inputs) can still
weaken its own run. Enforce is therefore trustworthy ONLY as an
org-ruleset required workflow (sourced from the `.github` repo at a
pinned ref) or with caller + config CODEOWNERS-protected. Rollout step
4 requires the WORKFLOW, not just the check name. Until then the gate
is warn-mode tripwire, not a security boundary.
A PR that adds or edits the config file is judged by the base policy
and flagged in the step summary; the change takes effect for later PRs.

## Rollout

1. Warn mode org-wide: land this workflow, add callers (non-required) to
   two-bot-next, two-web-next, kofra, nntune, routeware-shadow-api,
   paperclip-ops-tooling, paperclip-plugin-model-capacity.
2. Watch `would_fail`/`verdict` outputs for ~20 PRs carrying correct
   trailers; false positives must be zero.
3. Flip per repo by committing a NEW config file (repos start without
   one; do NOT flip the default here first):

```sh
# flip <repo> to enforce (after Test Author flow live + ~20 clean PRs)
git clone <repo> && cd <repo> &&
  cat > .github/test-guard.json <<'EOF'
{
  "mode": "enforce"
}
EOF
  git add .github/test-guard.json && git commit -m "ci(test-guard): enforce Builder/Test-Author separation" && git push
```

   The flip PR itself is judged by the old (warn) policy since the
   config is read from the base ref; enforce bites from the next PR on.

4. Only then, optionally add an org-ruleset REQUIRED WORKFLOW for
   `test-guard` (sourced from this repo at a pinned ref) — requiring
   the check NAME alone is not enough, since a PR-edited caller can
   satisfy a name check with weakened inputs.

## Rollback

- Workflow: revert the caller PR (or the reusable-workflow PR); the gate
  is advisory in warn mode, so removal unblocks nothing that was blocked.
- Attribution hook: `git config --global --unset core.hooksPath` on the
  host + restore `~/.gitconfig` from
  `ops/worker-vps/git-hooks/backup-20261012T1645Z/` (UNDO in
  `ops/worker-vps/operator-actions.log`).
