# Public pull-request security gate

Opt-in reusable: [`.github/workflows/public-pr-security.yml`](.github/workflows/public-pr-security.yml).
This change installs **no callers**, modifies no rulesets and changes no existing
required checks or reviewers. Private repositories and events other than
`pull_request` skip **every** job, even when a caller is misconfigured. No scheduled
scans, paid service, inherited secrets, `pull_request_target` or self-hosted runner.
Existing CI may run the offline regression fixtures on its existing schedule;
those tests do not scan the repository or contact vulnerability services.

## Adopt one public repository

1. Start from [the caller example](workflow-templates/public-pr-security.yml.example).
   Copy it to `.github/workflows/security.yml`; replace its placeholder with the
   **full 40-character reviewed merge SHA** containing the reusable. Do not use a
   branch/tag. The `.example` file is documentation, not an installed caller.
2. Retain its caller guard **and** permissions: `actions: read`, `contents: read`,
   `security-events: write`. These exactly cover the nested OSV reusable's job
   permissions; omitting `actions: read` or lowering `security-events` makes GitHub
   reject the workflow before a job can start. GitHub's `pull_request` SARIF upload
   support covers public fork PRs; never switch to a privileged trigger to fix an
   upload error. Investigate errors rather than disabling a scanner.
3. Set `go-targets-json` to the application's real GOOS/GOARCH/build-tag targets,
   **not** generic example tags. Its default is Linux/amd64 with no tags. Each entry
   must contain exactly `goos`, `goarch`, and `tags`; 1–8 targets, at most 32 tags
   each. Values are passed as environment/argv data, never shell fragments or
   `eval`. Invalid/unsupported targets or build errors fail the job. Scans disable
   CGO, ambient GOFLAGS/GOENV, automatic toolchain switching and go.work selection.
   CGO-dependent projects must resolve unsupported coverage before adoption; do
   not claim this is a production build match when their build requires CGO.
