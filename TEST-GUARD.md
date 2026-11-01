# test-guard: Builder/Test-Author separation gate

Reusable workflow: `.github/workflows/test-guard.yml`. The Builder must
not modify acceptance tests written by the Test Author (SDLC factory
design, section (c); evidence: AgentCoder). Enforcement is deterministic
(git trailer + CI gate), not a new session.

## How it works

On each PR the gate diffs against the merge base and matches changed
files against the test globs (fnmatch on the raw NUL-separated path).
Each test-touching commit classifies as Test Author (`Paperclip-Agent`
trailer in `test-author-ids`, or legacy test/QA `(Paperclip agent)`
co-author on trailer-less pre-hook history), Builder (an explicit
`Paperclip-Agent` trailer that is NOT the Test Author), or unknown (no
trailer at all).

- Any Builder-classified commit fails the gate however labeled: the
  marker label excuses UNKNOWN provenance, never a known Builder hand.
- Unknown commits need the marker label (default
  `tests-by-test-author`): present AND applied by someone other than the
  PR author (actor read from the issue-events API with multi-page
  parsing, so a Builder cannot self-label its own PR). Labels are
  refreshed from the pulls API on every run (a re-run keeps its original
  event, so payload labels go stale); on `merge_group` events, whose
  payload carries no PR at all, the PR is resolved via the commits API
  and the base ref comes from the pulls API too (best-effort — the merge
  queue is off, so that path is unexercised live and fails closed when
  the label is needed). The gate runs under `python3 -I` so a PR-planted
  root module cannot shadow the stdlib it imports.
- A label that is not needed (every commit Test Author-attributed) is
  ignored, so a stale or self-applied label cannot poison a clean PR.
  An unprovable-but-needed label (API failure, unknown actor,
  self-applied) is an `error` verdict, never a silent pass.
- Otherwise it warns (`warn` mode, the default) or fails (`enforce`).

A commit is Test Author-attributed when its LAST `Paperclip-Agent:`
trailer value matches an id in `test-author-ids` (the host commit-msg
hook appends trailers from the run env and overwrites pre-existing
ones, so the last line is the hook's), or carries a legacy test/QA
`(Paperclip agent)` co-author (pre-hook history only).
Trailers are advisory: a deliberately typed trailer (e.g. `git commit
--no-verify`) passes the gate with no run-env tampering at all, so the
hook (platform-set env, overwrite semantics, audit log) is friction,
not a forgery boundary. The gate catches role mix-ups — the wrong agent
editing tests — not determined forgery, which is a conduct/audit matter.

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
  pull-requests: read # marker-label actor audit via the issue-events API

concurrency:
  group: test-guard-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: true

jobs:
  # Keep id and name byte-identical: a future org ruleset requires `test-guard`.
  test-guard:
    name: test-guard
    uses: TogetherWeOwn/.github/.github/workflows/test-guard.yml@<pinned-sha>
```

Public and private repos alike run on GitHub-hosted `ubuntu-latest` (the
`runs-on-json` default). The org has no self-hosted runners; private repos
spend a few seconds of Actions minutes per PR.

Enforce (step 4) does NOT use this caller: it uses the required
workflow `.github/workflows/test-guard-required.yml` in this repo
(pull_request + merge_group triggers, pinned full-path call into the
reusable at a fixed SHA, bare `test-guard` aggregator check). A ruleset
that merely requires the check NAME can still be satisfied by a
PR-edited caller with weakened inputs — require the WORKFLOW.
`pincheck` (a job in this repo's `ci.yml`, feeding required `ci-ok`)
fails the PR when the wrapper's pin goes stale (the pinned commit's
reusable differs from the working tree's), so repin the wrapper in the
same PR as any reusable change — a stale pin blocks merge via `ci-ok`,
not via a side check. `gate-selftest` (same file) runs the gate's
committed regression suite (`.github/scripts/test-guard-selftest.sh`,
11 scenarios) on every PR for the same reason.

No secrets, no paid minutes beyond a ~30s ubuntu-latest run.

## Per-repo overrides (`.github/test-guard.json`)

```json
{
  "testGlobs": ["tests/**", "spec/**"],
  "mode": "warn",
  "markerLabel": "tests-by-test-author",
  "testAuthorIds": ["2c980cec-9df5-40a3-80c5-00b3b9448fbb"]
}
```

Unknown keys are ignored with a warning (typo safety).

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
# flip <repo> to enforce (after Test Author flow live + ~20 clean PRs).
# Branch + PR: protected branches reject direct pushes, and clobbering
# an existing config would drop that repo's overrides.
git clone <repo> && cd <repo> && git checkout -b ci/test-guard-enforce &&
  python3 -c "import json,os;p='.github/test-guard.json';d=json.load(open(p)) if os.path.exists(p) else {};d['mode']='enforce';open(p,'w').write(json.dumps(d,indent=2)+'\n')" &&
  git add .github/test-guard.json &&
  git commit -m "ci(test-guard): enforce Builder/Test-Author separation" &&
  git push -u origin ci/test-guard-enforce
# then open a PR; merge normally.
```

   The flip PR itself is judged by the old (warn) policy since the
   config is read from the base ref; enforce bites from the next PR on.

4. Only then, add an org-ruleset REQUIRED WORKFLOW pointing at
   `.github/workflows/test-guard-required.yml` in this repo —
   requiring the check NAME alone is not enough, since a PR-edited
   caller can satisfy a name check with weakened inputs. Never require
   the reusable (`test-guard.yml`) directly: rulesets need a
   pull_request/merge_group trigger, which only the wrapper has.
   Add the ruleset in Evaluate mode first, confirm the bare
   `test-guard` check appears green on 2+ PRs (proving the wrapper's
   pinned internal reference resolves under required-workflow
   execution), then switch to Active. If the check misfires there,
   repin the wrapper's internal `uses:` (it names the reusable at a
   pinned SHA for exactly this reason) rather than touching the
   reusable.

## Rollback

- Workflow: revert the caller PR (or the reusable-workflow PR); the gate
  is advisory in warn mode, so removal unblocks nothing that was blocked.
- Attribution hook: `git config --global --unset core.hooksPath` on the
  host + restore `~/.gitconfig` from
  `ops/worker-vps/git-hooks/backup-20261012T1645Z/` (UNDO in
  `ops/worker-vps/operator-actions.log`).
