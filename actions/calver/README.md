# Shared application CalVer releaser

One Node-built-in engine backs the [composite action](action.yml) and
[reusable workflow](../../.github/workflows/calver.yml). It reads GitHub metadata
and publishes version labels, **not deployments**. It never checks out or executes
the requested application commit, rebuilds an artifact, invokes production, changes
repository settings or enables immutable Releases.

See the [interface contract](contract.md) and [full-SHA examples](examples.md).

## Version policy

- Stable: `YYYY.MDD.P`, tag `vYYYY.MDD.P`.
- Canary: `YYYY.MDD.P-canary.N`, tag `canary/vYYYY.MDD.P-canary.N`.
- UTC month is not zero-padded; day is two digits: January 5 is `2026.105.0`,
  March 3 is `2026.303.0`, October 28 is `2026.1028.0`.
- P starts at zero and advances above the maximum stable counter for that date.
  Canary N starts at zero for the next stable base, advancing above that base's
  highest canary. Versions compare numerically, not lexically or by Git sort.
- Plan freezes the promotion attempt's UTC date. A transaction crossing midnight
  publishes that exact version, not a recomputed one. A historical `repairDate`
  requires `repair: true`; future repair dates are refused.
- Counters use all paginated repository tags. Annotated tags are peeled with
  cycle/depth limits. Non-404 lookup errors, incomplete/ambiguous history and
  changing refs fail closed. Legacy SemVer/daily tags remain untouched and can
  supply a reachable first notes boundary, but do not allocate CalVer counters.

### Deliberate differences from upstream Paperclip