4. Ensure the pilot's `.github/dependabot.yml` includes `package-ecosystem:
   github-actions`, directory `/`, on an appropriate update cadence. This is
   required **in each pilot**, not only in this org repository. The caller's
   reusable pin and its transitive action pins need deliberate updates/review.
5. Inspect all artifacts and resolve the initial inventory findings before making
   a policy check required. Keep all existing CI and reviewers unchanged. Do not
   require a new check before its workflow exists on the default branch. Pilot
   rollout and three green adopted repositories are separate acceptance work.

## What runs, and what the findings mean

| Scanner | Scope | Evidence / failure policy |
| --- | --- | --- |
| OSV-Scanner Action v2.7.0 | Maintained **PR base/head comparison**, new vulnerabilities only | `fail-on-vuln: true`, SARIF upload and mandatory local validation of upstream old/new JSON and clean diff SARIF artifacts; no historic-scan fallback |
| govulncheck v1.9.0 | Every tracked root or nested `go.mod`, for every configured target; **whole current symbol graph**, not only new findings | `-scan=symbol -format=openvex`; nonzero exit, missing/invalid document or any `affected` statement fails; `not_affected` remains evidence |
| cargo-deny 0.20.2 | Every tracked root/nested `Cargo.lock` with a sibling tracked `Cargo.toml`; complete workspace, all features, no target filter; **whole current advisory inventory** | `--workspace --locked --all-features check advisories`; central config ignores no advisories and denies yanked crates; SARIF plus exit status |
| zizmor 1.32.0 | All tracked `.github/workflows/*.yml`/`.yaml` (including nested paths) and `action.yml`/`action.yaml` definitions anywhere; **whole current inventory** | Online with a step-scoped token, auditor persona, strict collection, no PR config/ignore rules; JSON results and exit status must be clean |
| pinact 5.0.2 | Same full workflow/action inventory | `run --check --verify-comment --format=sarif` with an explicit empty central v3 config; no PR config, excludes or diff filter |
| Vendored metadata | Every tracked path component named exactly `vendor` or `third_party`, including nested trees | Each tree needs its **own** regular tracked `VENDORED.json`; malformed/missing metadata fails |

All tracked workflows/actions are audited, not only the changed files. A pilot
may therefore initially fail existing workflow findings or old Go/Rust advisories.
Only OSV is a new-vulnerability diff gate. Workflow-template examples outside
`.github/workflows` are not executable workflows and are not included, unless
named `action.yml`/`action.yaml`.

### Vendored metadata schema

For `src/vendor/lib/file.c`, put metadata in `src/vendor/VENDORED.json`:

```json
{
  "dependencies": [
    {
      "purl": "pkg:github/example/library@v1.2.3",
      "version": "v1.2.3",
      "upstream_commit": "0123456789abcdef0123456789abcdef01234567"
    }
  ]
}
```

The object has only `dependencies`, a nonempty list (maximum 1,000). Each item has
exactly the three string fields above: a `pkg:` package URL, a nonblank version,
and a full lowercase 40- or 64-hex upstream Git object ID. The gate validates the
metadata shape, **not** the truth of the claimed provenance or its license.
Metadata is limited to 64 KiB. A nested `src/vendor/lib/third_party` requires
`src/vendor/lib/third_party/VENDORED.json` too. Deleting the last tracked file of a
tree removes that tree from the current inventory; deleting only its metadata fails.

## Trust, bounds and evidence

- The policy driver is **embedded in the immutable reusable workflow**. The YAML
  anchor shares the identical literal script across steps; no caller checkout is
  used as Python code, no source checkout/ref input can replace policy, and no
  script needs repinning after squash. The readable source is
  [`security-gate/gate.py`](security-gate/gate.py); CI compares the rendered workflow
  byte-for-byte. Repository inspection always uses `python3 -I`.
- Checkout goes in `target/`, with no persisted credentials or submodules. Inspection
  reads the committed Git tree/blobs, not untracked/deleted/mutated worktree files.
  The policy rejects **all tracked symlinks and submodules** anywhere, including
  metadata symlinks and submodule entries: otherwise a scanner could silently miss
  dependencies or follow paths outside its checkout. This is intentionally strict;
  callers using those features must address the limitation before adopting.
  Traversal, control characters, non-UTF-8 and `.git` path components are rejected.
- Token-bearing zizmor/pinact steps see only materialized workflow/action **data**
  in a fresh temporary directory, not PR Python files, config or executables.
  The token has contents-read only. Language scans run in a separate hosted job,
  with no token passed to subprocesses. They inspect a regular-file snapshot with
  executable bits removed, `.cargo` config omitted and PR-selected Rust toolchain
  files omitted; no PR build scripts, `go generate`, Makefiles or package scripts
  run. Cargo metadata does not build crates. Go uses version-pinned Go 1.26.10;
  Cargo uses Rust 1.93.0. Unsupported minimum toolchains fail, rather than skip.
- Scanner archives are version-pinned and SHA-256 checked before extracting only
  the expected regular binary (no archive path extraction). Govulncheck is installed
  at an exact module version with the Go checksum database enabled. setup-go and
  rustup verify toolchain download checksums. No curl-to-shell/latest installer.
- Each discovery/inspection job has a 10-minute limit; local scanners have a
  300-second process-group timeout and 16-MiB stdout/stderr file cap. Snapshotting
  has a 120-second deadline. Discovery caps: 20,000 paths, 256 MiB total tracked
  data, 16 MiB per file, 32 modules/lockfiles per language, 256 workflows/actions.
  Workflow-policy and language jobs additionally have 20/30-minute limits. A cap,
  timeout, unavailable advisory service, bad checksum, missing tool or malformed
  output is a **failure**, never a clean verdict.
- `security-inspection`, `security-osv-validation`, `security-workflow-policy`,
  and `security-language-scans` preserve inspection/validation summaries, raw local
  scanner output, stderr and status where execution reached those stages, retained
  five days. Missing artifact data is an error. Hard job termination can leave
  partial evidence; its failed/cancelled job result still fails `security-ok`.
  Upload failure also fails the gate. No output is interpreted as success merely
  because the process returned zero. Upstream OSV separately preserves old/new
  JSON and SARIF; `osv-evidence` must download all three artifacts from this run
  and validate their completeness and the clean diff before aggregation succeeds.
- `security-ok` uses `always()` and accepts skipped conditional jobs **only** when
  successful inspection explicitly proved that scanner's inventory was absent.
  Failures/cancellations/skips of inspection, OSV or OSV evidence validation,
  absent discovery booleans, and skipped applicable scans fail aggregation.

### Upstream OSV boundary

The full action-workflow SHA is pinned to
`b1d267aea5211fa648d9d442fa64b90dbdcbdd36`. Its source requires both scan files
before comparing failed scans; that behavior and caller permissions were verified
at implementation. Its internal scanner/reporter container is **version-tagged**,
not digest-pinned. Pinning the reusable source does not make that transitive image
immutable. It also retains credentials for its own branch switching and honors
upstream scanner configuration; those are upstream implementation boundaries,
not capabilities provided to our policy step. Do not claim this gate enforces
image-digest immutability or scans submodules.

The upstream job does not expose a timeout input and declares no shorter timeout:
its hosted GitHub job has the platform's **360-minute default**, unlike our 5-minute
scanner limits. We do not fork/reimplement the PR diff or silently substitute the
deprecated `google/osv-scanner` reusable to conceal this limitation. Our mandatory
`osv-evidence` job additionally validates its old/new JSON result lists and package
shapes, plus a complete, finding-free diff SARIF document. Missing artifacts,
malformed JSON/SARIF or any upstream job failure fail `security-ok`. Tracked root
`old-results.json`, `new-results.json` and `results.sarif` paths are reserved and
rejected during inspection so PR files cannot prepopulate upstream output paths.

## Maintain and verify

After editing `security-gate/gate.py` or `security-gate/workflow.yml.in`:

```sh
python3 -I security-gate/render_workflow.py
python3 -I security-gate/render_workflow.py --check
python3 -I security-gate/test_gate.py
python3 -I security-gate/test_workflow.py
# With actionlint installed:
actionlint .github/workflows/public-pr-security.yml
```

The two offline suites are in the existing required `gate-selftest` → `ci-ok`
path, including public/private and event guards, nested discovery, provenance,
symlink/gitlink/traversal attacks, no PR imports, scanner errors/evidence, install
checksums, SHA pins, nested caller permissions and strict aggregation. Live
scanner/network smoke checks and actual pilot runs are additional evidence, not
replaced by the offline tests.

REA angle: upstream's maintained PR reusable already supplies the base/head diff;
its source, pinact's controlled-configuration implementation and zizmor's online
mode contract are the integration reference, rather than a new scanner/reporting
implementation.
