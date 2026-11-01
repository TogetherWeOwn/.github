# New repo checklist

Use this list for every new TogetherWeOwn repo, whether a person or an agent creates it.
Most of the standard comes from the org level, so a new repo picks it up on day one.
Check each item, and complete the last section, before the first PR merges.

## Inherited automatically (verify, don't copy)

- [ ] **Org ruleset.** The org ruleset targets all repos. It requires the `pr-standards`
      check (`.github/workflows/pr-standards-required.yml`), with no per-repo file.
      Open a test PR and confirm the check reports.
- [ ] **Community files.** The PR template, issue templates, CONTRIBUTING, SECURITY and
      SUPPORT come from this repo. A repo file with the same name replaces the org
      default. If the repo adds its own `pull_request_template.md`, keep the header
      comment and checklist line about internal references from the org template.

## Set once in repo settings

- [ ] **Merge method.** Allow squash merging only: turn off merge commits and rebase
      merging.
- [ ] **Squash commit message.** Set *Default commit message* to "Pull request title and
      description" (API: `squash_merge_commit_title=PR_TITLE`,
      `squash_merge_commit_message=PR_BODY`). The default, "commit title or PR title",
      uses a single commit's own subject, which is how internal ids reached `main`
      before.
- [ ] **Branch deletion.** Turn on *Automatically delete head branches*.
- [ ] **Branch protection.** Add a `protect-main` repo ruleset: CI (`ci-ok`),
      `Devin Review`, and on public repos `CodeRabbit`, per PIPELINE-STANDARD.md §1.
      Add a check only after it has reported on `main` (CI-STANDARD.md rule 25).

## Add from `workflow-templates/`

- [ ] `starter-ci.yml`, then tune the change areas (CI-STANDARD.md).
- [ ] `pr-lint.yml`, which checks the PR template sections. Keep
      `INTERNAL_ID_LEVEL: "error"` and `REQUIRE_CARD_REF: "false"`.
- [ ] `gitleaks.yml` and `pr-size.yml`.

## Naming and text

- [ ] **No internal references.** The repo name, description, topics, README, first
      commit and first branch carry no internal tracker ids, instance links or
      hostnames (CONTRIBUTING.md §2).
- [ ] **First commit and PR title.** Use a Conventional Commits header, for example
      `chore: initial import`.