We adopt [Paperclip's numeric syntax](https://github.com/paperclipai/paperclip/blob/96727b3cfefb61f85ffe1fbc29a9b6dd6837efe8/doc/RELEASING.md),
not its complete npm publication policy. Upstream's allocator uses published npm
versions, while applications here use repository tags. Upstream also has nightly,
approved beta and normally a three-day beta soak. Existing application staging,
E2E, soak, approval and production smoke gates remain authoritative instead.

Package/plugin compatibility stays SemVer (including public Kofra core and
consumed libraries). Internal package manifests are not reset to `0.0.0` merely
to label deployments. Kagi is an explicit exception: its existing package/tag
contract requires a real CalVer version PR and a consistent lockfile.

## Call protocol

A JSON request is an environment value, never shell-interpolated code:

```json
{"phase":"plan","input":{"channel":"canary","sha":"0123456789abcdef0123456789abcdef01234567","operation":"staging/12345/1","policy":"staged-promotion","evidence":{"runId":12345,"attempt":1,"workflow":".github/workflows/deploy-staging.yml","jobs":["certify-promotion"],"artifactId":67890,"artifactName":"certified-build","artifactDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}}}
```

These are illustrative selectors, not evidence. The engine independently reads
the exact attempt, required jobs and immutable artifact metadata. `workflow` and
`jobs` are trusted caller-workflow configuration identifying its real safety
boundary, not user-supplied assertions that a deployment succeeded. The caller
still verifies the archive, component/module/static-asset hashes, image digests,
certificates and approved environment before production secrets are used.

The source must be an eligible same-repository `main` push, not a manual run.
Every named deploy/certification job must exist uniquely and have actually
completed successfully; skipped/fault/failed jobs do not certify a build. Artifact
ID, name, SHA-256 archive digest, run, SHA, branch, expiry and creation after the
attempt's start are independently checked. A stable staged plan additionally
requires `sourceCanary` with this exact SHA and run/attempt/artifact provenance
and a fully completed successful source run.

For **pre-build canary planning**, pass `source` with only `runId`, `attempt`,
`workflow` and `jobs` instead of `evidence`. No artifact exists yet. After the
build, deploy and certification jobs complete, pass the full `evidence` alongside
the exact saved plan in the publication request. It must match the frozen source
selector and is stored as `publicationEvidence` in the tag annotation; retries
must match that exact proof. No version is reallocated or plan rewritten.

A canary publisher may be a successor job inside its still-in-progress source
run after all named jobs completed successfully, so the outer transaction lock
can remain held. This is not permission to publish before deployment: missing,
skipped, failed and running required jobs all refuse. Stable promotion does not
allow that in-progress exception. A follow-up's default `github.sha` is not the
selected source SHA. Callers must retain their stronger normal-vs-recovery/fault
certificate checks: this generic tool does not parse application-specific receipts.

The action returns a portable JSON `plan` plus `version`, `tag`, `sha`,
`release-date`, `previous-stable-tag`, `created`, `release-url` and `provenance-id`.
Carry that plan through the transaction without editing it. Publish with:

```json
{"phase":"publish","plan":{"...":"exact saved plan object"},"dryRun":true,"repair":false}
```

`dryRun: true` runs the same reads, evidence, collision, notes and repair checks,
but the API client prohibits every non-GET request. Plan always uses the same
read-only client fence. Local Actions outputs/summaries are still written. Remove
`dryRun` only for an authorized publication after existing deployment gates.
No runner-local notes path is returned or passed between jobs.

## Transaction and replay discipline

The caller must serialize **plan → version stamp → deployment → publish**, including
manual writers. Separate plan/publish reusable invocations cannot hold that lock
through caller jobs. The step adapter runs inside an already-serialized job; a
wrapper caller can hold a distinct outer workflow transaction lock. Never reuse
the caller lock group for a nested workflow. `cancel-in-progress: false` preserves
a running transaction but allows replacement of an older pending run: GitHub
concurrency is not FIFO. Only actual successful staging deployments get canaries,
not every merge that was superseded before deployment.

Planning is advisory without that boundary. Publication never silently reallocates
a stamped version. An exact planned ref collision on another SHA or another
operation fails; retry allocation only **before** deployment/version stamping.
A provenance annotation binds repository, operation, policy, channel, SHA,
version/date, source canary and artifact identity. Its deterministic ID detects
changed plans; it is not a cryptographic signature or replacement for workflow
trust and token permissions. A same-SHA arbitrary tag is not an idempotency key.

Tags are created through Git objects/refs, never moved or deleted. Stable Releases
are created against the verified existing tag. A 422 is not blindly retried:
re-read exactly once and accept only an exact concurrent match. A tag without its
Release is a partial transaction: rerun the original saved plan with `repair: true`.
An exact operation retry recovers the original plan even across midnight. Never
change the operation ID just to get past a refusal. Existing legacy repair adapters
remain responsible for their legacy tags; the new engine does not adopt arbitrary
old tags as its own operation provenance.

Historical repair never moves Latest backwards. Build-namespace Releases require
`latest: "never"`. Other new application stables explicitly become Latest only
when no numerically later stable exists. This comparison is protected by the same
caller writer lock; GitHub does not offer compare-and-swap for Latest. The engine
cannot compensate for independent writers ignoring the transaction contract.

## Repository exceptions (not a general bypass flag)

- **TWO bot:** `live-digest` is stable-only for `TogetherWeOwn/two-bot-next`.
  Its existing adapter resolves and proves live production first. Production
  workflow, takeover/allow-unstaged/schema gates, deployment IDs, readiness stamps,
  closed receipt schema and rollback `--message` SHA stay unchanged. Daily digest
  cadence, quiet-day/rollback/lost-dispatch/legacy repair and SBOM attachment
  behavior remain in the adapter. The shared planner additionally refuses quiet,
  rollback/divergent and already-issued daily digests with classified skip codes;
  the CLI reports a green `skipped=true` and `reason` without a plan. Callers must
  not publish when planning skipped. A CalVer tag is not new production evidence.
- **Kagi:** `direct-release` is stable-only for `TogetherWeOwn/kagi-shinobi`.
  Keep release selection, package/tag equality and environment gates. It publishes
  before deployment; this tool does not turn every main merge into a deployment.
- **Routeware:** `direct-release` supports only `TogetherWeOwn/routeware-shadow-api`
  with `namespace: "build"`, `latest: "never"`. Tags are `build/vYYYY.MDD.P` (future
  canaries are `build/canary/vYYYY.MDD.P-canary.N`). SemVer package/OpenAPI/
  APP_VERSION/contract tags and consumer CHANGELOG stay unchanged. Production
  remains disabled; no deployment or lifecycle deletion follows from this work.

A same-SHA rebuilt artifact is not immutable promotion. In particular, existing
web staging/production entrypoint differences are not resolved or certified by a
CalVer label. Embedded `buildVersion` may remain canary while deployment metadata
uses stable; never rewrite compiled constants to make labels agree.

## Permissions, routing and notes

Plan requires repository reads and Actions reads. Publish requires repository-local
`contents: write` plus Actions reads for source evidence. No cloud credential is
accepted by the shared tool. Adapter-only workflow-dispatch/SBOM permissions stay
in the adapter. GITHUB_TOKEN-created tags/Releases do not trigger ordinary
downstream workflows: call/dispatch the intended next workflow explicitly.

Public callers schedule only the free standard `ubuntu-latest`. Private callers
must supply their existing self-hosted `CI_OVERFLOW_RUNNER` labels explicitly.
The wrapper chooses routing from repository context/configuration, not arbitrary
input, and refuses a missing/non-self-hosted private configuration instead of
falling back to hosted compute. The action rechecks visibility from GitHub and
compares inputs to configured labels before any mutation. When used as a step,
the caller still owns scheduling its job on the existing approved runner. No new
runner spend, security settings or host automation is introduced.

Notes reuse the existing web/bot Conventional Commit parser, grouping, scope/PR
links and BREAKING CHANGE handling. History intake is bounded at **256 MiB**, not
mistakenly lowered to the bot's 32 MiB subprocess limit. Publication uses a JSON
HTTP body, not a large argv value. Output notes exceeding a conservative 120,000
byte GitHub body budget fail before the first write rather than silently truncate.
Previous stable selection is numeric and reachable; initial history has no
fabricated previous release. Publication and rendering stay in the same job.

Do **not** enable immutable Releases globally: the bot uploads/clobbers SBOM assets
asynchronously after publication, including missing-dispatch repair.

## Verification and rollout

`node --test ci/calver*.test.mjs` runs independent offline acceptance tests. They
are wired into the existing always-on `gate-selftest` job, already required by
`ci-ok`, rather than an unrequired green-only check. `node actions/calver/check-pins.mjs`
checks the example workflow/action/tooling pins agree and carry the current
implementation. `actionlint .github/workflows/calver.yml` checks workflow syntax.
No deployment workflow or live release mutation is needed for these checks.

Repository migrations consume the exact **merged** shared tooling/workflow SHA
published with the completed change, using it for both workflow and tooling.
Until then, the examples pin a real feature commit with identical tooling;
that is not a claim that the change is merged or production-verified. Each
repository's migration retains its own required review and deployment gates.
