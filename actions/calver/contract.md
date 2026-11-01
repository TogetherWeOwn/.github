# CalVer engine contract

This is the executable interface for the shared action and reusable workflow, not
an authorization to deploy. Callers retain all existing deployment and rollback
gates. The implementation lives in `actions/calver/calver.mjs` (Node built-ins
only); its public test seam is below. No caller code is executed by the releaser.

## Pure functions

- `parseVersion(version)` returns `{date, patch, canary}` for canonical numeric
  `YYYY.MDD.P[-canary.N]`, where `date` is ISO `YYYY-MM-DD` and `canary` is null
  for stable. Throws on invalid/noncanonical dates, counters or shape.
- `parseTag(tag, namespace = '')` returns the parsed version plus `version` and
  `channel` for exactly `vVERSION` or `canary/vVERSION`; a nonempty namespace
  (`build`) precedes both forms. Returns null for legacy/unrelated tags.
- `allocateVersion({tags, date, channel, namespace = ''})` returns
  `{version, tag, date, channel, namespace}`. `tags` is an array of tag-name
  strings. Stable P is one above the greatest stable P for that date, starting
  at zero. Canary uses the next stable base and one above its highest N.
- `validateRunner(runnerJson, visibility, privateRunnerJson)` returns the parsed
  labels: public repositories require exactly `"ubuntu-latest"`; private
  repositories must match the explicit configured self-hosted labels. No
  default, paid-hosted fallback or caller-only visibility assertion.

## Read/write API

`new Releaser({api, repository, now})`: `repository` is `owner/name`; `now` is an
injected function returning a Date, defaulting to the real clock. `api` provides
`request(method, path, body?)`, returning the decoded JSON object, and
`paginate(path, key?)`, returning the complete array (following all Link pages).
HTTP errors expose numeric `status`. Only a 404 from an exact lookup is absence.

REST routes for fake API adapters (prefix `/repos/{repository}`): paginated
`/tags?per_page=100`; exact `/git/ref/tags/{encodeURIComponent(tag)}`;
`/git/tags/{objectSha}`; `/git/commits/{sha}`; `/compare/{sha}...main`;
`/actions/runs/{runId}/attempts/{attempt}`; paginated
`/actions/runs/{runId}/attempts/{attempt}/jobs?per_page=100` with key `jobs`;
`/actions/artifacts/{artifactId}`; paginated initial notes
`/commits?sha={sha}&per_page=100`, or `/compare/{encodedPreviousTag}...{sha}?per_page=100`
with key `commits`; `/releases/tags/{encodedTag}`. Mutations: POST `/git/tags`,
POST `/git/refs`, and stable-only POST `/releases`. Responses use GitHub's standard
REST shapes, including exact ref name/object and tag-object SHA/message/target.
The exported `GitHub` transport in `github.mjs` implements Link pagination;
`Releaser.tags()` tests can inject a complete-array fake `paginate`.

- `tags()` returns all `{name, sha}` refs, peeling annotated tags recursively,
  refusing noncommit/cyclic/bounded-out targets and ambiguous duplicate names.
- `plan(input)` is read-only and returns a JSON-serializable plan.
- `publish(plan, {dryRun = false, repair = false, evidence} = {})` revalidates the plan and
  evidence. Returns the plan plus `{created, releaseUrl, dryRun}`. An existing
  exact operation/version/SHA is idempotent. Same-tag other-SHA and same-SHA
  other-operation are hard failures. A missing stable Release requires explicit
  repair; ordinary retries cannot silently report success without a Release.

Plan inputs: `channel` (`stable|canary`), lowercase exact 40-hex `sha`,
`operation` (nonempty bounded identifier), `policy`
(`staged-promotion|production-promotion|live-digest|direct-release`), optional
`namespace` (only `build` is supported), optional `latest` (`auto|never`), optional
`repairDate` (ISO UTC calendar date), optional `sourceCanary`, optional `sections`
(1-50 `{type, section, hidden?}` notes groups in release-please
`changelog-sections` shape; frozen into the plan and its identity, defaults to the
built-in groups when absent), and `evidence` described below. Date
freezes at plan time, including across midnight; repairDate is only publishable
with explicit repair. Version cannot be silently reallocated after deployment.
Every plan binds repository, policy, operation, SHA, date, namespace, source and
version with a deterministic identity. A JSON plan is portable across jobs;
runner-local notes filenames are not outputs.

`sha` must independently resolve to a commit reachable from this repository's
`main` (GitHub compare status ahead/identical). Planning reads every tag page.
A tag name is never sufficient proof of a deployment or operation identity.
Published annotated tags carry the plan provenance in their annotation. Creation
uses POST git/tags then POST git/refs, never PATCH/DELETE. Ref collisions are
re-read once and accepted only if the exact annotation and peeled SHA match.
Other 422 errors must propagate. Stable Release creation always targets that
verified existing tag. Canary publication never creates a GitHub Release.

Policy exceptions are repository-scoped: live-digest is only TWO bot stable;
direct-release is only Kagi stable or Routeware build-namespace stable. The
engine does not deploy or assert that release publication proves a rollout.
Live digest adapters still own live-production, rollback and quiet-day proofs.

## Staging evidence

