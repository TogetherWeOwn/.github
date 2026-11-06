# Reviewed org ruleset source

The two baseline files below are sanitized **live org exports**, not reconstructed
create payloads. The operator read `GET /orgs/TogetherWeOwn/rulesets/26726856`
(`baseline-review`) and `GET /orgs/TogetherWeOwn/rulesets/26726857`
(`baseline-codeql`) on 2026-11-06 at 21:51 UTC and supplied the responses after the
managed integration's org reads returned HTTP 403. Both were live in **Evaluate**;
committing these snapshots changes no settings and authorizes no activation.

Sanitization removes only top-level `created_at`, `updated_at` and `_links`;
`id`, `node_id`, `source_type`, `source`, conditions and every rule parameter are
retained. In particular, the live review export includes `required_reviewers: []`
and `require_extra_approval_for_unattributed_changes: true`. Treat these as reviewed
snapshots, not ready-to-submit create/update payloads: read-only identity fields
must be handled for the chosen API operation. Future policy changes go through a
PR and the separately authorized settings rollout, followed by a fresh export.

See [STANDARD.md](../STANDARD.md#1-governance-and-required-gates) for property schema,
classification, private-repo exclusions, per-repo extras and staged rollout.

- `baseline-review.json`: Evaluate; default branch of `governance=standard` repos,
  including private standards. Squash PR, 0 approvals, resolved threads; Devin
  commit status pinned to App 811515; non-strict; no CodeQL rule, no bypass actors.
- `baseline-codeql.json`: Evaluate; default branch only when **both**
  `governance=standard` and `codeql=required` match. CodeQL thresholds copied from
  two-web-next ruleset 24197355: high-or-higher security alerts, errors for alerts.
  No bypass actors. GitHub's `repository_property.include` is an AND across
  properties ([REST schema](https://docs.github.com/en/rest/orgs/rules#create-an-organization-repository-ruleset)).
- `baseline-ci.json`: **disabled, optional, not yet applied**; default branch when
  `governance=standard` AND `ci=standard`. Requires `ci-ok` from App 15368. Before
  any adoption, its producer must be merged and verified on the default branch.

Both review and CodeQL baselines move through Evaluate → Active **together**,
first on qualified canaries, then on all eligible targets. Do not activate while
Devin enrollment or target-branch CodeQL analysis is missing. Contribution forks
and fixtures are excluded by governance; private kofra-cloud and
open-serverless-vault stay standard but `codeql=deferred`. Never attach security
configuration 17 or enable paid Code Security on those two repos. Attach/enforce
configuration 17 only on the eligible `codeql=required` target set, retaining
advanced setups. No script in this directory applies anything.

## Existing inherited snapshots

`exports/25234562.json` and `exports/26327170.json` were read on 2026-11-06 via
`GET /repos/TogetherWeOwn/.github/rulesets/<id>`. They preserve the exact returned
objects (formatted JSON), including existing bypass configuration and workflow pin.
The new baselines have no bypass actors; this PR does **not** change the existing
25234562 OrganizationAdmin bypass. The `pr-standards` pin stays
`97fc0e24038021dfaa1fc9da754c6c5d6429130f`.

These repository-scoped responses omit org repository targeting. The org-admin
ruleset endpoint was not readable in the source audit; **do not import snapshots
as create/update payloads or infer coverage of future repos from them**. A settings
rollout must read/verify actual org targeting through an authorized admin scope.

## Verification

The CI `gate selftest` job runs `ci/test_org_standard.py`: exact property targets,
App pins, CodeQL thresholds, enforcement modes, no-bypass baseline invariants,
paired private exclusion and unchanged snapshot pin. It also checks local doc
links and compatibility redirects. Tests read JSON as data and perform no API
writes. GitHub Actions is the CI of record; no local full suite is required.
