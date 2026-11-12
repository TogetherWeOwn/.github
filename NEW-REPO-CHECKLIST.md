# New repo checklist

Use this list for every new TogetherWeOwn repo, whether a person or an automated
contributor creates it. [STANDARD.md](STANDARD.md) is the canonical contract.
Complete and verify these items before the first PR merges; source files and
property defaults alone do not prove that live protections are installed.

## Inherited defaults (verify, don't copy)

- [ ] **Existing org rules.** Verify inherited default-branch deletion/force-push
      protection (25234562) and `pr-standards` (26327170). The latter calls
      `.github/workflows/pr-standards-required.yml` at
      `97fc0e24038021dfaa1fc9da754c6c5d6429130f`, with no per-repo copy.
      Confirm the required workflow reports on a test PR; this checklist changes
      neither the live pin nor the org target set.
- [ ] **Community files.** The PR template, issue templates, CONTRIBUTING, SECURITY
      and SUPPORT come from this repo. A same-name repo file replaces the default.
      A custom PR template must retain the internal-reference comment/checklist
      and the complete Attestation and Model Used sections.

## Properties and baseline eligibility

- [ ] **Set `governance`.** Use `standard`, `fork-contrib` or `fixture` per
      [classification](STANDARD.md#custom-properties), instead of creating a
      duplicate baseline repo ruleset. Maintained local-development forks stay
      standard; contribution-only forks are excluded.
- [ ] **Set `codeql`.** Default `required` for public standard repos; use `deferred`
      for excluded repos and for private `kofra-cloud` / `open-serverless-vault`.
      Those two keep CodeQL disabled: no paid Code Security enablement and no
      security configuration 17 attachment under the current policy. Any future
      owner decision must first update [the private-repo boundary](STANDARD.md#custom-properties)
      and pass reviewed enrollment before either setting changes.
      **Set `codeql=deferred` explicitly on every new private repo before baseline
      targeting or security configuration attachment.** Keep CodeQL disabled until
      an explicit owner cost/security decision authorizes paid scanning and a
      reviewed enrollment changes the property; do not infer approval from the
      org-wide `required` default.
- [ ] **Verify both new baselines.** `baseline-review` targets `governance=standard`
      including private repos; `baseline-codeql` additionally requires
      `codeql=required`. Confirm rollout state and effective targeting. Evaluate
      is not Active; no bypass actors. Follow the paired Evaluate → Active rollout
      in STANDARD.md, not a per-repo ad hoc activation.
- [ ] **Enroll reviewers/scanning before enforcement.** Prove automatic completed
      `Devin Review` status from App 811515 on the test PR (user and bot authors),
      and target-branch CodeQL analysis where required. CodeRabbit is advisory,
      never a required check. Preserve applicable independent review.
- [ ] **Set `ci`.** Start `custom`; set `standard` only after `ci-ok` is on main
      and has reported green. Optional `baseline-ci` is not yet applied. Keep
      repo-specific CI/security checks in repo rulesets. **Require `ci-ok` and
      applicable security checks on the default branch** after their producers
      report green on main, pinning GitHub Actions-produced statuses to App 15368.
      `ci=custom` and disabled `baseline-ci` do not make CI advisory. Never require
      a check before its producer exists on main and has reported there
      (STANDARD.md CI rule 25).

## Set once in repo settings

- [ ] **Merge method.** Allow squash only; turn off merge commits and rebase merging.
- [ ] **Squash message.** Set title from PR title and message from PR description:
      `squash_merge_commit_title=PR_TITLE`, `squash_merge_commit_message=PR_BODY`.
- [ ] **Branch deletion.** Turn on automatic head-branch deletion.
- [ ] **Unique rules.** Preserve stack-specific checks and explicit protection for
      maintained non-default branches. Inspect inherited strictness rather than
      assuming every ruleset is non-strict (paperclip-ops inherits strict checks).

## Add from `workflow-templates/`

- [ ] `starter-ci.yml`, with real stack commands and tuned change areas.
- [ ] `pr-lint.yml`, keeping `INTERNAL_ID_LEVEL: "error"` and
      `REQUIRE_CARD_REF: "false"`.
- [ ] `gitleaks.yml` and `pr-size.yml`. Templates are copied/adapted; callable
      workflows listed in README are called by full reviewed SHA.
      Merge the caller first and observe its reported check before adding a
      required status check. Discover the actual emitted context and expected
      GitHub Actions App for each repository; do not infer the context from the
      caller job ID (STANDARD.md "PR size gate").
- [ ] Record exact-head GitHub Actions run/job evidence. No local full-suite or
      CI-mirror substitute. Prove docs-only skipping and full-run dependency paths.
- [ ] For preview/staging, document zero-idle-cost startup, bounded use and native
      cleanup/expiry before qualification; retain existing release/security gates.

## Naming and text

- [ ] No internal references in repo name/description/topics, README, commits,
      branches or PR metadata (CONTRIBUTING.md §2).
- [ ] First commit and PR title use a Conventional Commits header, e.g.
      `chore: initial import`.
- [ ] Confirm effective rules and the first PR's exact-head CI, Devin and applicable
      CodeQL evidence before merge. A template or Evaluate-only rule is not proof.
