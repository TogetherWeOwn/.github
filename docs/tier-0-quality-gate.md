# Tier-0 deterministic quality gate

Why: expensive review models keep catching what deterministic checks could
catch first. Tier-0 runs those checks on every PR, report-only, before any
review model spends a token.

## Opt in (one caller job per repo, plus one aggregator entry)

1. Add a caller job to a workflow in your repo:

```yaml
jobs:
  tier-0:
    uses: TogetherWeOwn/.github/.github/workflows/tier-0-quality-gate.yml@<sha>
    with:
      mode: report-only
    permissions:
      contents: read
```

Pin `uses:` to the merge-commit SHA of the gate version you reviewed;
a moving tag never qualifies. Callers in this repo use the local path
`./.github/workflows/tier-0-quality-gate.yml` instead. Never put
`timeout-minutes` or `runs-on` on a caller `uses:` job — GitHub rejects
it; timeouts live inside the reusable workflow.

2. Feed the repo's required aggregator (the `ci-ok` equivalent): add the
caller job to its `needs:`. Wire exactly one context — `tier-0-ok` is the
gate's only aggregator, and in report-only mode it always passes.

## The six gates (all auto-detect stack, all run only what changed)

| # | Gate | Tools | Skips when |
|---|------|-------|-----------|
| 1 | lint + strict types | tsc --strict + typescript-eslint / PHPStan max / clippy -D warnings / ruff + mypy / go vet + gofmt | stack absent, or repo has no config yet (skip notes what to adopt) |
| 2 | unused code | knip / vulture on changed files / PHPStan max | no config / no changed files |
| 3 | generator drift | `.tier0-generators` commands, else universal lockfile checks (cargo `--locked`, `go mod tidy -diff`, composer validity; npm/pip freshness needs a declared generator — npm has no reliable dry check), then `git status` must be clean | never — always runs |
| 4 | workflow checks | actionlint + zizmor + structural rule (no `paths:` on gating workflows) | never — always runs |
| 5 | security | gitleaks on the PR diff + Semgrep org rules + squawk on changed migrations | squawk only: no changed `migrations/*.sql` |
| 6 | tests | repo `.tier0-tests` entrypoint: diff coverage >= threshold + budget-boxed mutation on changed lines | repo has no `.tier0-tests` yet |

Missing repo-owned configs are skips with adoption notes, never failures —
a gate that fails repos for tooling they never adopted would burn the
false-positive budget on day one.

## Rollout: report-only first, promote on measured evidence

- Every gate ships `mode: report-only`. Findings land in the step summary
  and the `tier-0-findings` artifact; the run stays green.
- A check becomes required (`mode: enforcing`, per-gate as the workflow
  grows per-gate inputs, or whole-gate) once its false-positive rate over
  the last 20 PRs is measured under 5%.
- Catch-rate bookkeeping: each finding carries its gate id, so the count
  of reviewer findings that Tier-0 would have caught is a query, not an
  argument. Every finding below is already labelled for that join.

## Repo-owned extension points (adopt to enable)

- `.tier0-generators` — one shell command per line (schema:check, OpenAPI
  codegen, lockfile refresh). Lines starting with `#` are comments.
- `.tier0-tests` — executable entrypoint receiving `TIER0_CHANGED_FILES`
  (newline-separated), `TIER0_COVERAGE_THRESHOLD`, `TIER0_MUTATION_BUDGET_MIN`,
  `TIER0_ENABLE_MUTATION`. Fails under threshold or on surviving mutants.
- `semgrep-rules/` — copy the templates, name the repo's helpers/sinks.

## Limits

- Job outputs cap the changed-file list: PRs touching thousands of files
  fall back to per-job diffing (each gate re-runs `git diff` itself).
- Mutation coverage starts when the first repo adopts `.tier0-tests`;
  until then Gate 6 reports its own absence.
