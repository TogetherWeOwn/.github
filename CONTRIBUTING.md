# Contributing

Short org-wide contract. A repo may add its own `CONTRIBUTING.md` with stricter rules;
where they differ, the repo file wins. Automated contributors follow the same contract.

## 1. Branch and PR shape

- Work on a branch, open a pull request, never push to `main`. Squash merges only.
- PR titles are Conventional Commits headers (`type(scope): summary`, max 100 chars,
  no trailing period). Types: feat, fix, perf, refactor, test, docs, build, ci, chore,
  revert, style, security.
- Use the PR template. The `pr-lint` check requires two sections, Linked Issues or
  Issue Description (feat/fix/perf/refactor/security) and Testing (legacy
  Verification is also accepted): a missing or empty one is a warning by default
  and an error once a repo sets `PR_STANDARDS_MODE: error`.
- The org template also asks for Risk, How to revert (including data/external
  effects or why a revert is unsafe), and What these checks would miss. The
  [contract/skip pilot](PR-CONTRACT-PILOT.md) enforces those four sections only
  on its explicit pilot repository; other repositories receive warnings only.
  `N/A — <reason>` is valid, but an empty or untouched template is not evidence.
- Done means merged. Never leave an orphan PR open: merge it, or close it with a
  comment naming what replaced it.
- Search open and closed issues and PRs before starting; link duplicates and related PRs instead of opening a parallel one.
- If no public issue exists, describe the problem in the PR body (problem, expected vs actual, reproduction) so a reviewer never has to leave the PR.

## 2. No internal references, in any repo

This applies to every TogetherWeOwn repo, public or private. It is the same rule as
paperclipai/paperclip's CONTRIBUTING ("No Internal Issue References", "Branch Naming").

- **What to keep out:** internal tracker ids (`TWO-123`, `TOG-…`, `PAP-…`), Paperclip
  instance links, internal hostnames, private-network addresses, emails, agent names
  and secrets.
- **Where:** titles, bodies, branch names, commit messages, review comments, issues,
  release notes and tags.
- **What to do instead:** link public GitHub issues (`Fixes #123`) or describe the
  problem in your own words. Link the PR to the tracking card from the tracker side,
  never from GitHub.
- **Branch names:** name branches after the change (`fix/sudo-window`), never after a
  card (`two-123-…`).

The org ruleset requires the `pr-standards` check
(`.github/workflows/pr-standards-required.yml`) on every repo. It fails a PR with an
internal reference in its title, body, branch name or a commit subject, and a PR
title that is not a Conventional Commits header. Editing the title or body does not
re-run the check under the ruleset: push a commit or close and reopen the PR after
the fix. If you already pushed a branch with
an id in its name, rename it with
`gh api -X POST repos/<owner>/<repo>/branches/<old>/rename -f new_name=<new>`.
The PR follows the rename. Never rewrite merged history.

## 3. Honest disclosure

- Report the tests you actually ran and their results. Say what you did not run.
  Never claim a green run you did not see.

## 4. Reviews

- Address every review finding, or reply with why it does not apply.
- Reply in the finding's own thread with a first line of exactly one of:
  `Fixed in <commit sha>` (then one line on what changed),
  `Rejected: <proof>` (a `file:line` at the reviewed head, a test name with
  a run link, or a doc link showing the finding is wrong), or
  `Duplicate of <link>`. Any other opener (`Fixed.`, `Confirmed — …`,
  `✅ Resolved`) does not count, and neither does resolving the
  conversation. The required `pr-standards` check lists every Devin Review,
  Sourcery and CodeRabbit thread without such a reply and fails the PR; it warns instead on drafts,
  dependabot/renovate and fork-head PRs, and PRs opened before 2026-11-03.
- Batch each review round: commit the fixes, post the replies citing that
  commit's sha, then push once. The check reads the threads when it runs,
  so replies posted after the last push are only seen at the next run —
  push a commit or close and reopen the PR to re-run it.
