# Contributing

Short org-wide contract. A repo may add its own `CONTRIBUTING.md` with stricter rules;
where they differ, the repo file wins.

## 1. Branch and PR shape

- Work on a branch, open a pull request, never push to `main`. Squash merges only.
- PR titles are Conventional Commits headers (`type(scope): summary`, max 100 chars,
  no trailing period). Types: feat, fix, perf, refactor, test, docs, build, ci, chore,
  revert, style, security.
- Use the PR template. The `pr-lint` check requires two sections, Linked Issues or
  Issue Description (feat/fix/perf/refactor/security) and Verification: a missing or
  empty one is a warning by default and an error once a repo sets
  `PR_STANDARDS_MODE: error`. What Changed and Notes for reviewers are optional.
- Done means merged. Never leave an orphan PR open: merge it, or close it with a
  comment naming what replaced it.

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
title that is not a Conventional Commits header. If you already pushed a branch with
an id in its name, rename it with
`gh api -X POST repos/<owner>/<repo>/branches/<old>/rename -f new_name=<new>`.
The PR follows the rename. Never rewrite merged history.

## 3. Honest disclosure

- Report the tests you actually ran and their results. Say what you did not run.
  Never claim a green run you did not see.

## 4. Reviews

- Address every review finding, or reply with why it does not apply.
- Review comes from the required review-app checks: `Devin Review` on every repo
  and `CodeRabbit` on public repos. Each reviews every push to the PR. No
  approving review is required, whatever the change type (see
  [PIPELINE-STANDARD.md](PIPELINE-STANDARD.md) §1).
- Fix findings on the same PR and reply on the finding's thread; do not open a
  new PR to get a fresh review.
- Ask for a security review when the diff touches auth, sessions, secrets,
  permissions, payments or public exposure.
