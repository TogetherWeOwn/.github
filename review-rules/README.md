# Changed-file review rules (shadow)

`review-rules` adds **advisory** defect signals, not a replacement for review,
acceptance tests, or existing required checks. All initial callers use `mode:
warn`. No rule is promoted in `promoted-rules.json` and no ruleset is changed.

## Call the reusable workflow

Use `.github/workflows/review-rules.yml` at a reviewed **full commit SHA**.
Inputs:

| Input | Default | Meaning |
|---|---|---|
| `packs` | `rust,ts,python-shell-ci,php,security` | Comma-separated central packs; unknown/empty selections report incomplete coverage. |
| `mode` | `warn` | `warn` always returns success for handled errors/findings. `enforce` fails incomplete coverage and explicitly promoted, non-INFO findings only. |
| `runs-on-json` | empty | Optional runner-label JSON. Empty selects `ubuntu-latest` for every repo. |

The `review-rules-required.yml` wrapper demonstrates `pull_request` and
`merge_group`, with a separate bare `review-rules` check. Its name is a future
integration point, **not authorization to add a required check now**. The wrapper
remains green in shadow even when the called workflow fails; it reports that
coverage was lost. Platform cancellation, unavailable runners, or a workflow
that cannot be scheduled cannot be made successful by application code.

### Immutable trust chain

The reusable workflow checks out the central engine at a literal commit, not
`github.sha`, the caller branch, a tag, or the target repo's config. The wrapper
pins the reusable-workflow commit. Consumers initially pin this PR's full head
SHA and must repin to its squash-merge SHA after central merge. The embedded
engine commit deliberately remains immutable; updating the engine requires a
separate reviewed asset commit and workflow pin update. Retain the referenced
commits while consumers use them.

A PR-editable caller is **not a security boundary**. Future enforcement requires
trusted workflow/ruleset wiring, reviewed promotion evidence, and an explicit
rollout decision. Flipping `mode` alone does not promote the heuristic packs.

## What is checked

All Semgrep rules have `id`, severity, a defect-class message and corpus citations
in `metadata.examples`. Script-rule citations are in `scripts/checks.py:EXAMPLES`.
A citation records the motivating review case, not independent proof that this
rule correctly detects every variant. The paired synthetic fixtures are supplemented
by `tests/corpus-cases.json`: exact source excerpts pinned to repository, commit,
path and line range, plus repaired negatives. Corpus positives assert exact finding
lines, not merely another match elsewhere in the file.

| Pack | Static signals (55 rules total) |
|---|---|
| `rust` (10) | Error-source erasure/metric omissions, buffer-before-cap, substring counting, byte/Unicode decoding, lane timeouts, SQL text-pinned authorization tests, delimiter identity keys, repeated percent encoding, localhost prefix allowlists. |
| `ts` (22) | Secret environment reads; case/grammar agreement; anchored test pins; multiline scanning; swallowed errors; split/duplicate-query/UTF-16 slicing; Markdown escapes; flash deletion; browser secret headers/redirects; redemption on mount; merge deletion; numeric/time budgets; ingress caps; log/accounting evidence truncation. |
| `python-shell-ci` (7) | Curl-to-shell, pip hash locks, secrets in argv, weak SHA validators, URL-like selectors, failure-to-skip, narrow external-JSON exception handling. |
| `php` (10) | Validated numeric casts, migration nits (INFO), JSON predicate/index evidence, read-then-write, claim/enqueue, token consumption predicates, lineage triggers, grant hydration and plaintext bearer lookup. SQL fixtures use SQL files, not invalid TypeScript. |
| `security` (6) | Broad prose exemptions, plaintext Rust Debug secrets, first-only redaction, console secret sinks, body caching, branch-only trust decisions. |

Standard-library checks add action/container/download pins, artifact scrub guards,
suppression provenance, committed operator switches, provisioning/cancel cleanup,
API pagination, visible scanner work, head-SHA CLI binding, test inventory,
secret-denylist parity, tar flavor hints, route-ancestor parent guards, flash
consumption, dump version/rollback-probe coordination, SQL list limits, duplicate
codec hints, migration namespaces against the default branch, store parity-case
hints, and actual two-parent merge modify/delete history.

`merge-modify-delete` compares both parents to their common ancestor and reports
only a path modified on one side, deleted on the other and absent in the result.
It does not prohibit ordinary file deletion. Octopus merges report incomplete
coverage instead of being silently accepted.

### Deliberate limitations

