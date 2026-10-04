# Contributing

Short org-wide contract. A repo may add its own `CONTRIBUTING.md` with stricter rules;
where they differ, the repo file wins.

## 1. Branch and PR shape

- Work on a branch, open a pull request, never push to `main`. Squash merges only.
- PR titles are Conventional Commits headers (`type(scope): summary`, max 100 chars,
  no trailing period). Types: feat, fix, perf, refactor, test, docs, build, ci, chore,
  revert, style, security.
- Fill in every section of the PR template. The `pr-lint` check reads them: a missing
  or empty section is a warning by default and an error once a repo sets
  `PR_STANDARDS_MODE: error`.
- Done means merged. Never leave an orphan PR open: merge it, or close it with a
  comment naming what replaced it.

## 2. Public-repo privacy

Most TogetherWeOwn repos are public. Keep internal ticket ids, instance links,
localhost and private-network addresses out of every title, body, commit message,
review comment, issue and branch name. Link public GitHub issues (`Fixes #123`)
or describe the problem in your own words. Name branches after the change
(`fix/sudo-window`), never after an internal card.

## 3. Honest disclosure

- Name the model used (provider plus exact model ID), or write
  "None — human-authored".
- Report the tests you actually ran and their results. Say what you did not run.
  Never claim a green run you did not see.

## 4. Reviews

- Address every review finding, or reply with why it does not apply.
- One review per PR; fixes after a changes-verdict stay on the same review thread.
- Docs, specs, test scripts, fixtures and CI-only changes need one reviewer pass.
- Ask for a security review when the diff touches auth, sessions, secrets,
  permissions, payments or public exposure.
