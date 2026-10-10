# Pipeline standard: one PR, one review, one merge, one deploy (every TogetherWeOwn repo)

Owner directive, 2026-10-10. Our own process was the biggest blocker. Each rule below removes a step that cost time,
CI minutes or model allowance without making anything safer. It sits alongside [CI-STANDARD.md](CI-STANDARD.md),
which governs what CI runs; this document covers the whole path from PR to production.

Every repo links this file from its CONTRIBUTING.md or README. New repos start from the
[workflow templates](workflow-templates/).

## 1. Review

- **Paperclip Review runs once per CI-green, non-draft head.** The host review gate (`review-gate.timer`, every
  5 min) finds open non-draft PRs whose head has every non-review check green and no review yet. It then requests
  exactly ONE Paperclip Review for that head, and it does this for every author, host-authored PRs included. To get
  reviewed: make CI green and keep the PR non-draft. Nothing else is needed.
- **No automatic review on `opened`, `synchronize` or `reopened`, and none on drafts.** Those reviews were mostly
  superseded by the next push before they finished. Marking a draft "ready for review" still triggers one.
- **First review on Opus; re-reviews on Sonnet.** Once a PR's review card holds a completed assessment, later
  reviews of that PR run on Sonnet 5.5 (`review_model_router`).
- **Reviewer concurrency follows Claude's pace.** The cap is whatever the remaining weekly Claude allowance
  sustains until reset (`apply_caps.py`), with a minimum of 2. Reviews queue rather than burn the week early.
- **Authors:** ask for a re-review with `@togetherweown-reviewer review this PR` only once CI is green on the
  new head. Do not push while a review is running unless the push fixes that review's findings, because every
  push supersedes the running review.
- The required check is `Paperclip Review` 5/5 on the exact head. Never fake it, and never bypass it.

## 2. Merge

- **No rebase churn.** Every repo's rulesets have `strict_required_status_checks_policy=false`, so a BEHIND PR
  merges as-is once its checks and review pass.
- **Rule:** never update-branch, rebase or merge main into a PR unless it is DIRTY (has conflicts). Each refresh
  pushes a new head, re-runs all CI and supersedes that head's Paperclip Review.
- Host `pr_automerge.sh` enables `--auto --squash` and no longer runs update-branch, except for repos that are
  strict.

### 2a. Merge queue (not enabled)

The merge queue is off, because non-strict checks already remove the churn it would fix. If a repo enables it
later, every workflow that provides a required check must add `merge_group:`; the starter `ci.yml` template
already does.

## 3. Deploy

- **Production promotes the last green staging SHA.** The production deploy takes the commit of the most recent
  successful staging deploy on main. It no longer demands the newest main SHA, which main churn kept cancelling,
  and nobody freezes main to deploy.

### 3a. Promote the last staging-verified commit

The reusable workflow `.github/workflows/promote-staging.yml` in this repo has these inputs and output:

| | Name | Meaning |
|---|---|---|
| Input (required) | `staging-workflow` | The repo's staging workflow file, e.g. `deploy-staging.yml` |
| Input (optional) | `sha` | Empty means the latest successful run of that workflow on main |
| Output | `sha` | The commit to deploy |

The caller keeps its own environment and exact-SHA gates and checks out `needs.<job>.outputs.sha`.

Adopted in two-bot-next #755 and two-web-next #629. Never freeze main to deploy.

## 4. CI

- **Change-gated, per [CI-STANDARD.md](CI-STANDARD.md).** Nightly and heavy suites run on schedule and on push to
  main, never on every PR push. Superseded PR runs are cancelled early. Push-to-main, deploy and release runs are
  never cancelled.
- (Details: section 4a, appended by the CI change-gating rollout.)

## 5. Release

- **The release PR must never need a merge freeze.** It is regenerated on every push to main, so it is always
  current. It merges through the same gates as any other PR: deterministic release tests, required checks, and
  Paperclip Review 5/5 via the review gate. The host approves the `pull_request` workflow runs on
  `release-please--*` branches automatically (they come from `github-actions[bot]`).
### 5a. Release train (host-automated, two-bot-next and two-web-next)

When a `release-please--*` PR is open, the host `release-train.timer` runs the merge freeze those repos' own
`docs/releases.md` describes:

1. It adds a temporary ruleset `release-train-freeze (host, auto)` that blocks updates to main for everyone except
   the host App.
2. It dispatches `release.yml` on main, which regenerates the release PR against the frozen tip.
3. The review gate requests one Paperclip Review once CI is green.
4. Once the head's parent is main, every check is green and the review is 5/5, the train merges with
   `--match-head-commit` and lifts the freeze.

A freeze lifts itself after 60 minutes at most, and a failed train waits 6 hours before retrying. Push-to-main
release runs (tag and publish) are unchanged. Agents never freeze main by hand.

## 6. Self-blocking anti-patterns (each measured on 2026-10-10)

| Anti-pattern | Evidence (24h, two-bot-next + two-web-next) | Fix |
|---|---|---|
| Review on every push and on drafts | 1,081 reviews for 180 PRs (about 6 per PR); **36% superseded**, 17% incomplete or errored, 20% ended 5/5; 28% of reviews were on drafts | §1 review gate, no draft reviews |
| CI churn | **About 220 cancelled runs per day**: each push restarts suites and BEHIND PRs are rebased onto every merge | §2 merge queue, §4 cancel-superseded |
| Nightly suites on PRs | two-bot-next `nightly` ran **168 times** on PRs; `supply-chain` 259 times | §4 schedule/change-gate |
| Exact-newest-SHA production gate vs main churn | `deploy-staging` superseded/cancelled 8 times; main **frozen twice** in one day to deploy | §3 promote last green staging |
| Release PR racing main | release PR regenerated 5+ times; manual merge freeze | §5 |
| Automation fighting itself | idle-waker woke review cards that could never publish (102/117 wakes); the stuck-run reaper parked review cards (mentions silently skipped); auto-merge merged unreviewed PRs into a non-default base | Host automation runbook (`ops/productivity/PIPELINE-HOST-AUTOMATION.md` on the operator host) |
| Server-side drops | about 50% of GitHub webhooks rejected (`credentials` lease contention); a duplicate review row overwrote a 5/5 check | Paperclip fork tog.6/tog.7 |
