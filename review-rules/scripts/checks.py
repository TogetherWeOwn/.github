"""Deterministic, read-only advisory checks. Python standard library only.

Lexical checks are deliberately not advertised as YAML/TypeScript AST proofs.
All findings remain shadow until their IDs are promoted by a reviewed change.
Repository contents are data: never import, source or execute target code.
"""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import PurePosixPath

# Provenance is centralized so script findings have the same evidence contract as
# Semgrep metadata.examples. The runtime output never includes source snippets.
EXAMPLES = {
    'supply-action-pin': ['paperclip-ops-tooling#938 unpinned-checkout-org-reusable'],
    'supply-container-digest': ['paperclip-ops-tooling#737 sync-toolkit-unpinned-tarball'],
    'supply-download-integrity': ['paperclip#113 trivy-mutable-installer', '.github#16 binary-downloads-unverified'],
    'workflow-artifact-scrub': ['two-web-next#533 ledger-artifact-bypasses-token-scrub', 'nntunev2#11 artifact-index-html-password'],
    'workflow-pull-request-target': ['paperclip-ops-tooling#881 prt-ban-bypass'],
    'workflow-required-aggregator': ['nntunev2#3 ci-ok-cannot-go-red', 'two-web-next#737 skipped-ci-ok-opens-merge-window'],
    'security-suppression-change': ['nntunev2#3 gitleaksignore-bypass'],
    'security-suppression-source': ['nntunev2#3 gitleaks-ignore-path-pin-ineffective'],
    'security-committed-operator-switch': ['two-bot-next#947 two-eventsub-not-barred-from-toml'],
    'shell-provision-cleanup': ['paperclip-ops-tooling#766 no-builder-cleanup-trap'],
    'shell-trap-exec': ['kofra#647 run-grype-exec-skips-exit-trap'],
    'workflow-cancel-cleanup': ['paperclip-ops#14 cancel-strands-clone-no-always-cleanup'],
    'api-list-pagination': ['paperclip-ops-tooling#827 comments-checkruns-page1-only', 'paperclip-operator-toolkit#68 red-main-poll-single-page-failing-checks'],
    'shell-tool-failure-hidden': ['paperclip-ops-tooling#754 publish-duplicate-guard-noop', 'kofra#578 e2e-evidence-download-failure-masked'],
    'shell-scan-proves-work': ['paperclip-ops#6 gitleaks-bad-range-exits-0'],
    'shell-pr-first-match': ['paperclip-ops-tooling#754 mirror-lookup-no-head-pin'],
    'shell-cli-head-binding': ['paperclip-ops-tooling#752 automerge-match-head-commit-missing-sha'],
    'test-inventory-unreferenced': ['paperclip-ops-tooling#754 suite-not-in-ci', 'two-web-next#773 guard-test-not-wired-into-ci'],
    'ts-secret-denylist-parity': ['paperclip#49 secret-denylist-missing-xai-bedrock', 'paperclip#58 denylist-missing-managed-provider-key'],
    'ts-tar-exclude-flavor': ['paperclip#56 unanchored-target-node-modules-excludes', 'paperclip#56 bsdtar-dot-slash-exclude-unanchored'],
    'ts-kit-parent-guard': ['two-web-next#842 activity-log-load-skips-parent-guard', 'two-web-next#844 load-skips-parent-guard'],
    'ts-flash-consumer': ['two-web-next#827 home-kit-flash-never-consumed', 'two-web-next#849 join-flash-never-consumed'],
    'rust-dump-version-probe': ['two-bot-next#922 v5-inventory-grows-without-version-bump', 'two-bot-next#925 rollback-readiness-probe-rejects-v6'],
    'rust-sql-list-limit': ['nntunev2#28 unbounded-public-list'],
    'rust-duplicate-secret-codec': ['nntunev2#28 late-novel-handrolled-base64-duplicates-b64'],
    'migration-number-collision': ['nntunev2#73 migration-number-collision-main-0007', 'two-bot-next#924 migration-number-0431-collides-with-931'],
    'migration-number-order': ['nntunev2#73 migration-number-collision'],
    'store-parity-case': ['kofra#639 fake-d1-fence-divergence', 'kofra#630 update-recipient-order-diverges'],
    'merge-modify-delete': ['paperclip-ops-tooling#737 merge-drops-703-auth-files'],
}


