<!--
Title: Conventional Commits header, e.g. `fix(auth): refuse expired sudo sessions`.
Types: feat fix perf refactor test docs build ci chore revert style security. Max 100 chars, no trailing period.
Keep internal ticket ids, instance links, localhost and private-network addresses out of the title,
the body, commit messages and branch names. Name the branch after the change, for example
`fix/sudo-window`. Link only public GitHub issues (`Fixes #123`).
Write short, plain sentences. `pr-lint` requires the linked issue (feat/fix/perf/refactor/security)
and Verification; the other sections are optional context for the reviewer.
Full rules: CONTRIBUTING.md.
-->

## Linked Issues or Issue Description

<!--
  Required for feat, fix, perf, refactor and security. Pick ONE:
  (A) A public GitHub issue exists: `Fixes #123`, `Closes #123` or `Refs #123`.
  (B) No issue exists: describe the problem here in your own words (what happened, what you expected,
      how to reproduce it, or the motivation and proposed solution for a feature).
-->

-

## What Changed (optional)

<!-- One bullet per logical unit. The reviewer reads the diff; add only what the diff does not show. -->

## Verification

<!-- Commands you ran and their results, so a reviewer can repeat them. -->

-


## Notes for reviewers (optional)

<!-- Risks, follow-ups, or anything the reviewer should look at first. -->

## Checklist

### Self-review (10 buckets)

(Author: tick each line only after checking it on the diff. Run the Tier-0
gate first where the repo has adopted it, fix everything found, then mark
ready. A finding stands only when it names a concrete failing scenario.)

- [ ] 1 Untested branch — every changed behavior has a covering test. Asked:
      "which changed line would catch a regression?" Added the test, not just the fix.
- [ ] 2 Docs drift — every changed behavior is still described. Updated the
      doc with the code, in this PR.
- [ ] 3 CI logic — every job times out and the gating still holds. Proved
      with a CI run on this head, not prose. No `paths:` on gating workflows.
- [ ] 4 Generated drift — regenerated everything the source change requires
      (ran the generator; never hand-edited its output). Tree clean after.
- [ ] 5 Conflicts / stale — diffed against today's base (rebased first when
      the base moved), no markers left.
- [ ] 6 Unhandled errors — every new failure mode is handled or fails closed
      loudly. Asked: "what happens when this fails?"
- [ ] 7 Authz — no secret, credential, or permission widened. Least
      privilege; secrets stay in the store. Named who gains what.
- [ ] 8 Perf — every new wait, fetch, or scan is bounded
      (timeout/retry/limit) or measured on the biggest input.
- [ ] 9 Dead state — no TODOs, debug leftovers, or unreferenced code.
      Defended or deleted every line; history keeps it.
- [ ] 10 Spec mismatch — title, body, and commits describe the diff exactly.
      Fixed the code or fixed the claim — never weakened a test to fit.
