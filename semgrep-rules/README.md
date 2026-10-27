# Tier-0 org Semgrep starter pack

Two live rules, two disabled templates. The bar for a rule to stay live is
the rollout rule: under 5% false positives over the last 20 PRs.
A rule that guesses at repo-specific names (authz helpers, grant writes)
cannot clear that bar blind, so those ship as `paths:`-scoped templates
each repo enables by naming its own functions — not as generic regexes.

- `generic-dangerous-functions.yaml` — live, language-scoped: dynamic `eval`,
  interpolated JavaScript/TypeScript shell commands, and Python shell-enabled
  command execution. Python covers always-shell helpers and explicit `shell=`
  flags unless statically `False`, `0` or `None`; unknown flags are conservative
  findings. Argv calls without a shell flag are not shell sinks.
- `generic-secrets-in-code.yaml` — live: hardcoded credential assignments
  outside test fixtures.
- `template-authz-helper.yaml` — DISABLED template (stub callee matches
  nothing): route handlers must call the repo's authz helper. Enable by
  replacing the stub with the repo's helper call shape.
- `template-check-then-write.yaml` — DISABLED template (stub callee matches
  nothing): no check-then-write on grant/allow writes without an atomic
  operation. Enable by replacing the stub with the repo's grant-write sinks.