def finding(rule: str, path: str, line: int, message: str, severity: str = 'WARNING') -> dict:
    return {'rule_id': rule, 'file': path, 'line': max(1, line),
            'severity': severity, 'message': message}


def line_at(text: str, offset: int) -> int:
    return text.count('\n', 0, offset) + 1


def workflow_steps(text: str) -> list[tuple[int, str]]:
    """Split block-style steps; flow-style workflows remain a reported limitation.

    This does not claim to parse YAML. Supply pins are scanned independently,
    including flow mappings, quoted keys and folded uses values.
    """
    lines = text.splitlines(keepends=True)
    starts = [(i, len(m[1])) for i, line in enumerate(lines)
              if (m := re.match(r'^(\s*)-\s+(?:name|id|uses|run|if|shell|working-directory):', line))]
    result = []
    for i, indent in starts:
        end = i + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and not line.lstrip().startswith('#'):
                depth = len(line) - len(line.lstrip())
                if depth <= indent:
                    break
            end += 1
        result.append((i + 1, ''.join(lines[i:end])))
    return result


def workflow_runs(text: str) -> list[tuple[int, str]]:
    """Extract block-style run bodies; preserve original line numbers for findings."""
    lines = text.splitlines(keepends=True)
    result = []
    for i, line in enumerate(lines):
        match = re.match(r'^(\s*)(?:-\s+)?run:\s*(.*)', line)
        if not match:
            continue
        # For '- run:' the key itself is two spaces deeper than the dash.
        indent = len(match[1]) + (2 if line.lstrip().startswith('- ') else 0)
        value = match[2].strip()
        if value and value[0] not in '|>':
            result.append((i + 1, value.strip("\"'")))
            continue
        body = []
        j = i + 1
        while j < len(lines):
            if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= indent:
                break
            body.append(lines[j])
            j += 1
        nonempty = [len(s) - len(s.lstrip()) for s in body if s.strip()]
        trim = min(nonempty, default=0)
        result.append((i + 2, ''.join(s[trim:] if s.strip() else '\n' for s in body)))
    return result


def action_references(text: str):
    """Read common block/flow uses scalars without interpreting expressions."""
    pattern = r'''(?m)(?:^\s*(?:-\s*)?|[,{]\s*)["']?uses["']?\s*:\s*(?:[>|][-+]?\s*\n\s*)?["']?([^\s"',}#]+)'''
    for match in re.finditer(pattern, text):
        yield line_at(text, match.start()), match[1]


def pinned_action(value: str) -> bool:
    if value.startswith('./'):
        return True
    if value.startswith('docker://'):
        return bool(re.fullmatch(r'docker://[^@]+@sha256:[a-f0-9]{64}', value))
    return bool(re.fullmatch(r'[^@\s]+@[a-fA-F0-9]{40}', value))


def supply_checks(path: str, text: str) -> list[dict]:
    out = []
    suffix = PurePosixPath(path).suffix
    if suffix in {'.yml', '.yaml'}:
        for line, value in action_references(text):
            if not pinned_action(value):
                out.append(finding('supply-action-pin', path, line,
                    'PYCI-02a/SEC-AUTH-04: external uses must end in exactly 40 hex characters; docker actions need a sha256 digest.', 'ERROR'))
        for match in re.finditer(r'''(?m)^[ \t]*(?:-[ \t]*)?(?:image|container):[ \t]*["']?([^\s"'#]+)''', text):
            value = match[1]
            if value in {'|', '>', '{'} or value.startswith('${{'):
                continue  # expressions need caller-level provenance, not a guessed pin.
            if not re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', value):
                out.append(finding('supply-container-digest', path, line_at(text, match.start()),
                    'SEC-AUTH-04: container image tag is mutable; pin its sha256 digest.', 'ERROR'))
    if PurePosixPath(path).name.lower().startswith('dockerfile') or suffix == '.dockerfile':
        stages = set()
        for match in re.finditer(r'(?im)^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?', text):
            image, stage = match[1], match[2]
            if image.lower() != 'scratch' and image.lower() not in stages and not re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', image):
                out.append(finding('supply-container-digest', path, line_at(text, match.start()),
                    'SEC-AUTH-04: external FROM needs a sha256 digest (ARG-expanded images require explicit provenance).', 'ERROR'))
            if stage:
                stages.add(stage.lower())
    return out


