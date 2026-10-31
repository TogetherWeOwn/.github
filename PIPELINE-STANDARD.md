# Pipeline standard: one PR, one review, one merge, one deploy (every TogetherWeOwn repo)

Owner directive, 2026-10-10. Our own process was the biggest blocker. Each rule below removes a step that cost time,
CI minutes or model allowance without making anything safer. It sits alongside [CI-STANDARD.md](CI-STANDARD.md),
which governs what CI runs; this document covers the whole path from PR to production.

Every repo links this file from its CONTRIBUTING.md or README. New repos start from the
[workflow templates](workflow-templates/).

## 1. Review

- **Required checks come from the ruleset.** Every protect-main ruleset requires the repo's CI, `Devin Review`
  and, on public repos, `CodeRabbit`. No approving review is required. Read the live set with
  `gh api repos/<repo>/rules/branches/main`.
- **The Paperclip Review check is retired (2026-10-31).** No ruleset requires it. Do not wait for it, and do not
  ask anyone to run or waive it.
- **Answer every finding.** Fix each review finding, or reply on the PR with evidence of why it does not apply.
- **Batch fixes.** Push the fixes for one review round together. Every push re-runs CI and the reviews.
- Never fake or bypass a required check.

## 2. Merge

- **The agent that did the work merges it (owner decision 2026-10-31).** A PR merges with no owner approval once
  every required check is green and each finding is fixed or answered with evidence.
- **Production deploys need the owner.** If a merge to the default branch deploys production (today two-web-next
  and two-bot-next), the merge needs the owner's approval through `request_confirmation` on the card. So does
  every other production deploy, such as a manual dispatch or promotion.
- **No rebase churn.** Every repo's rulesets have `strict_required_status_checks_policy=false`, so a BEHIND PR
  merges as-is once its checks and review pass.
- **Rule:** never update-branch, rebase or merge main into a PR unless it is DIRTY (has conflicts). Each refresh
  pushes a new head and re-runs all CI and the reviews.
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

The caller keeps its own environment and exact-SHA gates and checks out `needs.<job>.outputs.sha`. Two rules:

- `staging-workflow` must be a workflow that ONLY deploys staging, for example a separate `deploy-staging.yml`.
  Never point it at the workflow that also runs the production deploy. A successful production run, or a
  dispatch where every job is skipped, would otherwise count as "staging-verified".
- Before deploying, the caller verifies that the commit is an ancestor of `origin/main`. A `sha` override must
  never ship an unmerged commit with production secrets.

Adopted in two-bot-next #755 and two-web-next #629. Never freeze main to deploy.

## 4. CI

- **Change-gated, per [CI-STANDARD.md](CI-STANDARD.md).** Nightly and heavy suites run on schedule and on push to
  main, never on every PR push. Superseded PR runs are cancelled early. Push-to-main, deploy and release runs are
  never cancelled.
- The CI rules, including "no nightly or scheduled-only suites on every pull request", live in
  [CI-STANDARD.md](CI-STANDARD.md) and are not repeated here.

## 5. Release

- **Applications use numeric CalVer:** stable `YYYY.MDD.P` (`vYYYY.MDD.P`), staging
  `YYYY.MDD.P-canary.N` (`canary/vYYYY.MDD.P-canary.N`). Month is not zero-padded;
  day is two digits. Examples: `2026.105.0`, `2026.303.0`, `2026.1028.0`.
- The [shared releaser](actions/calver/README.md) and its step adapter share one
  implementation. Callers pin both workflow and tooling to the same full commit
  SHA. Package/plugin/API compatibility versions remain SemVer.
- Counters come from complete repository tag history, not npm publications. This
  adopts upstream Paperclip's syntax, not its nightly/beta/three-day-soak policy.
  Existing staging, approval, artifact, smoke, takeover and rollback gates stay.
- A caller's transaction boundary spans plan, version stamping, deploy and publish.
  Planning is otherwise advisory. Publish the exact frozen version after the
  required successful deployment evidence; never relabel a stamped build after
  a collision. Promotion uses its attempt's UTC date and the same saved artifact.
- Only stables create Releases. Canary tags carry source run/attempt/artifact
  provenance. Historical repairs and Routeware build releases never regress Latest.
- TWO bot keeps its daily live-production digest, quiet-day/rollback checks and
  legacy repair/SBOM adapters outside the production workflow. Kagi keeps its
  release-selection and package/tag equality gate; it releases before deployment.
  Routeware keeps SemVer API tags and a separate `build/` CalVer namespace; its
  disabled production lane stays disabled.
- Do not globally enable immutable Releases: asynchronous bot SBOM uploads and
  repairs require mutable assets. Never move tags or freeze main for a release.

### 5a. No host release train

The old release-please/host-timer merge-freeze description is retired. Do not
create a host timer, cron, ruleset mutation or release train for this migration.
Use existing GitHub workflows and native task dependencies/monitors. GitHub
concurrency preserves a running transaction with `cancel-in-progress: false`, but
pending runs can replace older pending runs: it is not a FIFO deployment queue.

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
