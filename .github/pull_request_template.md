<!--
Title: Conventional Commits header, e.g. `fix(auth): refuse expired sudo sessions`.
Types: feat fix perf refactor test docs build ci chore revert style security. Max 100 chars, no trailing period.
Keep internal ticket ids, instance links, localhost and private-network addresses out of titles,
bodies, commits and branches. Link only public GitHub issues (`Fixes #123`).
Use short, plain sentences. Full contribution rules: CONTRIBUTING.md.
-->

## Thinking Path

<!-- Briefly explain the approach and alternatives considered. -->

## Linked Issues or Issue Description

<!-- Required for feat/fix/perf/refactor/security: link a public issue (Fixes/Closes/Refs #123)
or describe the problem and motivation here. No internal tracker identifiers. -->

-

## What Changed

<!-- One bullet per logical unit; avoid repeating the diff. -->

-

## Verification

<!-- Exact commands and results; distinguish executed tests from planned checks. -->

-

## Attestation

<!-- SHADOW / WARN only. Fill every field; retain the exact heading/field/bullet syntax.
T0 = critical paths, T1 = standard changes, T2 = docs-only. Declaring a higher risk is OK.
T0 paths: migrations/**, sql/**, **/auth/**, **/session*, billing/**,
.github/workflows/deploy*, wrangler*, crates/cutover/**, **/voice/**, crypto/vault.
**/ includes the root; session* includes descendants; crypto/vault includes descendants.
Renames inspect both old and new paths; deleting a T0 file is still T0.
T2 currently means only root or docs/ .md/.rst/.txt files, never .github/ files or code.

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
-->

risk_tier: <T0|T1|T2>

### Claims

- C1: <behavior guaranteed by this change>

### Tests

- C1 | test=<path/to/test_file>::<test_name> | failed_before=not-run | proof=<command and observed result>

### High-risk checklist

- [ ] D1: <secret sinks, redaction and log/artifact exposure>
- [ ] D2: <authentication, authorization and cross-principal/oracle cases>
- [ ] D3: <migrations, grants, restore, erasure and state integrity>
- [ ] D4: <input bounds, query limits, retries and resource caps>
- [ ] D5: <concurrency, lost writes, replay and single-use effects>
- [ ] D6: <pinned dependencies/actions and test/guard wiring>
- [ ] D7: <failure propagation, non-empty evidence and honest verdicts>
- [ ] D8: <malformed shapes, Unicode, parsing and host validation>

## Risks

<!-- Blast radius, rollback and follow-ups. -->

## Model Used

<!-- Identify assistance used, if any. -->

## Checklist

- [ ] Verification commands and results are recorded.
- [ ] Attestation describes actual evidence, including any tests not run before the change.
- [ ] No secrets or private/internal identifiers are included.