def shell_checks(path: str, text: str) -> list[dict]:
    out = []
    # Strip comment-only lines, not quoted shell data. These checks are hints;
    # no regex is presented as a control-flow proof or a checksum verifier.
    code = '\n'.join('' if l.lstrip().startswith('#') else l for l in text.splitlines())
    for match in re.finditer(r'(?m)^.*\b(?:curl|wget)\b[^\n]*(?:/main/|/master/|releases/download/|\$\{?TAG\}?|\.tar\.gz|\.tgz)[^\n]*', code):
        after = code[match.start():]
        checked = re.search(r'\bsha256sum\s+(?:--check|-c)\b', after)
        execute = re.search(r'(?:\|\s*(?:sh|bash|tar)\b|\b(?:tar\s|sudo\s+(?:mv|install)|chmod\s+\+x|\./install))', after)
        if not checked or (execute and execute.start() < checked.start()):
            out.append(finding('supply-download-integrity', path, line_at(code, match.start()),
                'PYCI-02b/SEC-AUTH-04: archive/installer needs immutable provenance and checksum verification before extraction/execution in this step.', 'ERROR'))
    trap = re.search(r'(?m)^\s*trap\s+[^\n]*\bEXIT\b', code)
    provision = re.search(r'\b(?:mktemp|lxc\s+(?:init|launch)|pscale\s+branch\s+create)\b', code)
    if provision and not trap and not re.search(r'#\s*sourced-cleanup:\s*\S+', text):
        out.append(finding('shell-provision-cleanup', path, line_at(code, provision.start()),
            'PYCI-11: provisioned resource has no same-file EXIT cleanup; verify caller-owned cleanup explicitly.'))
    if trap:
        after_trap = code[trap.end():]
        for execute in re.finditer(r'(?m)^\s*exec\b[^\n]*', after_trap):
            try:
                words = shlex.split(re.sub(r'^\s*exec\b', '', execute[0], count=1), comments=True)
            except ValueError:
                words = ['<unparsed>']
            has_command = False
            options_done = False
            while words:
                redirection = re.match(
                    r'^(?:\d+|\{[A-Za-z_]\w*\})?(?:&>>|&>|>>|>\||>&|<&|<<|<>|>|<)(.*)$', words[0])
                if redirection:
                    words.pop(0)
                    if not redirection[1] and words:
                        words.pop(0)
                    continue
                if options_done:
                    has_command = words[0] not in {'&&', '||', ';', '|', '&'}
                    break
                if words[0] == '--':
                    words.pop(0)
                    options_done = True
                elif words[0] in {'-c', '-l'}:
                    words.pop(0)
                elif words[0] == '-a':
                    words.pop(0)
                    if words:
                        words.pop(0)
                elif words[0].startswith('-'):
                    break
                else:
                    has_command = words[0] not in {'&&', '||', ';', '|', '&'}
                    break
            if not has_command:
                continue
            if not re.search(r'(?m)^\s*trap\s+-\s+EXIT\b', after_trap[:execute.start()]):
                out.append(finding('shell-trap-exec', path, line_at(code, trap.end() + execute.start()),
                    'PYCI-11: top-level exec replaces the shell and skips EXIT trap; clean explicitly or avoid exec.', 'ERROR'))
                break
    for match in re.finditer(r'(?m)^.*\b(?:gh|git|gitleaks|curl)\b[^\n]*2>\s*/dev/null[^\n]*', code):
        nearby = code[match.start():].splitlines()[:3]
        handled = re.search(r'\brc=\$\?|\|\|\s*(?:exit\s+[1-9]|return\s+[1-9])', '\n'.join(nearby))
        errexit = re.search(r'(?m)^\s*set\s+-[a-z]*e', code[:match.start()])
        if not handled and not errexit:
            out.append(finding('shell-tool-failure-hidden', path, line_at(code, match.start()),
                'PYCI-07a: forge/tool stderr is discarded without a visible status check; tool failure is not a clean result.'))
    for match in re.finditer(r'(?m)^.*\b(?:gh\s+run\s+download|gitleaks|semgrep|trivy)\b[^\n]*\|\|\s*(?:true|exit\s+0)\b', code):
        out.append(finding('shell-tool-failure-hidden', path, line_at(code, match.start()),
            'PYCI-07a: scan/download failure is converted to success; report incomplete work explicitly.', 'ERROR'))
    for match in re.finditer(r'\bgitleaks\s+(?:git|detect)\b', code):
        if not re.search(r'(?:commits scanned|scanned[_-]?(?:files|commits)|scan[_-]?count)', code):
            out.append(finding('shell-scan-proves-work', path, line_at(code, match.start()),
                'PYCI-07b: scanner success alone does not prove a nonempty range; assert a positive scanned count.'))
    for match in re.finditer(r'(?m)^.*\bgh\s+(?:api|pr\s+list)\b[^\n]*\.\[0\][^\n]*', code):
        if not re.search(r'select\([^)]*\.(?:head\.sha|number)\s*==', match[0]):
            out.append(finding('shell-pr-first-match', path, line_at(code, match.start()),
                'PYCI-05a: first PR result is not an identity match; select full PR/head identity before indexing.'))
    for match in re.finditer(r'--match-head-commit\b([^\n]*)', code):
        tail = match[1].strip()
        if not tail or tail.startswith(('--', '&&', '||', ';')):
            out.append(finding('shell-cli-head-binding', path, line_at(code, match.start()),
                'PYCI-13: --match-head-commit is missing its evaluated SHA argument.', 'ERROR'))
    return out


