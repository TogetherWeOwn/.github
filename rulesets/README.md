# Reviewed org ruleset source

These files are reviewed policy inputs, **not applied settings**. See
[STANDARD.md](../STANDARD.md#1-governance-and-required-gates) for property schema,
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
