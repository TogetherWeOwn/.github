# PR contract and test-skip pilot

## Scope and status

Source implementation is not live enforcement. Only
`TogetherWeOwn/paperclip-plugin-sandbox-boat` is in the explicit enforcement
allow-list in `pr-standards-required.yml`. Other repositories, drafts,
dependabot/renovate authors and fork-head PRs get **warnings** for the new rules.
Existing internal-reference/title and Devin-reply gates are unchanged.

The required workflow is still pinned by the org ruleset to
`97fc0e24038021dfaa1fc9da754c6c5d6429130f`. Merging this source does not update
that setting. Rollout needs the reviewed merged SHA, a narrowly scoped operator
pin update, pilot-native conversation resolution and effective-bypass readback.
Do not change other checks, settings or bypass actors. Unknown inherited bypass
scope is unknown, not proof of enforcement. Expansion requires a separate review
after two weeks of measured live pilot operation without false-block complaints.

## Contract

Use the existing org template, not a second contract block:

- **Testing:** observed exact-head Actions runs/jobs and results, plus unrun checks.
- **Risk:** blast radius and failure modes.
- **How to revert:** code, data and external side effects, or why reversal is unsafe.
- **What these checks would miss:** gaps in the evidence and automation.

Each section needs non-placeholder evidence. `N/A — a concrete reason` is valid
for non-runtime changes; an empty or placeholder reason is not. Leading TODO/TBD
promises and "none yet" forms are placeholders, even with qualifying text; this
is a bounded syntax check, not a semantic judgment of arbitrary prose. Fenced command
output/results count, but empty fences, comments and headings alone do not.
Headings inside fences never open sections. Verification and Risks were folded
into Testing and Risk. The legacy pr-lint caller accepts Testing or Verification,
preserving substantive Verification when both headings exist. The Attestation
parser terminates at any next level-one/two heading, independent of those names;
a compatibility fixture
checks this without changing the parser or its rollout pins.

## Added-line skip lint

Only added lines in test files are judged; old/context/deleted markers do not
become errors. Test paths include test/spec directories, JS/TS `.test`/`.spec`
files, Python `test_*.py`/`*_test.py`, Go `*_test.go`, and Rust `.rs` modules
because Rust unit tests live inline.

| Marker | Accepted justification |
|---|---|
| `it/test/describe.skip(...)`, `xit(...)`, `xdescribe(...)` | `skip-reason: <text>` comment on the same or previous new-file line. A JS test name is not a skip justification. |
| JS/TS `it/test/describe.only`, including chained/tagged registrations | None; remove focus so the rest of the suite runs. Unrelated `.only` methods are not test focus. |
| `@pytest.mark.skip` / `skipif` | Non-empty literal `reason=`, or adjacent skip-reason comment. |
| `pytest.skip(...)`, `t.Skip(...)` | Non-empty literal message, or adjacent skip-reason comment. |
| `#[ignore]` | `= "reason"`, or adjacent skip-reason comment. |