def pagination_checks(path: str, text: str) -> list[dict]:
    """Function-scoped for Python, whole-step/file advisory elsewhere."""
    regions = [(1, text)]
    if path.endswith('.py'):
        try:
            tree = ast.parse(text)
            lines = text.splitlines()
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            regions = [(n.lineno, '\n'.join(lines[n.lineno - 1:n.end_lineno])) for n in functions] or regions
        except (SyntaxError, RecursionError):
            pass
    out = []
    for first, body in regions:
        for match in re.finditer(r'(?:--limit\s+\d+|per_page\s*[=:]\s*\d+|pulls\[0\])', body):
            if re.search(r'(--paginate|hasNextPage|rel=["\']next|next_link|capped-intent:\s*\S)', body):
                continue
            out.append(finding('api-list-pagination', path, first + line_at(body, match.start()) - 1,
                'PYCI-06: capped listing has no visible pagination in this function/step; add a page-2 fixture or document newest-N intent.'))
    return out


def workflow_checks(path: str, text: str) -> list[dict]:
    out = []
    # Trigger recognition includes quoted on, flow list and zero-indent list.
    # Restrict to workflow files; prose mentions and run-body strings are not triggers.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r'''\s*["']?on["']?\s*:''', line):
            indent = len(line) - len(line.lstrip())
            block = [line]
            for following in lines[i + 1:]:
                depth = len(following) - len(following.lstrip())
                if following.strip() and depth <= indent and not following.lstrip().startswith(('-', '#')):
                    break
                block.append(following)
            if re.search(r'\bpull_request_target\b', '\n'.join(block)):
                out.append(finding('workflow-pull-request-target', path, i + 1,
                    'SEC-AUTH-07: privileged pull_request_target trigger requires a separate trust review; suppression comments do not make it safe.', 'ERROR'))
    steps = workflow_steps(text)
    for first, block in steps:
        if 'actions/upload-artifact@' not in block:
            continue
        sensitive = re.search(r'(playwright-report|test-results|staging-journeys|stdout|stderr|(?:^|/)logs?(?:/|\s|$))', block)
        gated = re.search(r"(?:steps|needs)\.[\w-]+\.outputs\.swept\s*==\s*'true'", block)
        if sensitive and not gated:
            out.append(finding('workflow-artifact-scrub', path, first,
                'PYCI-04b/SEC-AUTH-03A: sensitive report upload has no swept==true gate; green and failed runs both need scrubbing (including embedded index.html).', 'ERROR'))
    if re.search(r'\b(?:lxc\s+(?:init|launch)|pscale\s+branch\s+create)\b', text):
        cleanups = [b for _, b in steps if re.search(r'if:\s*.*(?:always\(\)|cancelled\(\))', b)
                    and re.search(r'\b(?:delete|destroy)\b', b)]
        if not cleanups:
            out.append(finding('workflow-cancel-cleanup', path, 1,
                'PYCI-11: external resource has no visible owning-step always/cancelled cleanup; a grandchild trap is insufficient.'))
    if re.search(r'toJSON\(needs\)', text) and re.search(r'''grep[^\n]*["']result["']:["']failure''', text):
        out.append(finding('workflow-required-aggregator', path, 1,
            'PYCI-12: compact-text grep does not parse pretty toJSON(needs); simulate a failed dependency with a JSON parser.', 'ERROR'))
    if 'gitleaks' in text:
        for first, script in workflow_runs(text):
            if not re.search(r'\bgitleaks\s+(?:git|detect|dir)\b', script):
                continue
            removes = re.search(r'rm\s+-f\s+(?:--\s+)?\.?/?\.gitleaksignore', text)
            base = re.search(r'git\s+show\s+[^\n]*:\.gitleaksignore', text)
            if not removes or not base:
                out.append(finding('security-suppression-source', path, first,
                    'SEC-AUTH-01: verify working-tree ignore removal and a base-blob-only suppression path; the ignore-path flag alone is not proof.'))
    return out