- The target merge gate is CI, `Devin Review` pinned to App 811515 and CodeQL where `codeql=required`; verify the effective rules and rollout state per [STANDARD.md](STANDARD.md#1-governance-and-required-gates). Zero required GitHub approving reviews does not waive independent task/security review. **CodeRabbit is advisory**: answer each finding that arrives, but do not wait for a review to start or post commands to trigger it. The final-stage reviewer merges; the author merges only when no review stage exists. Production deploys and merges with an automatic production path need exact-head Release Manager acceptance (STANDARD.md §2), not owner merge approval.
- Fix findings on the same PR and reply on the finding's thread; do not open a
  new PR to get a fresh review.
- Ask for a security review when the diff touches auth, sessions, secrets,
  permissions, payments or public exposure.

## 5. Attestation (in the PR template)

The Attestation block in the PR template is mandatory and machine-checked.
Fill every field; retain the exact heading/field/bullet syntax.

T0 = critical paths, T1 = standard changes, T2 = docs-only. Declaring a higher risk is OK.
T0 directories at any depth: migrations, sql, db, database, drizzle.
T0 directory/module words: auth, authn, authz, authentication, authorization, oauth,
session, sessions, billing, payment, payments, permission, permissions,
gateway, voice, crypto, vault, secret, secrets,
backup, backups, dump, dumps, grant, grants, role, roles, erasure, ingest.
Words are case-insensitive and split at punctuation, snake_case, kebab-case, and CamelCase
boundaries (auth.rs, gateway_backoff.rs, member-erasure.ts, SessionController.php).
The words also match directory names/descendants. Prose filenames ending .md/.rst/.txt
are not modules, but still inherit any critical directory floor. Substrings such as
names in authors.py, voices/, and roleplay.ts do not match those critical words.
Also T0: .github/workflows/deploy*, crates/cutover/**, and wrangler .toml/.json/.jsonc
configs at any depth (including wrangler.production.toml); prose like docs/wrangler.md
is not a config. Deploy scripts outside these paths still require a declared T0 when critical.
Renames inspect both old and new paths; deleting a T0 file is still T0.
T2 requires only root or docs/ .md/.rst files or explicitly allowlisted text basenames:
README.txt, LICENSE.txt, NOTICE.txt, CHANGELOG.txt, CONTRIBUTING.txt, CODE_OF_CONDUCT.txt,
SECURITY.txt, SUPPORT.txt, AUTHORS.txt (case-insensitive). Critical directory floors win.
Never T2: .github/ files, code, arbitrary .txt, CMakeLists.txt, or requirements/constraints
manifests (including requirements.txt, requirements-dev.txt, and docs/requirements.txt).
Path names are only a risk floor, not proof that other code is non-critical; declare T0
for critical behavior even when its path does not match. Ingest is conservatively T0
because filenames cannot prove whether a body route requires authentication.

Claims: one behavior claim per C-number. For docs, state the documentation-only claim.
Tests: one line per claim/test mapping; repository-relative path, optional ::test_name.
Every claimed test file must be added/modified/renamed in this PR, not removed or merely present
elsewhere in the repository. Inline tests in source files are allowed. Existing unchanged tests
belong in Verification, not this field. Duplicate mapping lines for multiple tests/claims.
failed_before=yes|no|not-run describes what you actually observed. No invented red-before proof.
proof= names the command/result/mutant or other evidence; never executes from this text.
Only T2 may replace all test lines with: - none | reason=Documentation-only; no behavior changed.
The validator checks shape and changed-file references, NOT test execution or truth of claims.

High-risk checklist: keep D1-D8. Replace each placeholder with evidence or
not-applicable plus a concrete reason. For T0 all boxes must be checked; never blindly check them.
Use plain one-line text (no | delimiter inside test fields). Keep attestation unfenced and visible.

The D-items are:

- D1: secret sinks, redaction and log/artifact exposure
- D2: authentication, authorization and cross-principal/oracle cases
- D3: migrations, grants, restore, erasure and state integrity
- D4: input bounds, query limits, retries and resource caps
- D5: concurrency, lost writes, replay and single-use effects
- D6: pinned dependencies/actions and test/guard wiring
- D7: failure propagation, non-empty evidence and honest verdicts
- D8: malformed shapes, Unicode, parsing and host validation

## 6. Releases

Releases follow [STANDARD.md](STANDARD.md#5-release): applications use
numeric CalVer, packages/plugins/APIs use SemVer, and only stables create Releases.
Release notes are grouped by each repo's `.github/release.yml` categories
(Security, Features, Fixes, Documentation, Other changes).

## 7. Security

Report vulnerabilities privately per [SECURITY.md](SECURITY.md); never open a
public issue for one. Security findings about our own deployments are tracked
internally, never as public issues.
