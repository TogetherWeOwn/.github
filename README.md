# TogetherWeOwn org defaults

Shared community files, reviewed policy source, reusable automation and workflow
starters. Files here do not install workflows or apply GitHub settings by themselves.

## Start here

- **[STANDARD.md](STANDARD.md)** — canonical PR, CI, governance, deployment and release standard.
- [New repo checklist](NEW-REPO-CHECKLIST.md) — properties, effective-rule verification and bootstrap.
- [Contributing](CONTRIBUTING.md) — PR syntax, review replies and attestation.
- [Ruleset source and snapshots](rulesets/README.md) — Evaluate templates, exclusions and rollout boundary.
- [Security](SECURITY.md), [support](SUPPORT.md), [code of conduct](CODE_OF_CONDUCT.md).
- [Public PR security gate](PUBLIC-PR-SECURITY.md) and [test guard](TEST-GUARD.md).
- [Tier-0 quality gate](docs/tier-0-quality-gate.md).

`PIPELINE-STANDARD.md` and `CI-STANDARD.md` remain compatibility redirects, not
competing policy documents. The live `pr-standards` required-workflow pin remains
`97fc0e24038021dfaa1fc9da754c6c5d6429130f`; this consolidation does not repin it.

## Callable workflows and actions

Call only files with `on: workflow_call`, using a reviewed **full commit SHA**:

| Reusable | Purpose |
|---|---|
| [changes.yml](.github/workflows/changes.yml) | Merge-base change detector for stack CI. |
| [tier-0-quality-gate.yml](.github/workflows/tier-0-quality-gate.yml) | Deterministic quality/security checks, including gitleaks; mode and stack inputs are documented in the file. |
| [public-pr-security.yml](.github/workflows/public-pr-security.yml) | Opt-in public PR security gate. |
| [test-guard.yml](.github/workflows/test-guard.yml) | Test ownership gate. |
| [review-rules.yml](.github/workflows/review-rules.yml) | Shared review-rule scanner. |
| [pr-size.yml](.github/workflows/pr-size.yml), [pr-size-weekly.yml](.github/workflows/pr-size-weekly.yml) | Advisory labels and weekly tally; distinct permission grants. |
| [pr-attestation.yml](.github/workflows/pr-attestation.yml) | Shadow attestation observer. |
| [promote-staging.yml](.github/workflows/promote-staging.yml) | Resolve a qualified staging source; caller retains release gates. |
| [calver.yml](.github/workflows/calver.yml) / [CalVer action](actions/calver/README.md) | Shared release versioning. |

Example for the existing change detector (pin reviewed on main):

```yaml
jobs:
  changes:
    uses: TogetherWeOwn/.github/.github/workflows/changes.yml@773ee91a904b9e1b65eddaaad4400d36e05fa717
    with:
      areas-json: '{"app":["src/**"],"docs":["docs/**","*.md"]}'
      full-run-globs-json: '["package.json","**/package.json","package-lock.json","**/package-lock.json","npm-shrinkwrap.json","**/npm-shrinkwrap.json","pnpm-lock.yaml","**/pnpm-lock.yaml","yarn.lock","**/yarn.lock","Gemfile","**/Gemfile","Gemfile.lock","**/Gemfile.lock","Cargo.toml","**/Cargo.toml","Cargo.lock","**/Cargo.lock","go.mod","**/go.mod","go.sum","**/go.sum","pyproject.toml","**/pyproject.toml","poetry.lock","**/poetry.lock","uv.lock","**/uv.lock","requirements*.txt","**/requirements*.txt","requirements*.in","**/requirements*.in","setup.py","**/setup.py","setup.cfg","**/setup.cfg","Pipfile","**/Pipfile","Pipfile.lock","**/Pipfile.lock","composer.json","**/composer.json","composer.lock","**/composer.lock","pnpm-workspace.yaml","**/pnpm-workspace.yaml","go.work","**/go.work","go.work.sum","**/go.work.sum","tsconfig*.json","**/tsconfig*.json","jsconfig*.json","**/jsconfig*.json",".cargo/**","**/.cargo/**","rust-toolchain*","**/rust-toolchain*",".npmrc","**/.npmrc",".yarnrc*","**/.yarnrc*","Makefile*","**/Makefile*","CMakeLists.txt","**/CMakeLists.txt",".github/**","shared/**","common/**","build/**","Dockerfile*","**/Dockerfile*","docker-compose*.yml","**/docker-compose*.yml"]'
      docs-areas-json: '["docs"]'
```

There is **no standalone callable `ci.yml` or `gitleaks.yml`** here today:
[ci.yml](.github/workflows/ci.yml) is this repo's CI, not a reusable. Use the
existing components above and the starters below; do not invent callable paths.
Pin shared tooling and workflows consistently per their own adoption docs.

## Workflow starters (copy and adapt)

[workflow-templates/](workflow-templates/) contains starters, not callable
workflows. Begin with [starter-ci.yml](workflow-templates/starter-ci.yml),
[pr-lint.yml](workflow-templates/pr-lint.yml),
[gitleaks.yml](workflow-templates/gitleaks.yml) and
[pr-size.yml](workflow-templates/pr-size.yml). Adapt stack commands, change areas,
permissions and secrets; keep job-level gating and a fail-closed `ci-ok` aggregator.
Never require a new check until its producer is on main and has reported there.
