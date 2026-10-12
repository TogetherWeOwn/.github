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

A commit is Test Author-attributed when its `Paperclip-Agent:` trailer
value matches an id in `test-author-ids` (primary; appended by the host
commit-msg hook from the run env) or a token in `test-author-tokens`
(fallback), or carries a legacy test/QA `(Paperclip agent)` co-author.

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
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

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

All keys optional. Present keys beat the workflow inputs.

## Rollout

1. Warn mode org-wide: land this workflow, add callers (non-required) to
   two-bot-next, two-web-next, kofra, nntune, routeware-shadow-api,
   paperclip-ops-tooling, paperclip-plugin-model-capacity.
2. Watch `would_fail`/`verdict` outputs for ~20 PRs carrying correct
   trailers; false positives must be zero.
3. Flip per repo with a one-line PR (do NOT flip the default here first):

```sh
# flip <repo> to enforce (after Test Author flow live + ~20 clean PRs)
git clone <repo> && cd <repo> &&
  python3 -c "import json;p='.github/test-guard.json';d=json.load(open(p));d['mode']='enforce';json.dump(d,open(p,'w'),indent=2)" 
```

4. Only then, optionally require `test-guard` in the org ruleset.

## Rollback

- Workflow: revert the caller PR (or the reusable-workflow PR); the gate
  is advisory in warn mode, so removal unblocks nothing that was blocked.
- Attribution hook: `git config --global --unset core.hooksPath` on the
  host + restore `~/.gitconfig` from
  `ops/worker-vps/git-hooks/backup-20261012T1645Z/` (UNDO in
  `ops/worker-vps/operator-actions.log`).