def check_text(path: str, text: str, packs: set[str]) -> list[dict]:
    out = []
    suffix = PurePosixPath(path).suffix
    workflow = suffix in {'.yml', '.yaml'} and ('.github/workflows/' in path or '/sdlc/' in path)
    shell = suffix in {'.sh', '.bash'} or text.startswith(('#!/bin/bash', '#!/usr/bin/env bash', '#!/bin/sh'))
    supply = bool(packs & {'security', 'python-shell-ci'})
    if supply:
        out.extend(supply_checks(path, text))
        if shell:
            out.extend(shell_checks(path, text))
        if workflow:
            out.extend(workflow_checks(path, text))
            for first, script in workflow_runs(text):
                for item in shell_checks(path, script) + pagination_checks(path, script):
                    item['line'] += first - 1
                    out.append(item)
        elif shell or suffix == '.py':
            out.extend(pagination_checks(path, text))
    if 'security' in packs and PurePosixPath(path).name in {'.gitleaksignore', 'gitleaks.toml', '.gitleaks.toml', '.secretlintrc'}:
        out.append(finding('security-suppression-change', path, 1,
            'SEC-AUTH-01: suppression changed; retain base-ref policy and run a hostile-ignore canary before approval.'))
    if 'security' in packs and suffix == '.toml':
        for match in re.finditer(r'(?m)^\s*TWO_EVENTSUB\s*=\s*["\']?1', text):
            out.append(finding('security-committed-operator-switch', path, line_at(text, match.start()),
                'SEC-AUTH-07: operator-only TWO_EVENTSUB switch is committed in TOML; keep it runtime-controlled.', 'ERROR'))
    if 'ts' in packs and suffix in {'.ts', '.js', '.mjs'}:
        for match in re.finditer(r'''["']--exclude["']\s*,\s*["']([^"']+)["']''', text):
            if not ('tarExcludeFor(' in text or 'find-prune' in text):
                out.append(finding('ts-tar-exclude-flavor', path, line_at(text, match.start()),
                    'TS-003a: tar exclusion is not flavor-aware; ./ anchors GNU but not bsdtar. Prove nested tracked files survive both.'))
    if 'rust' in packs and suffix == '.rs':
        if re.search(r'fn\s+(?:encode|decode)_(?:b64|base64|b64url)\b', text) and PurePosixPath(path).stem not in {'b64', 'base64', 'codec'}:
            out.append(finding('rust-duplicate-secret-codec', path, 1,
                'RUST-14b: new local base64 codec may diverge from the shared strict codec; test padding/alphabet rejection.'))
        if path.endswith('workers/api/src/library.rs'):
            for match in re.finditer(r'(?s)fn\s+\w+_statement\b.{0,800}', text):
                body = match[0]
                if re.search(r'\bSELECT\b', body) and not re.search(r'\bLIMIT\b|unpaged-by-design:', body):
                    out.append(finding('rust-sql-list-limit', path, line_at(text, match.start()),
                        'RUST-04: statement builder has no visible LIMIT; execution/EXPLAIN and paged fixtures decide.'))
    return out