The matcher follows the registration/adjacent-comment shape from
[T3 Code's skip lint](https://github.com/pingdotgg/t3code/blob/main/oxlint-plugin-t3code/rules/no-test-timeout-or-unreasoned-skip.ts),
not its timeout policy. No third-party code or parser dependency is installed.
String/comment examples are masked across context and added lines within each
visible new-file diff hunk, including Python triple-quoted strings. Ordinary
single/double quotes in JS/TS, Python and Go stop at unescaped newlines;
templates/triple quotes and Rust multiline strings retain multiline masking. Focus registrations may span up to 20 preceding visible lines,
with the diagnostic anchored to an added `only` token, never a context-only marker.
Static Python
raw/unicode text literals count as reasons; bytes, dynamic f-strings and positional
pytest mark reasons do not replace the required literal `reason=` or adjacent
comment. Multiline literal arguments use at most 20 following visible diff lines.
This is a bounded diff lint, not a language AST, a proof that the reason is true,
or a complete detector of dynamically constructed skips. Lexical openers outside
the visible hunk, aliases, computed registrations and pre-existing markers require
review.
Uninspectable changed test patches/file-list truncation fail closed in enforced
contexts; transient API failures need a rerun, never a privileged fallback.

Deleted test files/cases and potentially lowered numeric coverage/threshold
settings produce warning annotations and summary lines only. Replacement tests
can look like deletion, and unrelated numbers on a coverage line can look like a
reduction; neither advisory heuristic blocks merging.

## Advisory sensitive-path label

`sensitive-path-label.yml` is a reusable `workflow_call` plus `pull_request`
workflow. It has only `pull-requests: write`, no checkout, no PR-code execution,
and no `pull_request_target`. Add a small caller in the pilot repo **after this
source is merged**, pinning its full reviewed squash SHA:

```yaml
name: Sensitive paths (advisory)
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
permissions:
  pull-requests: write
jobs:
  label:
    uses: TogetherWeOwn/.github/.github/workflows/sensitive-path-label.yml@<reviewed-full-squash-sha>
    permissions:
      pull-requests: write
```

Matches cover workflows, review configuration/CODEOWNERS, rulesets, auth,
secret/token code, migrations/drizzle and deploy/install/release configuration.
Renames inspect both paths. Forks and non-pilots get annotations only. API/write
failure warns and retains independent review. Existing labels are never removed.
The label is not a required check, proof of safety, or a permission boundary.

## Review-thread interlock: native, not another validator

Use required conversation resolution in the pilot's existing repository ruleset
`26277675`; do not create a custom unresolved-thread checker or a second findings
ledger. A formatted answer, resolved conversation or green Devin status alone is
**not** independent acceptance of a blocking disposition. Independent Security/QA
must accept each blocking disposition in its original thread at the exact reviewed
head. CodeRabbit remains advisory; never wait for it to start or request a retry.

## Verification and live acceptance

`ci.yml` runs the exact workflow heredocs through `test_pr_standards*.py` under
required `pr-standards selftest` / `ci-ok`. Full suites run in Actions only. Local
syntax parsing is not test execution or CI evidence. Record exact-head Actions
run/job links, independent Security then QA review, and all finding dispositions
before delivery. No privileged PR-code execution, new credential or paid service
is required.

After reviewed source merge and operator pin/native-rule readback, use live pilot
PRs for these cases, keeping deliberate negatives as drafts until repaired:

| Case | Required observation |
|---|---|
| Complete contract, reasoned skip, sensitive path | Contract/skip pass; advisory label appears for same-repo pilot. |
| Each missing/empty/placeholder contract section | Warning on draft, error on a ready same-repo pilot; `N/A — reason` passes. |
| Each unreasoned skip and every `.only` | Warning on draft, error on ready pilot; reasoned forms pass (except focus). |
| Deleted tests / lowered coverage | Warnings plus summary only, never an error. |
| Fork/bot/non-pilot | No new errors; fork label becomes annotation, no write. |
| Unresolved blocking thread | Native merge interlock refuses. |
| Late-created or reopened unresolved thread | Native interlock still refuses, even after a previous green run. |
| Author self-resolves without independent acceptance | Independent QA refuses final acceptance; native resolution alone is insufficient. |
| Accepted disposition in original thread at reviewed head | Independent reviewer records acceptance; native resolution and all other gates can pass. |

Do not merge deliberate negative PRs or count source self-tests as live acceptance.
If draft behavior cannot demonstrate a ready-PR error, retain the draft warning
evidence and arrange a bounded safe live negative before claiming enforcement.
Only verified live acceptance starts the two-week measurement window. A source
merge or successful pin update alone does not start it. Preserve existing security,
review, Actions/Devin and production gates throughout.

Rollback is a reviewed source fix and separately authorized pin change; do not
weaken an active rule to clear a false positive without its normal authority.
The code creates no data migration or production deployment. Label effects are
advisory and additive; removal, if needed, is explicit rather than automatic.
