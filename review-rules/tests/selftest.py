#!/usr/bin/env python3
"""Rule-engine unit tests and equivalent Semgrep positive/negative fixture test.

python3 -I review-rules/tests/selftest.py
python3 -I review-rules/tests/selftest.py --semgrep

The fixture pass checks both signs for EVERY shipped rule, verifies scanner
work, and refuses parser/timeout errors. It uses the production digest/options.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_rules_runner', ROOT / 'scripts/run.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
checks = engine.checks
ALL = set(engine.PACKS)

# Each pure-text check has a positive and negative source fixture. Keep these
# alongside the AST fixtures, not embedded in the production implementation.
TEXT_CASES = [
    ('supply-action-pin', '.github/workflows/ci.yml',
     'steps:\n  - uses: actions/checkout@v4',
     'steps:\n  - uses: actions/checkout@' + 'a' * 40),
    ('supply-container-digest', 'Dockerfile',
     'FROM ubuntu:24.04', 'FROM ubuntu@sha256:' + 'a' * 64 + ' AS build\nFROM build AS next\nFROM scratch'),
    ('supply-download-integrity', 'scripts/install.sh',
     'curl -f https://example.invalid/main/tool.tar.gz -o tool.tgz\ntar xf tool.tgz',
     'curl -f https://example.invalid/archive/' + 'a' * 40 + '.tar.gz -o tool.tgz\nsha256sum -c hashes\ntar xf tool.tgz'),
    ('workflow-artifact-scrub', '.github/workflows/e2e.yml',
     'steps:\n  - uses: actions/upload-artifact@' + 'a' * 40 + '\n    if: always()\n    with:\n      path: playwright-report/',
     'steps:\n  - uses: actions/upload-artifact@' + 'a' * 40 + "\n    if: steps.scrub.outputs.swept == 'true'\n    with:\n      path: playwright-report/"),
    ('workflow-pull-request-target', '.github/workflows/ci.yml',
     '"on": [push, pull_request_target]\njobs: {}',
     'on: [push, pull_request]\njobs: {}\n# docs mention pull_request_target'),
    ('workflow-required-aggregator', '.github/workflows/ci.yml',
     '''run: echo '${{ toJSON(needs) }}' | grep -q '"result":"failure"' ''',
     '''run: echo '${{ toJSON(needs) }}' | jq -e 'all(.[]; .result == "success")' '''),
    ('security-suppression-change', '.gitleaksignore',
     'src/auth.ts:leak:1', ''),
    ('security-suppression-source', '.github/workflows/leaks.yml',
     'steps:\n  - run: gitleaks detect --gitleaks-ignore-path base.ignore',
     'steps:\n  - run: |\n      git show origin/main:.gitleaksignore > base.ignore\n      rm -f .gitleaksignore\n      gitleaks detect --gitleaks-ignore-path base.ignore'),
    ('security-committed-operator-switch', 'wrangler.toml',
     '[env.staging.vars]\nTWO_EVENTSUB = 1', '[env.staging.vars]\nSAFE = "true"'),
    ('shell-provision-cleanup', 'scripts/provision.sh',
     'tmp=$(mktemp -d)', 'tmp=$(mktemp -d)\ntrap \'rm -rf "$tmp"\' EXIT'),
    ('shell-trap-exec', 'scripts/run.sh',
     'trap \'rm -rf "$tmp"\' EXIT\nexec grype .',
     'trap \'rm -rf "$tmp"\' EXIT\ngrype .'),
    ('shell-trap-exec', 'scripts/run.sh',
     'trap \'rm -rf "$tmp"\' EXIT\nexec > >(tee "$log") grype .',
     'trap \'rm -rf "$tmp"\' EXIT\nexec > >(tee "$log") 2>&1'),
    ('shell-trap-exec', 'scripts/run.sh',
     'trap cleanup EXIT\nexec >|log grype .',
     'trap cleanup EXIT\nexec >|log 2>&1'),
    ('shell-trap-exec', 'scripts/run.sh',
     'trap cleanup EXIT\nexec grype \\\n  --foo .',
     'trap cleanup EXIT\nexec >log \\\n 2>&1'),
    ('workflow-cancel-cleanup', '.github/workflows/branch.yml',
     'steps:\n  - run: pscale branch create db test',
     'steps:\n  - run: pscale branch create db test\n  - if: always()\n    run: pscale branch delete db test'),
    ('api-list-pagination', 'ops/reader.py',
     'def read():\n    return get("/pulls?per_page=100")',
     'def read():\n    page = get("/pulls?per_page=100")\n    while next_link(page):\n        page = get(next_link(page))'),
    ('shell-tool-failure-hidden', 'scripts/fetch.sh',
     'gh run download 1 || true', 'gh run download 1 || exit 1'),
    ('shell-scan-proves-work', 'scripts/leaks.sh',
     'gitleaks git --log-opts "$range"',
     'gitleaks git --log-opts "$range" 2> log\ngrep -Eq "[1-9][0-9]* commits scanned" log'),
    ('shell-pr-first-match', 'scripts/pr.sh',
     "gh api /repos/o/r/pulls --jq '.[0].number'",
     "gh api /repos/o/r/pulls --jq '[.[] | select(.head.sha == $sha)][0].number'"),
    ('shell-cli-head-binding', 'scripts/merge.sh',
     'gh pr merge 1 --match-head-commit --squash',
     'gh pr merge 1 --match-head-commit "$evaluated_sha" --squash'),
    ('ts-tar-exclude-flavor', 'src/transfer.ts',
     'const args = ["--exclude", "./target"];',
     'const args = tarExcludeFor(local, remote, ["target"]);'),
    ('rust-sql-list-limit', 'workers/api/src/library.rs',
     'fn list_statement() -> String { "SELECT * FROM media".into() }',
     'fn list_statement() -> String { "SELECT * FROM media LIMIT 20".into() }'),
    ('rust-duplicate-secret-codec', 'src/session.rs',
     'fn decode_base64(input: &str) {}', 'fn decode_secret(input: &str) { shared::decode(input); }'),
]


class TextChecks(unittest.TestCase):
    def test_all_positive_negative(self):
        for rule, path, positive, negative in TEXT_CASES:
            with self.subTest(rule=rule, sign='positive'):
                hits = checks.check_text(path, positive, ALL)
                self.assertIn(rule, {x['rule_id'] for x in hits})
            with self.subTest(rule=rule, sign='negative'):
                # A removed suppression file has no positive text to inspect;
                # prose mentions must not be treated as suppression changes.
                safe_path = 'docs/security.md' if rule == 'security-suppression-change' else path
                hits = checks.check_text(safe_path, negative, ALL)
                self.assertNotIn(rule, {x['rule_id'] for x in hits})

    def test_pin_exact_length_quotes_flow_folded_and_staging(self):
        for source in ["{uses: 'actions/checkout@v4'}", '  - "uses": "actions/checkout@v4"',
                       '  - uses: >-\n      actions/checkout@v4',
                       '  - uses: actions/checkout@' + 'a' * 43]:
            self.assertIn('supply-action-pin', {x['rule_id'] for x in checks.check_text('ops/sdlc/staging/check.yml', source, ALL)})
        for source in ['uses: ./local/action', 'uses: docker://alpine@sha256:' + 'a' * 64,
                       'uses: actions/checkout@' + 'A' * 40 + ' # version']:
            self.assertFalse(checks.supply_checks('ci.yml', source))

    def test_container_mapping_form_checks_nested_image(self):
        pinned = 'jobs:\n  build:\n    container:\n      image: node@sha256:' + 'a' * 64
        unpinned = 'jobs:\n  build:\n    container:\n      image: node:20'
        self.assertNotIn('supply-container-digest',
                         {x['rule_id'] for x in checks.supply_checks('.github/workflows/ci.yml', pinned)})
        self.assertIn('supply-container-digest',
                      {x['rule_id'] for x in checks.supply_checks('.github/workflows/ci.yml', unpinned)})

    def test_checksum_must_precede_execution(self):
        text = 'curl https://example.invalid/main/install.sh | sh\nsha256sum -c hashes'
        self.assertIn('supply-download-integrity', {x['rule_id'] for x in checks.shell_checks('install.sh', text)})

    def test_checksum_in_other_step_does_not_excuse_download(self):
        text = 'steps:\n  - run: curl https://example.invalid/main/tool.tar.gz | tar xz\n  - run: sha256sum -c hashes'
        self.assertIn('supply-download-integrity', {x['rule_id'] for x in checks.check_text('.github/workflows/ci.yml', text, ALL)})

    def test_pagination_in_other_function_not_an_exemption(self):
        text = 'def correct():\n    return next_link(page)\ndef broken():\n    return get("/pulls?per_page=100")'
        self.assertEqual(1, len(checks.pagination_checks('ops/read.py', text)))

    def test_trigger_mutants(self):
        for text in ['on:\n- push\n- pull_request_target\njobs: {}',
                     '  "on":\n    pull_request_target: {}\n  jobs: {}',
                     'on: [push, pull_request_target] # zizmor: ignore']:
            self.assertIn('workflow-pull-request-target', {x['rule_id'] for x in checks.workflow_checks('.github/workflows/a.yml', text)})

    def test_workflow_runs_preserve_line_offsets(self):
        text = 'steps:\n  - name: install\n    run: |\n      echo one\n      echo two\n  - run: echo three\n'
        self.assertEqual([(4, 'echo one\necho two\n'), (6, 'echo three')], checks.workflow_runs(text))

    def test_svelte_line_mapping(self):
        text = '<h1>Hello</h1>\n<script lang="ts">\nconst x = 1;\n</script>\n<p>{x}</p>'
        result = engine.svelte_scripts(text)
        self.assertEqual(text.count('\n'), result.count('\n'))
        self.assertEqual(result.splitlines()[2], 'const x = 1;')
        self.assertNotIn('<h1>', result)
        with self.assertRaises(ValueError):
            engine.svelte_scripts('<script>broken')

    def test_document_mentions_and_pack_selection(self):
        self.assertFalse(checks.check_text('docs/a.md', 'uses: actions/checkout@v4\npull_request_target\n.gitleaksignore', ALL))
        self.assertFalse(checks.check_text('.github/workflows/ci.yml', 'uses: actions/checkout@v4', {'rust'}))

    def test_shell_explicit_status_and_sourced_cleanup(self):
        self.assertFalse(checks.shell_checks('a.sh', 'git diff a b 2>/dev/null\nrc=$?\nexit "$rc"'))
        self.assertFalse(checks.shell_checks('a.sh', '# sourced-cleanup: parent.sh\ntmp=$(mktemp -d)'))
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nrm -rf "$tmp"\ntrap - EXIT\nexec tool')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec >log 2>&1')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec -a logger')})
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec >log grype .')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec >log 2>&1; echo hi')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec > >(tee -a "$log") 2>&1')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec > >(tee "$log-$(date +%F)") 2>&1')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec > >(tee "a(b)") 2>&1')})
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec 2>|log grype .')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec >log | grype .')})
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec \\\n grype .')})
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec >log 2>&1 \\\n grype .')})
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec "grype .')})
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec >log \\\n 2>&1')})

    def test_shell_trap_exec_reports_exec_line(self):
        text = '#!/bin/sh\nset -eu\ntmp=x\ntrap cleanup EXIT\n\n\nexec grype .'
        hits = [x for x in checks.shell_checks('a.sh', text) if x['rule_id'] == 'shell-trap-exec']
        self.assertEqual([7], [x['line'] for x in hits])
        continued = 'trap cleanup EXIT\nexec grype \\\n  --foo .'
        hits = [x for x in checks.shell_checks('a.sh', continued) if x['rule_id'] == 'shell-trap-exec']
        self.assertEqual([2], [x['line'] for x in hits])

    def test_shell_trap_exec_scan_is_bounded(self):
        # Behavior pins for the work bounds (the wall-clock cap that makes a
        # revert fail is test_blank_runs_and_long_lines_stay_linear). A long line of
        # unclosed substitution openers still flags instead of rescanning
        # quadratically, and collapse leaves it literal once its scan budget
        # is spent.
        unclosed = 'trap cleanup EXIT\nexec ' + '$(' * 20000
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh', unclosed)})
        self.assertEqual('$(' * 5000, checks.collapse_substitutions('$(' * 5000))
        self.assertEqual('__substitution__ x', checks.collapse_substitutions('$(echo hi) x'))
        # Mechanism pin (fails before the budget existed): with the scan
        # budget spent, even the balanced tail is left literal; unbudgeted
        # rescans eventually reach and collapse it.
        tail = '$(' * 6000 + '$(echo hi)'
        self.assertEqual(tail, checks.collapse_substitutions(tail))
        # Many redirection-only exec lines stay clean, and a command exec
        # after them still flags at its own line: joining a continuation
        # must not consume or misnumber the rest of the file.
        many = 'trap cleanup EXIT\n' + 'exec 3>&1\n' * 5000
        self.assertNotIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh', many)})
        hits = [x for x in checks.shell_checks('a.sh', many + 'exec grype .')
                if x['rule_id'] == 'shell-trap-exec']
        self.assertEqual([5002], [x['line'] for x in hits])
        # An exec statement over the length cap takes the unparsed-command
        # branch and flags, even if it is shaped like a redirection.
        oversized = 'trap cleanup EXIT\nexec >' + 'a' * 9000 + ' 2>&1'
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh', oversized)})
        # `trap - EXIT` only suppresses exec lines after it.
        self.assertIn('shell-trap-exec', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'trap cleanup EXIT\nexec tool\ntrap - EXIT')})
        # Hoisted once-only searches keep their per-match ordering: a
        # checksum verified after the first download does not cover a later
        # download, and `set -e` after a discarded-stderr line does not
        # suppress it (only the positions relative to each match may decide).
        downloads = ('curl -O https://h/main/a.tar.gz\nsha256sum -c sums\ntar xf a.tar.gz\n'
                     'curl -O https://h/main/b.tar.gz\ntar xf b.tar.gz')
        hits = [x for x in checks.shell_checks('a.sh', downloads)
                if x['rule_id'] == 'supply-download-integrity']
        self.assertEqual([4], [x['line'] for x in hits])
        self.assertIn('shell-tool-failure-hidden', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'gh api repos/x 2>/dev/null\nset -e')})
        self.assertNotIn('shell-tool-failure-hidden', {x['rule_id'] for x in checks.shell_checks('a.sh',
            'set -e\ngh api repos/x 2>/dev/null')})

    def test_blank_runs_and_long_lines_stay_linear(self):
        # Wall-clock caps with wide headroom: the linear shapes finish these
        # in ~0.01-0.1s, while the quadratic shapes they replaced took
        # 2-18s at these sizes (^\s* rescanning blank runs, ^.*X[^\n]*Y
        # backtracking on one long line, per-match newline recounts). A
        # revert to those shapes fails here instead of passing slowly.
        cases = [
            ('trap then blank run', lambda: checks.shell_checks(
                'a.sh', 'trap cleanup EXIT\n' + '\n' * 80000)),
            ('blank run then set -e', lambda: checks.shell_checks(
                'a.sh', '\n' * 80000 + 'set -e\ngh x 2>/dev/null')),
            ('action references on blank run', lambda: list(checks.action_references('\n' * 40000))),
            ('dockerfile without FROM after blank run', lambda: checks.supply_checks(
                'Dockerfile', '\n' * 40000 + 'RUN true')),
            ('long curl line without suffix', lambda: checks.shell_checks('a.sh', 'curl ' * 20000)),
            ('long gitleaks line without || true', lambda: checks.shell_checks('a.sh', 'gitleaks ' * 20000)),
            ('many uses lines', lambda: checks.supply_checks(
                'ci.yml', '- uses: a/b@v1\n' * 20000)),
            ('many capped-listing markers', lambda: checks.pagination_checks(
                'a.sh', '--limit 1\n' * 20000)),
            ('many tar excludes', lambda: checks.check_text(
                'a.ts', "'--exclude', 'x'\n" * 30000, {'ts'})),
            ('trap then long whitespace run', lambda: checks.shell_checks(
                'a.sh', 'trap' + ' ' * 40000 + 'x\nmktemp -d')),
            ('workflow if: repeats on one step line', lambda: checks.workflow_checks(
                'ci.yml', 'steps:\n  - run: lxc init x\n    if: ' + 'if: ' * 32000)),
            ('workflow grep repeats on one line', lambda: checks.workflow_checks(
                'ci.yml', 'run: echo ${{ toJSON(needs) }}\n' + 'grep ' * 32000)),
            ('workflow git show repeats on one line', lambda: checks.workflow_checks(
                'ci.yml', 'run: gitleaks detect\n' + 'git show ' * 64000)),
            ('select( repeats on one gh line', lambda: checks.shell_checks(
                'a.sh', 'gh api x .[0] ' + 'select(' * 64000)),
            ('dump inventory after long identifier', lambda: checks.repo_checks(
                FakeRepo({'crates/core/src/backup/dump_file.rs':
                          'const DUMP_VERSION: u32 = 6;\n' + 'a' * 80000 +
                          '\nconst DUMP_TABLES: &[&str] = &["users", "events"];'},
                         old={'crates/core/src/backup/dump_file.rs':
                              'const DUMP_VERSION: u32 = 5;\nconst DUMP_TABLES: &[&str] = &["users"];'}),
                ['crates/core/src/backup/dump_file.rs'], {'rust'}, None)),
            ('long python line', lambda: checks.pagination_checks('a.py', 'x ' * 60000)),
            ('many env reads on one ts line', lambda: checks.repo_checks(
                FakeRepo({'src/a.ts': 'process.env.X_KEY; ' * 60000,
                          'src/secret-env-redaction.ts': '["A_KEY"]'}),
                ['src/a.ts'], {'ts'}, None)),
        ]
        for label, run in cases:
            with self.subTest(label=label):
                started = time.perf_counter()
                run()
                self.assertLess(time.perf_counter() - started, 5.0, label)
        # Behavior pins at the same shapes: matches after blank runs are
        # still found and reported at their own line, not the run's start.
        self.assertEqual([(4, 'a/b@v1')], list(checks.action_references('\n\n\n- uses: a/b@v1')))
        hits = [x for x in checks.shell_checks('a.sh', 'trap cleanup EXIT\n' + '\n' * 1000 + 'exec tool')
                if x['rule_id'] == 'shell-trap-exec']
        self.assertEqual([1002], [x['line'] for x in hits])
        hits = [x for x in checks.supply_checks('Dockerfile', '\n\nFROM ubuntu:24.04')
                if x['rule_id'] == 'supply-container-digest']
        self.assertEqual([3], [x['line'] for x in hits])
        # Long-line two-token checks still fire when the second token does
        # follow, and stay silent when it only precedes the first token.
        self.assertIn('supply-download-integrity', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'x ' * 5000 + 'curl -O https://h/main/a.tgz')})
        self.assertNotIn('supply-download-integrity', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'echo /main/a.tgz\ncurl -O https://h/x')})
        self.assertIn('shell-pr-first-match', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'gh api x --jq .[0] ' * 500)})
        # A scanner masked by `||` at a line break still flags: shell
        # continues the command on the next line, and the text-wide regex
        # lines_matching replaced let its `\s*` cross that newline. Without
        # the trailing `||` the next line is a separate command: no flag.
        self.assertIn('shell-tool-failure-hidden', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'gitleaks detect --source . ||\n  true')})
        self.assertIn('shell-tool-failure-hidden', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'trivy fs . ||\n  exit 0')})
        self.assertNotIn('shell-tool-failure-hidden', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'gitleaks detect --source .\n  true')})
        # select() identity filtering still suppresses the first-match
        # finding, including when an earlier select() filters on something
        # else; a non-identity select() does not suppress it.
        self.assertNotIn('shell-pr-first-match', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'gh api x .[0] select(.state == 1) select(.head.sha == $s)')})
        self.assertIn('shell-pr-first-match', {x['rule_id'] for x in checks.shell_checks(
            'a.sh', 'gh api x .[0] select(.state == 1)')})


class FakeRepo:
    def __init__(self, files, old=None, main=None):
        self.data = files
        self.old = old or {}
        self.main = main or {}
        self.base = 'base'
    def files(self, ref=None):
        return list(self.main if ref == 'main' else self.data)
    def text(self, path, ref=None, missing=False):
        return (self.old if ref == self.base else self.data).get(path, '')


class RepoChecks(unittest.TestCase):
    def hits(self, files, changed, packs=ALL, old=None, main=None):
        return {x['rule_id'] for x in checks.repo_checks(FakeRepo(files, old, main), changed, packs, 'main')}

    def test_test_inventory_positive_negative(self):
        files = {'ops/productivity/test_cycle.py': 'def test_case(): pass', '.github/workflows/ci.yml': 'run: true'}
        self.assertIn('test-inventory-unreferenced', self.hits(files, ['ops/productivity/test_cycle.py']))
        files['.github/workflows/ci.yml'] = 'run: python3 -m unittest ops.productivity.test_cycle'
        self.assertNotIn('test-inventory-unreferenced', self.hits(files, ['ops/productivity/test_cycle.py']))

    def test_denylist_positive_negative(self):
        path = 'server/src/env.ts'
        files = {path: 'const token = process.env.FUTURE_API_KEY;', 'packages/adapter-utils/src/secret-env-redaction.ts': 'const keys = ["OLD_API_KEY"];'}
        self.assertIn('ts-secret-denylist-parity', self.hits(files, [path]))
        files['packages/adapter-utils/src/secret-env-redaction.ts'] += 'const next = "FUTURE_API_KEY";'
        self.assertNotIn('ts-secret-denylist-parity', self.hits(files, [path]))

    def test_parent_guard_positive_negative_public_and_late_parent(self):
        path = 'web/src/routes/admin/events/+page.server.ts'
        files = {path: 'export const load = async () => { const db = dbFor(); };',
                 'web/src/routes/admin/+layout.server.ts': 'export function load() { requireAdmin(); }'}
        self.assertIn('ts-kit-parent-guard', self.hits(files, [path]))
        files[path] = 'export const load = async ({parent}) => { await parent(); const db = dbFor(); };'
        self.assertNotIn('ts-kit-parent-guard', self.hits(files, [path]))
        files[path] = 'export const load = async ({parent}) => { const db = dbFor(); await parent(); };'
        self.assertIn('ts-kit-parent-guard', self.hits(files, [path]))
        del files['web/src/routes/admin/+layout.server.ts']
        self.assertNotIn('ts-kit-parent-guard', self.hits(files, [path]))

    def test_flash_positive_negative(self):
        path = 'web/src/routes/+page.server.ts'
        files = {path: 'const flash = readJoinResultCookie(cookies);'}
        self.assertIn('ts-flash-consumer', self.hits(files, [path]))
        files[path] += '\ndeleteJoinCookie(cookies);'
        self.assertNotIn('ts-flash-consumer', self.hits(files, [path]))

    def test_dump_positive_negative_and_inventory_content_not_line_count(self):
        path = 'crates/core/src/backup/dump_file.rs'
        old = {path: 'const DUMP_VERSION: u32 = 5;\nconst DUMP_TABLES: &[&str] = &["users"];'}
        files = {path: 'const DUMP_VERSION: u32 = 5;\nconst DUMP_TABLES: &[&str] = &["users", "events"];'}
        self.assertIn('rust-dump-version-probe', self.hits(files, [path], old=old))
        files[path] = files[path].replace('= 5;', '= 6;')
        self.assertIn('rust-dump-version-probe', self.hits(files, [path], old=old))
        self.assertNotIn('rust-dump-version-probe', self.hits(files, [path, 'scripts/rollback_readiness_probe.py'], old=old))

    def test_migrations_latest_main_and_namespaces(self):
        path = 'migrations/0002_new.sql'
        main = {'migrations/0002_existing.sql': '', 'other/migrations/0099_other.sql': ''}
        self.assertIn('migration-number-collision', self.hits({path: ''}, [path], main=main))
        self.assertNotIn('migration-number-collision', self.hits({'migrations/0003_new.sql': ''}, ['migrations/0003_new.sql'], main=main))
        self.assertIn('migration-number-order', self.hits({'migrations/0001_new.sql': ''}, ['migrations/0001_new.sql'], main=main))
        self.assertNotIn('migration-number-order', self.hits({'migrations/0003_new.sql': ''}, ['migrations/0003_new.sql'], main=main))

    def test_store_parity_positive_negative(self):
        path = 'src/store.ts'
        self.assertIn('store-parity-case', self.hits({path: 'update();'}, [path]))
        files = {path: 'update();', 'tests/store.test.ts': 'test("parity", () => {});'}
        self.assertNotIn('store-parity-case', self.hits(files, list(files)))


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Review Rules Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.write('keep.txt', 'original\n')
        self.commit('initial')
        self.base = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args, check=True):
        p = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.root), *args], capture_output=True, text=True)
        if check and p.returncode:
            self.fail('fixture git command failed: ' + p.stderr)
        return p.stdout

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def commit(self, message):
        self.git('add', '--all')
        self.git('commit', '-qm', message)

    def test_changed_names_spaces_leading_dash_and_rename(self):
        self.write('new space.py', 'x = 1')
        self.write('-option.py', 'x = 2')
        self.git('mv', 'keep.txt', 'renamed.txt')
        self.commit('files')
        repo = engine.Git(self.root)
        statuses = repo.statuses(self.base, repo.head)
        self.assertEqual('A', statuses['new space.py'])
        self.assertEqual('A', statuses['-option.py'])
        self.assertEqual('D', statuses['keep.txt'])
        self.assertEqual('original\n', repo.text('renamed.txt'))

    def test_missing_ref_never_clean(self):
        repo = engine.Git(self.root)
        with self.assertRaises(RuntimeError):
            repo.merge_base('does-not-exist', 'HEAD')

    def test_symlink_not_followed(self):
        (self.root / 'host.py').symlink_to('/etc/passwd')
        self.commit('symlink')
        with self.assertRaises(ValueError):
            engine.Git(self.root).text('host.py')

    def test_modify_delete_merge_positive_and_retained_negative(self):
        self.git('switch', '-qc', 'feature')
        self.write('keep.txt', 'important changes\n')
        self.commit('modify')
        self.git('switch', '-q', 'main')
        self.git('rm', 'keep.txt')
        self.commit('delete')
        main = self.git('rev-parse', 'HEAD').strip()
        self.git('switch', '-q', 'feature')
        self.git('merge', 'main', check=False)
        self.git('rm', 'keep.txt')
        self.commit('resolve delete')
        repo = engine.Git(self.root)
        repo.base = repo.merge_base(repo.head, main)
        self.assertEqual(['merge-modify-delete'], [x['rule_id'] for x in checks.merge_audit(repo)])
        self.git('reset', '--hard', 'HEAD^')
        self.git('merge', 'main', check=False)
        self.write('keep.txt', 'important changes\n')
        self.commit('resolve keep')
        repo = engine.Git(self.root)
        repo.base = repo.merge_base(repo.head, main)
        self.assertFalse(checks.merge_audit(repo))

    def test_invalid_packs_warn_exit_zero_with_artifact(self):
        output = self.root / 'report'
        with contextlib.redirect_stdout(io.StringIO()):
            code = engine.main(['--repo', str(self.root), '--base', self.base,
                                '--packs', '../../evil', '--output', str(output)])
        report = json.loads((output / 'review-rules.json').read_text())
        self.assertEqual(0, code)
        self.assertEqual('incomplete', report['status'])

    def test_missing_docker_warn_and_enforce_truth_table(self):
        self.write('code.py', 'x = 1')
        self.commit('source')
        for mode, expected in [('warn', 0), ('enforce', 1)]:
            output = self.root / ('report-' + mode)
            with patch.object(engine, 'docker_scan', side_effect=FileNotFoundError('missing')), contextlib.redirect_stdout(io.StringIO()):
                code = engine.main(['--repo', str(self.root), '--base', self.base, '--mode', mode,
                                    '--output', str(output)])
            self.assertEqual(expected, code)
            self.assertEqual('incomplete', json.loads((output / 'review-rules.json').read_text())['status'])

    def test_only_changed_files_are_staged_and_pr_ignores_are_ignored(self):
        self.write('untouched.rs', 'fn untouched() {}')
        self.commit('old source')
        base = self.git('rev-parse', 'HEAD').strip()
        self.write('src/new.ts', 'console.log(apiKey); // nosemgrep')
        self.write('.semgrepignore', '**')
        self.write('src/.semgrepignore', '**')
        self.write('src/deep/.semgrepignore', '**')
        self.commit('new source')
        captured = []
        def scanner(root, packs):
            captured.extend(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file())
            self.assertNotIn('**', (root / '.semgrepignore').read_text())
            return {'results': [], 'errors': [], 'paths': {'scanned': ['src/new.ts']}}
        with patch.object(engine, 'docker_scan', side_effect=scanner), contextlib.redirect_stdout(io.StringIO()):
            engine.main(['--repo', str(self.root), '--base', base, '--output', str(self.root / 'report')])
        self.assertEqual({'.semgrepignore', 'src/new.ts'}, set(captured))


class ScannerIntegrationTests(unittest.TestCase):
    # Opt-in real Docker scan; the normal unit tier remains hermetic.
    def setUp(self):
        GitTests.setUp(self)
        self.git('update-ref', 'refs/remotes/origin/main', self.base)

    git = GitTests.git
    write = GitTests.write
    commit = GitTests.commit

    @unittest.skipUnless('--semgrep' in sys.argv, 'requires digest-pinned scanner')
    def test_nested_ignores_cannot_hide_findings_in_pr_or_merge_group(self):
        self.write('src/deep/vulnerable.rs',
                   'fn allowed(host: &str) -> bool { host.starts_with("http://localhost") }\n')
        self.write('other.rs', 'fn independently_scanned() {}\n')
        self.write('.semgrepignore', '**\n')
        self.write('src/.semgrepignore', '**\n')
        self.write('src/deep/.semgrepignore', '**\n')
        self.commit('malicious nested ignores')
        for kind in ('pull_request', 'merge_group'):
            event = {'repository': {'default_branch': 'main'}}
            event[kind] = ({'base': {'sha': self.base}} if kind == 'pull_request'
                           else {'base_sha': self.base})
            event_path = self.root / 'event.json'
            event_path.write_text(json.dumps(event))
            output = self.root / ('report-' + kind)
            with self.subTest(event=kind), contextlib.redirect_stdout(io.StringIO()):
                code = engine.main(['--repo', str(self.root), '--event', str(event_path),
                                    '--packs', 'rust', '--output', str(output)])
                report = json.loads((output / 'review-rules.json').read_text())
                self.assertEqual(0, code)
                self.assertEqual('complete', report['status'], report['errors'])
                self.assertEqual(2, report['stats']['scanned_targets'])
                self.assertIn(('rust-localhost-prefix-allow', 'src/deep/vulnerable.rs', 1),
                              {(x['rule_id'], x['file'], x['line']) for x in report['findings']})

    def test_partial_omission_receipt_is_incomplete_for_both_events(self):
        self.write('src/hidden.rs', 'fn hidden() {}\n')
        self.write('other.rs', 'fn independently_scanned() {}\n')
        self.commit('two source files')
        result = {'results': [], 'errors': [], 'paths': {'scanned': ['other.rs']}}
        for kind in ('pull_request', 'merge_group'):
            event = {'repository': {'default_branch': 'main'}}
            event[kind] = ({'base': {'sha': self.base}} if kind == 'pull_request'
                           else {'base_sha': self.base})
            event_path = self.root / 'event.json'
            event_path.write_text(json.dumps(event))
            output = self.root / ('report-' + kind)
            with self.subTest(event=kind), patch.object(engine, 'docker_scan', return_value=result), contextlib.redirect_stdout(io.StringIO()):
                code = engine.main(['--repo', str(self.root), '--event', str(event_path),
                                    '--packs', 'rust', '--output', str(output)])
                report = json.loads((output / 'review-rules.json').read_text())
                self.assertEqual(0, code)
                self.assertEqual('incomplete', report['status'])
                self.assertEqual(1, report['stats']['scanned_targets'])
                self.assertTrue(report['errors'])


class ReceiptTests(unittest.TestCase):
    def test_publication_failure_stays_warn(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(engine, 'publish', side_effect=OSError('disk full')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, engine.main(['--repo', temp, '--base', 'bad', '--output', temp]))
                self.assertEqual(1, engine.main(['--repo', temp, '--base', 'bad', '--output', temp, '--mode', 'enforce']))

    def test_scanner_timeout_cleans_only_own_container(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[1] == 'run':
                raise subprocess.TimeoutExpired(command, 480)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(engine.subprocess, 'run', side_effect=run), self.assertRaises(subprocess.TimeoutExpired):
            engine.docker_scan(ROOT / 'tests/fixtures', {'rust'})
        self.assertEqual(['docker', 'rm', '--force'], calls[1][:3])
        self.assertEqual(calls[0][calls[0].index('--name') + 1], calls[1][3])

    def test_irrelevant_language_does_not_report_zero_work(self):
        repo = FakeRepo({'ci.yml': 'name: ci'})
        with patch.object(engine, 'docker_scan', side_effect=AssertionError('scanner should not run')):
            self.assertEqual(([], [], 0), engine.scan(repo, ['ci.yml'], {'rust'}))

    def test_svelte_and_workflow_extractions_are_in_coverage_manifest(self):
        repo = FakeRepo({'page.svelte': '<script>const n = 1;</script>',
                         '.github/workflows/ci.yml': 'steps:\n  - run: echo ok\n'})
        for packs, scanned in [({'ts'}, ['page.svelte.ts']),
                               ({'python-shell-ci'}, ['__review_rules_runs__/1-0.sh'])]:
            with self.subTest(packs=packs):
                result = {'results': [], 'errors': [], 'paths': {'scanned': scanned}}
                with patch.object(engine, 'docker_scan', return_value=result):
                    self.assertEqual(([], [], 1), engine.scan(repo, list(repo.data), packs))
                result['paths']['scanned'] = []
                with patch.object(engine, 'docker_scan', return_value=result):
                    self.assertTrue(engine.scan(repo, list(repo.data), packs)[1])

    def test_partial_target_omission_is_incomplete_even_with_scanner_work(self):
        repo = FakeRepo({'src/hidden.rs': 'fn hidden() {}', 'other.rs': 'fn other() {}'})
        result = {'results': [], 'errors': [], 'paths': {'scanned': ['other.rs']}}
        with patch.object(engine, 'docker_scan', return_value=result):
            hits, errors, count = engine.scan(repo, list(repo.data), {'rust'})
        self.assertEqual([], hits)
        self.assertEqual(1, count)
        self.assertTrue(errors, 'partial omission must not produce a complete receipt')

    def test_event_base_pr_and_merge_group(self):
        sha = 'a' * 40
        for event in [{'pull_request': {'base': {'sha': sha}}}, {'merge_group': {'base_sha': sha}}]:
            event['repository'] = {'default_branch': 'main'}
            self.assertEqual((sha, 'refs/remotes/origin/main'), engine.event_refs(event))
        with self.assertRaises(ValueError):
            engine.event_refs({'merge_group': {}, 'repository': {'default_branch': 'main'}})

    def test_warning_mode_and_promotion_truth_table(self):
        report = {'mode': 'warn', 'errors': ['scanner missing'], 'promoted_rules': ['id'],
                  'findings': [{'rule_id': 'id', 'severity': 'ERROR'}]}
        self.assertEqual(0, engine.exit_code(report))
        report.update(mode='enforce', errors=[], promoted_rules=[])
        self.assertEqual(0, engine.exit_code(report))
        report['promoted_rules'] = ['id']
        self.assertEqual(1, engine.exit_code(report))
        report['findings'][0]['severity'] = 'INFO'
        self.assertEqual(0, engine.exit_code(report))

    def test_annotation_escaping(self):
        self.assertEqual('evil%2Cline=1%0A%3A%3Aerror%3A%3Ax%25', engine.escaped('evil,line=1\n::error::x%', True))

    def test_all_script_rules_have_provenance(self):
        source = (ROOT / 'scripts/checks.py').read_text()
        ids = set(re.findall(r"finding\('([^']+)'", source))
        self.assertEqual(ids, set(checks.EXAMPLES))
        for examples in checks.EXAMPLES.values():
            self.assertTrue(examples)
            self.assertTrue(all('#' in example for example in examples))


def fixture_test() -> int:
    cases = (json.loads((ROOT / 'tests/cases.json').read_text())
             + json.loads((ROOT / 'tests/corpus-cases.json').read_text()))
    ids = set()
    for pack in engine.PACKS:
        for path in (ROOT / pack).glob('*.yml'):
            ids.update(re.findall(r'^\s*- id: (\S+)', path.read_text(), re.M))
            if not re.search(r'\bexamples:\s*\n\s*-', path.read_text()):
                raise AssertionError('rule missing metadata.examples: ' + str(path))
    if ids != {case['rule'] for case in cases}:
        raise AssertionError('rule inventory and fixture manifest differ')
    for ident in ids:
        if {case['positive'] for case in cases if case['rule'] == ident} != {True, False}:
            raise AssertionError('missing positive/negative fixture: ' + ident)
    # Deliberately vulnerable samples can be stored as *.fixture data, so
    # repository-wide security tools do not mistake them for shipped code. Only
    # this isolated test tree restores the intended language extension; the
    # vulnerable bytes and positive expectation remain unchanged.
    with tempfile.TemporaryDirectory(prefix='review-rules-fixtures-') as temp:
        staging = Path(temp)
        (staging / '.semgrepignore').write_text('# Include every fixture.\n')
        for case in cases:
            source = ROOT / 'tests' / case['file']
            target = staging / case['file'].removeprefix('fixtures/').removesuffix('.fixture')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        # `node --check` ignores --experimental-strip-types, so this is a
        # JavaScript-syntax check, not a TypeScript parse. It applies only to
        # fixtures authored as plain .ts files, which must therefore stay in
        # the JS subset (no TypeScript-only syntax); it catches defects such
        # as a raw newline inside a string literal. Corpus excerpts stored as
        # *.ts.fixture may use full TypeScript and are parsed by Semgrep.
        for case in cases:
            if not case['file'].endswith('.ts'):
                continue
            target = staging / case['file'].removeprefix('fixtures/')
            parsed = subprocess.run(['node', '--experimental-strip-types', '--check', str(target)],
                                    capture_output=True, text=True)
            if parsed.returncode:
                raise AssertionError(f'TypeScript fixture is not valid JavaScript syntax ({target.relative_to(staging)}): {parsed.stderr.strip()}')
        result = engine.docker_scan(staging, ALL)
    if result.get('errors'):
        raise AssertionError('Semgrep fixture parse errors: ' + str(len(result['errors'])))
    scanned = set(result.get('paths', {}).get('scanned', []))
    if not scanned:
        raise AssertionError('Semgrep scanned no fixtures')
    failures = []
    for case in cases:
        path = case['file'].removeprefix('fixtures/').removesuffix('.fixture')
        if path not in scanned:
            failures.append((case['rule'], 'fixture not scanned', path))
        hits = [hit for hit in result['results'] if hit['check_id'] == case['rule'] and hit['path'] == path]
        if bool(hits) != case['positive']:
            failures.append((case['rule'], 'positive' if case['positive'] else 'negative', path))
        if 'lines' in case and sorted({hit['start']['line'] for hit in hits}) != case['lines']:
            failures.append((case['rule'], 'exact finding location mismatch', path))
    if failures:
        for failure in failures:
            print('FAIL', *failure)
        return 1
    print(f'PASS: {len(ids)} rules; {len(cases)} positive/negative fixtures; {len(scanned)} targets; no parser errors.')
    return 0


if __name__ == '__main__':
    if '--semgrep' in sys.argv:
        fixture_status = fixture_test()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ScannerIntegrationTests)
        integration = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(fixture_status or int(not integration.wasSuccessful()))
    unittest.main()
