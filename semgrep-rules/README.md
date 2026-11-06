# Tier-0 org Semgrep starter pack

Five live rules across two files, plus two disabled templates. The bar for a
rule to stay live is the rollout rule: under 5% false positives over the last
20 PRs.
A rule that guesses at repo-specific names (authz helpers, grant writes)
cannot clear that bar blind, so those ship as `paths:`-scoped templates
each repo enables by naming its own functions — not as generic regexes.

- `generic-dangerous-functions.yaml` — live, language-scoped: dynamic
  JavaScript/TypeScript `eval`, `exec` and `new Function`, Python `eval` and
  `exec`, interpolated JavaScript/TypeScript shell commands, and Python shell-enabled
  command execution. Python covers always-shell helpers and shell flags supplied
  by explicit `shell=`, literal `**{"shell": ...}` mappings, one nested literal
  expansion (`**{..., **{"shell": ...}}`), or Popen's ninth positional parameter
  (including forwarding wrappers). Flags statically `False`, `0` or `None` are
  excluded; unknown values in those bindings are conservative findings. Literal
  merge order is not evaluated: any matched unsafe/unknown shell value is reported,
  even if a later safe value overrides it. Safe-only merges remain clean. Plain
  argv calls are not shell sinks. This is bounded syntactic matching, not exhaustive
  analysis of deeper merges or dynamically constructed arguments.
- `generic-secrets-in-code.yaml` — live: hardcoded credential assignments
  outside test fixtures.
- `template-authz-helper.yaml` — DISABLED template (stub callee matches
  nothing): route handlers must call the repo's authz helper. Enable by
  replacing the stub with the repo's helper call shape.
- `template-check-then-write.yaml` — DISABLED template (stub callee matches
  nothing): no check-then-write on grant/allow writes without an atomic
  operation. Enable by replacing the stub with the repo's grant-write sinks.