- Static signals are **not behavioral proof**. Authz/CSRF/oracle matrices,
  cancellation/RAII/concurrency/replay tests, backup/codec round trips, repeated
  redaction cases, erasure/grant inventories, browser credential redirect tests,
  query EXPLAIN plans, validator/matcher equivalence, verdict truth tables and
  backend parity still need executable repository tests and human review.
- Lexical YAML/shell checks do not parse arbitrary YAML anchors, custom shells,
  templated commands or dynamic action/image refs. `run` extraction supports
  block-style steps; it treats the extracted commands as Bash. Flow-style run
  steps and non-Bash programs are not claimed as complete shell coverage. Parser
  errors are reported, not converted to clean results.
- Svelte extraction covers `<script>` bodies with original line positions, not
  template semantics, preprocessor output, or the full compiler grammar.
- Route guards/test inventory/secret parity are repository-aware lexical hints,
  not a TypeScript call graph, test-collection proof or runtime-secret inventory.
  A regex/AST match or nearby checksum string is not evidence of secure behavior.
- Changed files are selected from the event's merge base to the evaluated head.
  Unchanged files can be read for inventory context but are not Semgrep targets.
  This is not a whole-repository vulnerability scan. Renames are scanned as
  delete/add. Missing refs never fall back to an empty/clean diff.
- Files over 2 MiB, a changed-source budget over 64 MiB, invalid UTF-8, symlinks,
  submodules, source-extraction collisions, scanner timeouts and parse errors
  report incomplete coverage. Binary blobs are not static-source targets.
- No precision/recall or <=5% false-alarm claim is made. The fixtures prove the
  stated examples and important mutants, **not production false-positive rate**.
  Nits remain INFO/nonblocking. Promotion requires measured error cost and
  independent review of concrete failure/line/proof/blast radius.

## Isolation and receipts

The engine reads committed Git blobs without executing target code, loading
PR-controlled Python modules, or trusting target Semgrep config/ignore files.
`python3 -I` runs the trusted standard-library engine. Semgrep receives only
changed source and central rules, no credentials, `.git`, host Docker socket or
network. Its Docker image is digest-pinned with read-only mounts/filesystem,
nonroot runner UID, dropped capabilities and bounded CPU/memory/PIDs/time.
`nosemgrep` suppression is disabled. All PR files named `.semgrepignore` are
excluded at every directory depth; only the trusted root ignore file is staged.
The scanner's returned paths must cover the eligible staged-target manifest,
including extracted Svelte/workflow scripts. Any missing target makes the receipt
incomplete even when other files scanned successfully. Timeout cleanup removes
only the uniquely named scanner created by that invocation.

`review-rules.json` records `rule_id`, `file`, `line`, `severity`, `message`,
coverage/errors, evaluated head/base and target counts. It never includes source
snippets or scanner metavariable captures. The job summary is Markdown; warn mode
uses warning annotations, capped at 50 findings, while JSON retains all findings.
Artifacts expire after seven days. Infrastructure failure produces an incomplete
receipt where the runner can still execute the fallback, not a false clean bill.

## Development and verification

From the repository root:

```sh
python3 -I review-rules/tests/selftest.py
python3 -I review-rules/tests/selftest.py --semgrep
```

The second command is the equivalent of `semgrep --test`: all shipped IDs must
have positive and negative entries in `tests/cases.json`; every fixture must be
scanned; expectations are matched by exact rule ID and path; parser errors fail.
It also requires Node.js 22+ to syntax-check the TypeScript fixtures.
`node --check` does not strip types, so that check applies only to fixtures
authored as plain `.ts` files and only proves they stay in the
JavaScript-syntax subset (no TypeScript-only syntax); it is not a TypeScript
parse. Corpus excerpts stored as `*.ts.fixture` may use full TypeScript and
are parsed by Semgrep instead.
The same digest and isolation options as production are used. The Semgrep tier
also runs real PR and merge-group event scans with nested malicious ignores and
an independently scanned sibling; the vulnerable target must still be detected.
Unit tests pair positive/negative script cases and exercise real Git conflict-
resolution history, source isolation, partial target omissions, extraction
manifests, event refs, warn/enforce outcomes and timeout cleanup.

The non-required `review-rules-tests` CI job also runs `semgrep --validate` and
SHA/digest-pinned actionlint. Semgrep 1.128.1 validation downloads its own rule-lint
registry, so **validation only** needs network. The actual scan is offline.

To change a rule, edit its YAML and both fixtures, run both tests and config
validation, and preserve its corpus citations. Add a regression fixture for each
verified miss/false alarm. Never silence scanner errors to make the suite pass.

Rollback: revert a caller PR (or repin to a previously reviewed central SHA).
This removes only the shadow report; existing CI/review/deploy gates are untouched.
