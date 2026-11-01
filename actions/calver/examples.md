# Pinned caller fragments

These are integration fragments, **not runnable deployment workflows**. Keep the
caller's existing build, deploy, approval, artifact, certificate, smoke and
rollback jobs. Pin values below resolve to a real tooling commit whose workflow
and engine content match this tree; required CI verifies that parity. After
merge, use the exact merged SHA published with this change in every pin.

## Reusable workflow

The outer workflow holds its existing transaction lock across planning, caller
build/deploy/verification jobs, and publication. A lock scoped to one reusable
invocation is not enough. Keep its group distinct from any nested callee group.
Do not add a second lock using the same group inside the called workflow.

```yaml
# At caller workflow scope; all writers/repairs must share its existing boundary.
concurrency:
  group: application-release-transaction
  cancel-in-progress: false

jobs:
  plan-version:
    uses: TogetherWeOwn/.github/.github/workflows/calver.yml@838b93131c4bac8224eed66b5e27a183c302d69c
    permissions:
      contents: read
      actions: read
    with:
      tooling-sha: 838b93131c4bac8224eed66b5e27a183c302d69c
      runs-on-json: '"ubuntu-latest"'
      # Trusted caller context constructs JSON; never evaluate user JSON as shell.
      request: ${{ needs.context.outputs.plan-request }}
    needs: [context]

  # Existing caller jobs consume needs.plan-version.outputs.plan/version.
  # Their actual approved build/deploy/certification and rollback logic stays.
  # Publication passes that exact plan plus independently verifiable evidence;
  # its job grants contents:write and actions:read to this same reusable.
```

For a private caller, replace `runs-on-json` with
`${{ vars.CI_OVERFLOW_RUNNER }}` (the existing explicitly configured self-hosted
JSON labels, beginning with `self-hosted`), without a hosted default or new pool.
The wrapper checks out `TogetherWeOwn/.github` separately at `tooling-sha`; it
does not accidentally look for shared scripts in a default caller checkout.
Public callers always schedule the standard free `ubuntu-latest`.

## Step adapter in the caller's existing serialized transaction

The planning fragment runs before version stamping/building. The publication
fragment runs in a **successor job**, after the named deploy and certification
jobs have actually completed. They remain inside one outer transaction lock.
The fragments below are not permission to put the publisher in its own still-
running required-certification job or to replace real gates with an echo step.

```yaml
# Planning job: contents:read, actions:read. Source run/attempt are known now;
# artifact ID/digest are intentionally unavailable until after upload.
- uses: TogetherWeOwn/.github/actions/calver@838b93131c4bac8224eed66b5e27a183c302d69c
  id: version
  with:
    runs-on-json: '"ubuntu-latest"'
    request: ${{ steps.context.outputs.prebuild-plan-request }}

# Separate successor publication job: contents:write, actions:read.
# It consumes the saved plan plus the actual immutable artifact proof.
- uses: TogetherWeOwn/.github/actions/calver@838b93131c4bac8224eed66b5e27a183c302d69c
  if: success()
  id: publication
  with:
    runs-on-json: '"ubuntu-latest"'
    request: ${{ steps.context.outputs.verified-publish-request }}
```

A pre-build request has `phase: "plan"`, and `input` with `channel: "canary"`,
exact `sha`, stable `operation`, `policy: "staged-promotion"`, and `source` with
`runId`, `attempt`, trusted `workflow` and required `jobs`. Publishing uses
`phase: "publish"`, the exact `plan` object, and full `evidence` with matching
selector plus `artifactId`, `artifactName`, `artifactDigest`. Stable promotion
plans use full evidence and `sourceCanary`; the source run must then be completely
successful, with artifact/member/image checks still owned by the caller.

To verify a publication without writes, set `dryRun: true` on the publish
request. The same validations run, but the API transport refuses non-GET calls.
There is no caller-local notes filename output and no cloud credential input.

A bot live-digest adapter retains its live-production resolver and classifies
`skipped`/`reason` from planning before deciding whether to publish or dispatch
SBOM attachment/repair. Use an explicit repository exception, not a general
"skip staging" boolean. Routeware builds use `namespace: "build"` and
`latest: "never"`; this is not a change to its API contract release or an
instruction to enable production.