def repo_checks(repo, changed: list[str], packs: set[str], migration_ref: str | None) -> list[dict]:
    """Cross-file checks over Git blobs, never files supplied by the checkout."""
    out = []
    files = repo.files()
    texts = {}

    def read(path):
        if path not in texts:
            texts[path] = repo.text(path)
        return texts[path]

    if packs & {'ts', 'python-shell-ci'}:
        # A mention is only a candidate referrer, not proof of test collection.
        runners = [p for p in files if p.startswith('.github/workflows/') or
                   (p.endswith(('.sh', '.json', '.mjs', '.js', '.ts', 'Makefile')) and
                    any(x in p.lower() for x in ('ci/', 'verify', 'package.json', 'vitest', 'makefile')))]
        references = '\n'.join(read(p) for p in runners if p not in changed or not is_test(p))
        for path in changed:
            if not is_test(path) or any(p in {'fixtures', 'fixture', 'testdata', 'helpers'} for p in PurePosixPath(path).parts) or '.manual.' in path:
                continue
            stem = PurePosixPath(path).stem
            module = path.removesuffix('.py').replace('/', '.')
            explicit = any(s in references for s in (path, PurePosixPath(path).name, stem, module))
            discovery = ('unittest discover' in references and path.endswith('.py')) or ('pytest' in references and path.endswith('.py'))
            if not explicit and not discovery:
                out.append(finding('test-inventory-unreferenced', path, 1,
                    'PYCI-01/TS-005: no lexical CI referrer found; attach actual runner collection output (glob discovery may be valid).'))
    if 'ts' in packs:
        denyfiles = [p for p in files if p.endswith('secret-env-redaction.ts')]
        if denyfiles:
            entries = set(re.findall(r'''["']([A-Z][A-Z0-9_]+)["']''', '\n'.join(read(p) for p in denyfiles)))
            for path in changed:
                if not path.endswith(('.ts', '.js', '.mjs', '.svelte')) or is_test(path):
                    continue
                for match in re.finditer(r'''(?:process\.env\.|import\.meta\.env\.)([A-Z][A-Z0-9_]*)|(?:process\.env\[|Deno\.env\.get\()["']([A-Z][A-Z0-9_]*)["']''', read(path)):
                    name = match[1] or match[2]
                    if re.search(r'(?:KEY|TOKEN|SECRET|PASSWORD)', name) and name not in entries:
                        out.append(finding('ts-secret-denylist-parity', path, line_at(read(path), match.start()),
                            'TS-001/SEC-AUTH-08: credential env read is absent from the literal redaction inventory; verify dynamic injection parity.', 'ERROR'))
        for path in changed:
            if not path.endswith('+page.server.ts'):
                continue
            page = PurePosixPath(path)
            layouts = [str(parent / '+layout.server.ts') for parent in page.parents if str(parent) != '.']
            guarded = any(layout in files and re.search(r'\b(?:redirect|error|require\w*|guard\w*)\s*\(', read(layout)) for layout in layouts)
            body = read(path)
            db = re.search(r'\b(?:dbFor|query|list[A-Z]\w*)\s*\(', body)
            parent = re.search(r'await\s+parent\s*\(', body)
            if guarded and db and (not parent or parent.start() > db.start()):
                out.append(finding('ts-kit-parent-guard', path, line_at(body, db.start()),
                    'TS-010: guarded route has a DB call before visible await parent(); verify real load ordering with a deferred-parent test.', 'ERROR'))
            for match in re.finditer(r'\b(?:read|get)(\w*Flash|JoinResult)Cookie\s*\(', body):
                if not re.search(r'(?:delete\w*Cookie|consume\w*Flash|apply\w*FlashConsumption)\s*\(', body):
                    out.append(finding('ts-flash-consumer', path, line_at(body, match.start()),
                        'TS-007b: one-shot flash read has no visible consumer; prove consume-once on this served route.'))
    if 'rust' in packs:
        dump = 'crates/core/src/backup/dump_file.rs'
        if dump in changed:
            old, new = repo.text(dump, repo.base, missing=True), read(dump)
            inventory = lambda s: set(re.findall(r'''["']([a-z][a-z0-9_]*)["']''', '\n'.join(re.findall(r'(?s)(?:DUMP_TABLES|\w+_ADDED_TABLES)[^=]*=\s*&?\[(.*?)\]', s))))
            version = lambda s: re.search(r'DUMP_VERSION\s*:\s*\w+\s*=\s*(\d+)', s)
            before, after = version(old), version(new)
            if inventory(new) - inventory(old):
                bumped = before and after and int(after[1]) > int(before[1])
                probe_changed = any('rollback_readiness_probe' in p for p in changed)
                if not bumped or not probe_changed:
                    out.append(finding('rust-dump-version-probe', dump, 1,
                        'RUST-03: dump inventory grew without both a numeric version bump and probe update; old-fixture restore and export/import round-trip still required.', 'ERROR'))
    if packs & {'rust', 'php'}:
        # Numeric SQL migrations have separate namespaces per directory. Laravel
        # timestamp migrations are deliberately not conflated with NNNN files.
        def migrations(paths):
            result = {}
            for path in paths:
                p = PurePosixPath(path)
                match = re.fullmatch(r'(\d{4})_[^/]+\.sql', p.name)
                if match and any('migration' in part for part in p.parts):
                    result[path] = (str(p.parent), int(match[1]))
            return result
        current = migrations(files)
        baseline = migrations(repo.files(migration_ref)) if migration_ref else {}
        for path in changed:
            if path not in current:
                continue
            namespace, number = current[path]
            conflicts = [p for p, pair in {**baseline, **current}.items() if pair == (namespace, number) and p != path]
            if conflicts:
                out.append(finding('migration-number-collision', path, 1,
                    'R2a: migration number collides in this directory against the checked-out/default-branch inventory.', 'ERROR'))
            elif path not in baseline and baseline and number <= max((n for ns, n in baseline.values() if ns == namespace), default=-1):
                out.append(finding('migration-number-order', path, 1,
                    'R2a: new migration does not follow the default-branch maximum in its directory; renumber and update the manifest.', 'ERROR'))
    if 'php' in packs:
        parity_changed = any(is_test(p) and 'parity' in (p + read(p)).lower() for p in changed)
        for path in changed:
            if 'store' in PurePosixPath(path).stem.lower() and not is_test(path) and path.endswith(('.ts', '.php')) and not parity_changed:
                out.append(finding('store-parity-case', path, 1,
                    'R7: store changed with no visible parity-case update; run identical return/order/error vectors against fake and production backends.'))
    return out


def is_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith('test_') or any(x in name for x in ('.test.', '.spec.', 'selftest', 'Test.php'))


def merge_audit(repo) -> list[dict]:
    """Find two-parent modify/delete resolutions that discarded modified blobs.

    This is Git evidence, not a ban on normal deletions. Octopus merges are
    reported as incomplete by the runner instead of silently declared safe.
    """
    out = []
    for merge in repo.merges():
        parents = repo.parents(merge)
        if len(parents) != 2:
            raise ValueError('octopus merge requires a separate merge-integrity audit')
        left, right = parents
        ancestor = repo.merge_base(left, right)
        a, b = repo.statuses(ancestor, left), repo.statuses(ancestor, right)
        survivors = set(repo.files(merge))
        for path in set(a) & set(b):
            if {a[path], b[path]} == {'D', 'M'} and path not in survivors:
                out.append(finding('merge-modify-delete', path, 1,
                    'TS-011: merge resolved a proven modify/delete conflict by deleting the modified file; recover or explicitly justify the loss.', 'ERROR'))
    return out