A staged `evidence` object selects `runId`, `attempt`, `workflow` (exact
`.github/workflows/*.yml` path), `jobs` (nonempty exact required job names),
`artifactId`, `artifactName` and `artifactDigest` (`sha256:` + 64 lowercase hex).
The engine reads the run attempt, complete jobs and artifact metadata from
GitHub. Require same repository and SHA, `push` on `main`, and each uniquely named
required job completed successfully, an immutable unexpired artifact bound to
that run and SHA, and exact artifact identity/digest. Required jobs must actually
run: skipped, failed, missing, duplicate, manual/fault-injection evidence fails.
Callers select the eligible artifact using their existing stronger certificates;
this minimal check does not replace archive/member/image verification.

**Pre-build planning:** a canary plan can instead supply `source` containing only
`runId`, `attempt`, `workflow`, `jobs`. This selector is independently verified
against GitHub, but is NOT a certificate: the artifact does not exist yet. Supply
the full `evidence` to `publish(plan, {evidence})` after deployment/certification.
It must match the frozen selector. The tag annotation stores the unchanged plan
plus `publicationEvidence`; an exact replay must match that proof too. This
avoids requiring an artifact ID before building/version stamping. A canary
publisher using this deferred-source plan can be a successor job in its
still-in-progress source run ONLY after all named deploy/certification jobs
actually completed successfully. Full-evidence plans require completed successful
runs at both planning and publication; they do not use this exception. Failed,
skipped or missing jobs still refuse. A completed run must be successful.

A stable staged promotion additionally requires an exact source canary on this
SHA whose annotation contains the same source run/attempt/artifact identity.
Stable promotion requires the source run fully completed successfully; it cannot
use the active-canary exception. Publish re-fetches evidence and the source
canary. This corrects the initial contract's impossible pre-build artifact
requirement and its self-deadlocking whole-run check for an in-run publisher.

## Production promotion evidence

`production-promotion` is root stable only, with no namespace and no
`sourceCanary`. It is for an application whose production deploy promotes an
already-soaked `main` commit and runs no canary channel. Its `evidence` has the
staged shape, but selects the production deploy run itself:

- the run attempt is same-repository, `head_branch` `main`, event `push`,
  `workflow_run`, `schedule` or `workflow_dispatch`, and path `workflow`. It is
  completed (with any conclusion, so a run that went red only in its release
  job stays repairable) or still in progress because the publisher is its
  successor job. Queued or waiting runs refuse;
- every named job completed successfully in that attempt (skipped, running,
  missing or duplicate jobs refuse), whether the run is still active or not;
- the deployed `sha` is reachable from `main` and from the run's head SHA. The
  run's head is main's tip when it started, not the deployed commit;
- the artifact is bound to that run, unexpired, nonempty, created after the
  attempt started, has the exact digest, and its name carries the deployed `sha`
  as a whole token. The trusted main workflow writes that name after its deploy
  gates pass. The caller still verifies the receipt content.

Before allocation, a production plan classifies two green skips (CLI
`skipped=true`, no plan): `already-released` when a stable tag (legacy or CalVer,
from another operation) already points at `sha`, and `rollback` when the newest
stable is not reachable from `sha`. An exact operation retry still recovers its
own frozen plan, so a missing Release stays repairable with `repair: true`.

## Notes and Latest

`renderNotes(commits)` groups Conventional Commits and BREAKING CHANGE footers;
`commits` is an array of `{sha, message}`. Large histories are bounded and fail
explicitly rather than silently truncating. Reachable previous stable selection
ignores future/unreachable versions and supports legacy release boundaries.
Notes render and publish in the same job; GitHub API JSON body is used, never a
large argv value. Historical repair and namespaced build Releases never advance
Latest. Explicit `make_latest` is computed numerically against all stable tags,
not GitHub's timestamp or lexical version sorting.

## Workflow/action contract

The composite action reads one JSON request (`phase`, plan input or existing
`plan`, publish options) via an environment variable, using the shared engine.
Outputs contain the JSON plan and scalar version/tag/SHA/date/provenance, never
a local notes path. The reusable workflow exposes the same JSON request and an
explicit runner input. It checks the public shared tooling out separately at an
immutable SHA; caller checkout is not a source for shared scripts. Templates
use full SHA pins and CI checks matching tooling content. Plan is read-only;
publish needs contents:write and Actions evidence reads, not cloud credentials.

A caller serialization boundary must span plan, version stamping, deployment and
publish (the step adapter fits an existing serialized job). Separate workflow
invocations do not hold that lock. GitHub pending concurrency runs can replace
one another even with cancel-in-progress:false; there is no claimed FIFO queue.
Manual repairs participate in the same writer discipline. No global immutable
Release setting changes, host automation, or production dispatches are allowed.

## Required offline acceptance coverage

Node's built-in test runner; fake APIs and local fixtures, zero live mutation.
Cover leap/date/canonical grammar, P/N numeric allocation, post-stable canaries,
empty/legacy history, multi-page refs, annotated peeling and lookup errors,
plan/deploy/publish collision without reallocating, exact replay and explicit
missing-Release repair, frozen cross-midnight date, historical Latest, unsafe
staging refusal, source binding, >32 MiB notes input and zero mutating dry-run
requests. Static coverage checks runner safety, SHA pins, no notes-path output
and required `ci-ok` wiring. Tests are independently authored on this feature
branch and included in its single implementation PR.
