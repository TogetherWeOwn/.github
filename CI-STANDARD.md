# CI standard: only run what the change affects (permanent, every TogetherWeOwn repo)

Owner directive 2026-10-04: the "only if this code
changed" rule is on for all CI in all repos, old and new, forever. It saves
time, money and resources.

## Rules

1. **Change detection job.** Every workflow with jobs heavier than ~2 min
   starts with a `changes` job that diffs the PR against its merge base with
   native `git diff --name-only --no-renames -z <merge-base> <head>` (no
   unpinned third-party action) and outputs one boolean per area (e.g. `web`,
   `bot`, `docs`). Always pass `--no-renames`: with rename detection on, a
   moved file lists only its destination, so `git mv CHANGELOG.md
   docs/CHANGELOG.md` looks docs-only and `git mv src/x.ts docs/x.ts` hides a
   deleted code file. The flag makes a rename a delete plus an add, so both
   paths reach the filter. `-z` emits lossless NUL-separated paths rather
   than quoted lines; parse bytes without stripping whitespace. A failed
   fetch, merge-base or diff fails the detector, never reports an empty
   successful change. Run embedded Python with `python3 -I -` so checked-out
   modules and `PYTHONPATH` cannot replace stdlib imports ([isolated mode](https://docs.python.org/3/using/cmdline.html#cmdoption-I)).
   Docs-only requires EVERY changed path to match a docs area, with no non-doc
   area hit. Unclassified paths conservatively force `full_run=true`; a false
   `docs_only` flag alone does not select a heavy job. Sources:
   [git diff](https://git-scm.com/docs/git-diff),
   [fnmatchcase](https://docs.python.org/3/library/fnmatch.html).
   Use the reusable
   `changes.yml` in this repo: inputs are a filters map of area to globs plus
   the full-run globs; outputs are per-area booleans plus `full_run` and
   `docs_only`.
2. **Job-level gating.** Heavy jobs run only if their area changed:
   `if: needs.changes.outputs.<area> == 'true'` (via
   `fromJSON(needs.changes.outputs.areas).<area>`), or when
   `needs.changes.outputs.full_run == 'true'`.
   NEVER put workflow-level `on: pull_request: paths:` on a workflow that
   produces a required check. GitHub then never reports the check, and the PR
   hangs on "Expected — waiting" forever.
3. **One required aggregator.** Each repo has one required check, `ci-ok`
   (`if: always()`, `needs:` every gated job), which passes when every needed
   job succeeded or was skipped. Branch protection requires only `ci-ok`
   (plus pr-lint, secret scan), so gating can never block merges.
4. **Full-run triggers.** Changes to dependency manifests or lockfiles,
   `.github/**`, shared/common code, build/tooling config, or the
   change-detection filter itself make EVERYTHING run. Include root AND
   nested manifests/locks for each supported stack: `package.json`,
   `Cargo.toml`, `go.mod`, `pyproject.toml`, `requirements*.txt`, `Gemfile`,
   `composer.json` and their lockfiles. fnmatch is not recursive globstar:
   `**/package.json` alone does not match root `package.json`. Include shared
   workspace/compiler inputs such as `pnpm-workspace.yaml`, `go.work`,
   `go.work.sum`, `tsconfig*.json` and `.cargo/**`, even under a code area.
   Keep the reference example and starter filters aligned. Any path outside
   the supplied area map forces a full run, including unknown deleted sources;
   extend the map only when a narrower selection is demonstrably safe.
5. **Safety net.** `push` to `main` plus a nightly `schedule` run the FULL
   suite, including mutation/long gates. A regression that slips past a PR is
   caught within hours.
6. **Always run (cheap or security).** PR title lint, secret scanning, the
   `ci-ok` aggregator. CodeQL/supply-chain scans run when code or
   dependencies change, plus weekly on schedule.
7. **Exempt.** Release/deploy workflows (their own trigger events) and
   upstream forks (`paperclip`, `CLIProxyAPI`, `hindsight`, `OmniRoute`,
   `acpx`), whose CI must match upstream.
8. **Every new repo** starts from the org template with this already wired in
   (`workflow-templates/starter-ci.yml`).
9. **Concurrency and timeouts.** PR runs cancel superseded runs for the same
   ref (`concurrency: group: pr-${{ github.event.pull_request.number }},
   cancel-in-progress: true` on pull_request only; never on `main`,
   release, deploy or merge-queue flows). Give non-PR events a unique
   `github.run_id` group: a shared group cancels older PENDING runs even with
   `cancel-in-progress: false` ([GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)).
   Every job sets `timeout-minutes` (~2-3x its p95).
10. **Drafts skip heavy jobs (PR events only).** The draft check must never
    gate non-PR events (push/schedule/merge_group/dispatch have no
    `pull_request` object, so a bare `draft == false` first conjunct would
    skip the full-suite-on-main safety net). Gate force/full-run first and
    apply the draft check only to `pull_request` events, e.g.
    `if: <force> || <full_run> || (<area> && (github.event_name !=
    'pull_request' || github.event.pull_request.draft == false))`.
    PR triggers include `types: [opened, synchronize, reopened,
    ready_for_review]`. `ci-ok` (`if: always()`) still reports so the check
    never hangs.
11. **Merge-queue tier (optional).** Light checks run on PR; heavy suites may
    additionally run on `merge_group` once merge queues are enabled. `ci-ok`
    aggregates both events.
12. **Reusable stack workflows.** Org `.github` holds one reusable workflow /
    composite action per stack (node, rust, php, python, e2e, security).
    Matrix shards set `fail-fast: false`.
13. **Caches and single runs.** `setup-node` uses `cache:`; Rust uses
    `Swatinem/rust-cache` (hosted) or sccache (self-hosted); buildx uses
    `cache-from/to: type=gha`. Never run full `push` + `pull_request` builds
    on the same SHA (gate push to `main` only or skip duplicates); pass
    artifacts between jobs instead of rebuilding.
14. **Affected tests inside a repo.** Prefer changed-aware runners
    (`vitest --changed` / `--related`, `cargo -p` for affected crates,
    `pytest --testmon`); full suite still runs on `main` plus nightly.
15. **Shard long suites.** Shard Playwright (`--shard`), `cargo nextest
    --partition` or equivalent, then merge the reports into one artifact.
16. **Flakes are never silent green.** CI-only retries are bounded (1-2),
    logged as FLAKY, and paired with a quarantine job that files or links a
    ticket. No retry may turn a real failure green without a trace.
17. **Mutation: incremental on PR, full on schedule.** PRs run incremental /
    diff-based mutation (e.g. Stryker `--incremental`) as informational;
    the full sweep runs nightly/weekly, informational.
18. **E2E: smoke on PR, full on schedule.** PRs run the smoke path only; the
    full E2E runs nightly plus pre-release against staging.
19. **Security cadence.** Secrets scan on every run; CodeQL on PR for changed
    languages plus weekly; dependency/SBOM scans on lockfile change plus
    weekly.
20. **Governance.** Track minutes per PR, queue time and lead time; alert at
    ~75% of included minutes; set per-repo budgets so one repo cannot starve
    shared hosted concurrency (seen 2026-10-04: an ops-tooling burst queued
    TWO Next ~25 min). Clean up stale PRs and old runs.
21. **Tier 0 before LLM review (2026-10-10).** Deterministic checks catch what
    a linter can, so the Paperclip Review spends its rounds on judgment. Every
    repo runs, inside `ci-ok`: actionlint (checksum-pinned) and zizmor on
    workflows; gitleaks; each generator re-run plus `git diff --exit-code`
    (schema, OpenAPI, lockfiles, vendored lists); and the stack's strict
    lint/type/unused-code checks. Each repeated review finding becomes a new
    rule here or a repo lint. Measured 2026-10-09: about 35% of 146 structured
    review findings were catchable this way, and workflow logic was the
    largest single class.
22. **Review only green heads; no pushes during review.** Paperclip Review is
    requested once per CI-green, non-draft head. The author batches all
    findings of a round into one push after the review completes. Measured
    2026-10-10: 45% of review rows were superseded or incomplete, mostly
    from pushes while a review was queued or running.

23. **Web quality gate (2026-10-10).** Every repo that serves a website gates on
    Lighthouse and web standards, with budgets in a committed file (e.g.
    `ci/quality-budget.json`) so any loosening is a visible, reviewed diff:
    - Lighthouse category scores (desktop: performance >= 0.90, accessibility,
      best-practices and SEO >= 0.95; mobile performance floor) plus Core Web
      Vitals budgets (LCP <= 2.5 s, CLS <= 0.1, TBT <= 200 ms), 3 runs, median;
    - axe (0 serious/critical), security headers (CSP, nosniff,
      Referrer-Policy; HSTS where the app, not the edge, owns it), HTML basics
      (doctype, lang, title, viewport, description, one h1), internal links
      resolved against the app's own route table, and a bundle-size budget;
    - one "what to fix" job summary (failing audits, score vs budget, top
      offending resources), reports uploaded as artifacts (never
      temporary-public-storage).
    When it runs:
    - **PRs:** only when files that can change the rendered site change (app
      source, components, styles, public assets, build config,
      package.json/lockfile, the Lighthouse/standards config itself); docs-,
      CI- and tests-only PRs skip. It reports inside `ci-ok`, which counts a
      path-skipped job as success (the scope-gate pattern, never `on: paths:`).
    - **After every staging deploy:** against staging, and it gates promote to
      production (the promotion resolver requires it alongside the journeys).
    - **Nightly:** against staging, to catch drift no PR causes (third-party
      scripts, CDN, content).
    Agents fix failures; a budget is raised (ratchet) or lowered only in its own
    justified PR, and Paperclip Review treats loosening as a finding. Template:
    `workflow-templates/web-quality.yml`. Reference implementation:
    togetherweown/two-web-next.

24. **Web E2E and quality loop (2026-10-11).** Beyond the quality gate (rule
    23), every website repo closes the loop from "something broke" to "an agent
    fixed it":
    - **Critical journeys required on PR.** Browser journeys run on every PR
      that can change the site, against a local full stack (Worker + disposable
      DB, no secrets). Like every gated job (rule 3), the journeys count only
      through `ci-ok`: the scope step decides whether they run, a scope-skip
      passes, and a failed or cancelled upstream job fails `ci-ok`. They are not
      a separately required check. Per-PR remote previews are not used: they
      would put a deploy-capable credential in PR workflows.
    - **Visual regression.** Full-page screenshots of the public routes at
      desktop and mobile widths against committed baselines, before the
      journeys, with volatile regions (times) masked. CI never rewrites a
      baseline; an intentional change commits reviewed baselines in the same PR.
    - **Security scan of staging.** An OWASP ZAP passive baseline runs after
      every staging deploy (gating promotion: a High, or Medium/Low above the
      committed budget, fails the run the promotion resolver requires) and
      nightly. Public repos print alert names and counts only and never upload
      the report.
    - **Daily explorer + QA routine.** A GET-only, guest, staging-only crawl at
      desktop and mobile widths records non-200s, page/console errors, failed
      subresources, broken images, overflow and serious axe violations with
      stable finding ids; a daily Paperclip routine files one card per new
      finding for the site's engineer and notes resolved ones.
    - **Real-user metrics.** A nightly check of production p75 LCP/CLS/INP from
      the CDN's RUM data against committed budgets, with a read-only analytics
      token (never the deploy token).
    Scheduled failures go through the host flake ledger (one re-run, then a
    private main-red card); PR failures wake the PR's owner card via the PR
    controller. Baselines and budgets only ratchet; loosening is its own
    reviewed PR. Templates: `workflow-templates/web-zap-staging.yml`.
    Reference implementation: togetherweown/two-web-next (docs/visual-regression.md,
    docs/zap-baseline.md, docs/explore-staging.md, docs/rum-web-vitals.md).

## Banned

- `on: paths:` on any workflow that produces a required check.
- Requiring a skippable job without the `ci-ok` aggregator.
- `cancel-in-progress` on `main`, release, deploy or merge queue.
- Jobs without `timeout-minutes`.
- Full mutation / full E2E / full CodeQL on every push.
- Nightly / scheduled-only suites (full ignored-test sweeps, benchmarks)
  triggered on every `pull_request`. They run on `schedule`,
  `workflow_dispatch` and `push` to `main`; on a PR only when that PR changes
  the suite's own wiring or trips a Rule 4 full-run trigger (decided by the
  job-level change detector, so the workflow still reports). This never
  removes a PR-time check: dependency/advisory scans still run on lockfile
  change (Rules 6 and 19), and any guard that only the nightly suite ran moves
  into the per-PR workflow first. Measured 2026-10-10: one repo's nightly sweep
  ran on ~170 PR pushes a day, most cancelled by the next push.
- Retries that hide flakes (unlogged or ticketless).
- `push` + `pull_request` duplicate full runs on the same SHA.
- `[skip ci]` to dodge gates.
- A change-detection `git diff --name-only` without `--no-renames -z`.
- Suppressing a change-detection error and reporting an empty successful diff.

## Verification (per repo)

Done means merged plus verified: one docs-only PR skips the heavy jobs with
`ci-ok` green, and one dependency-file change runs everything. Rollback:
revert the wiring PR.
