#!/usr/bin/env python3
"""Run central, digest-pinned shadow rules over changed Git blobs only.

Usage: python3 -I /trusted/review-rules/scripts/run.py --repo /checkout \
  --event "$GITHUB_EVENT_PATH" --packs rust,security --mode warn --output /tmp/report

Never executes repository code, loads repository configuration, prints source
snippets, or mounts credentials/.git/the Docker socket inside the scanner.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import uuid

# Match the shipped pack languages; YAML-only changes in a Rust-only caller are
# not a zero-work scanner failure. Deterministic checks still inspect all paths.
EXTENSIONS = {
    'rust': {'.rs'},
    'ts': {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.svelte'},
    'python-shell-ci': {'.py', '.sh', '.bash'},
    'php': {'.php', '.ts', '.tsx', '.js', '.jsx', '.py', '.sh', '.bash', '.rs', '.sql'},
    'security': {'.rs', '.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.svelte', '.yml', '.yaml'},
}

RULES = Path(__file__).resolve().parents[1]
IMAGE = 'semgrep/semgrep@sha256:fca58525689355641019c05ab49dcc5bc3a1eb7e044f35014ee39594b5aa4fc1'
PACKS = ('rust', 'ts', 'python-shell-ci', 'php', 'security')
MAX_BLOB = 2 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
spec = importlib.util.spec_from_file_location('review_rules_checks', RULES / 'scripts/checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class Git:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.base = ''
        self.head = self.rev('HEAD')
        self._trees = {}
        self._text = {}

    def git(self, *args: str) -> bytes:
        # No shell, diff drivers, hooks or textconv. References are resolved to
        # commit hashes before use. Never fall back to main or an empty diff.
        command = ['git', '--no-pager', '-c', 'core.hooksPath=/dev/null',
                   '-c', f'safe.directory={self.root}', '-C', str(self.root), *args]
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
        if result.returncode:
            raise RuntimeError('git operation failed; review is incomplete')
        return result.stdout

    def rev(self, ref: str) -> str:
        value = self.git('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()
        if not re.fullmatch(r'[0-9a-f]{40,64}', value):
            raise ValueError('git returned an invalid commit identity')
        return value

    def merge_base(self, left: str, right: str) -> str:
        return self.git('merge-base', self.rev(left), self.rev(right)).decode().strip()

    def tree(self, ref: str | None = None) -> dict:
        commit = self.rev(ref) if ref else self.head
        if commit not in self._trees:
            result = {}
            for item in self.git('ls-tree', '-r', '-z', '--full-tree', commit).split(b'\0'):
                if not item:
                    continue
                meta, raw_path = item.split(b'\t', 1)
                mode, kind, oid = meta.decode().split()
                path = raw_path.decode('utf-8', errors='strict')
                parts = PurePosixPath(path)
                if parts.is_absolute() or '..' in parts.parts or '.git' in parts.parts or '\\' in path:
                    raise ValueError('unsupported Git path; review is incomplete')
                result[path] = (mode, kind, oid)
            self._trees[commit] = result
        return self._trees[commit]

    def files(self, ref: str | None = None) -> list[str]:
        return list(self.tree(ref))

    def text(self, path: str, ref: str | None = None, missing: bool = False) -> str:
        commit = self.rev(ref) if ref else self.head
        key = (commit, path)
        if key not in self._text:
            item = self.tree(commit).get(path)
            if item is None and missing:
                return ''
            if item is None:
                raise ValueError('expected Git blob missing')
            mode, kind, oid = item
            if mode not in {'100644', '100755'} or kind != 'blob':
                raise ValueError('symlink or submodule requires separate review')
            size = int(self.git('cat-file', '-s', oid))
            if size > MAX_BLOB:
                raise ValueError('blob exceeds review size budget')
            raw = self.git('cat-file', 'blob', oid)
            if b'\0' in raw:
                self._text[key] = ''  # Binary content is not a static-source target.
            else:
                self._text[key] = raw.decode('utf-8', errors='strict')
        return self._text[key]

    def statuses(self, base: str, head: str) -> dict[str, str]:
        fields = self.git('diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                          '--name-status', '-z', self.rev(base), self.rev(head), '--').split(b'\0')
        return {fields[i + 1].decode('utf-8'): fields[i].decode('ascii')
                for i in range(0, len(fields) - 1, 2)}

    def merges(self) -> list[str]:
        return self.git('rev-list', '--merges', f'{self.base}..{self.head}').decode().split()

    def parents(self, commit: str) -> list[str]:
        return self.git('rev-list', '--parents', '-n', '1', self.rev(commit)).decode().split()[1:]


def event_refs(event: dict) -> tuple[str, str]:
    """Use actual event base for PR/merge queue; missing base is an error."""
    if 'merge_group' in event:
        base = event['merge_group'].get('base_sha', '')
    else:
        base = event.get('pull_request', {}).get('base', {}).get('sha', '')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', base):
        raise ValueError('PR/merge_group event has no valid base SHA')
    default = event.get('repository', {}).get('default_branch', '')
    if not default or default.startswith('-') or any(c in default for c in ('\n', '\r', '\0')):
        raise ValueError('event has no valid default branch')
    return base, f'refs/remotes/origin/{default}'


def svelte_scripts(text: str) -> str:
    """Keep only script bodies and their original line numbers.

    This is intentionally not a Svelte compiler. Unterminated scripts fail
    incomplete; templates/markup are not claimed as JavaScript coverage.
    """
    opens = list(re.finditer(r'<script\b[^>]*>', text, flags=re.I))
    blocks = list(re.finditer(r'<script\b[^>]*>(.*?)</script\s*>', text, flags=re.I | re.S))
    if len(opens) != len(blocks):
        raise ValueError('unterminated Svelte script')
    out = ['\n' if c == '\n' else ' ' for c in text]
    for match in blocks:
        out[match.start(1):match.end(1)] = match[1]
    return ''.join(out)


def docker_scan(root: Path, packs: set[str]) -> dict:
    name = 'review-rules-' + uuid.uuid4().hex
    command = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--read-only',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--memory', '2g', '--cpus', '2', '--pids-limit', '256',
               '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,mode=1777',
               '-e', 'HOME=/tmp', '-e', 'SEMGREP_SEND_METRICS=off',
               '-e', 'SEMGREP_ENABLE_VERSION_CHECK=0',
               '--mount', f'type=bind,source={RULES},target=/rules,readonly',
               '--mount', f'type=bind,source={root},target=/scan,readonly', '-w', '/scan', IMAGE,
               'semgrep', 'scan', '--metrics', 'off', '--disable-version-check', '--no-rewrite-rule-ids',
               '--disable-nosem', '--no-git-ignore', '--json', '--jobs', '2',
               '--max-memory', '1024', '--timeout', '10', '--timeout-threshold', '3',
               '--max-target-bytes', str(MAX_BLOB)]
    for pack in sorted(packs):
        command += ['--config', f'/rules/{pack}']
    command += ['.']
    try:
        result = subprocess.run(command, capture_output=True, timeout=480, check=False)
    except subprocess.TimeoutExpired:
        # Killing the Docker CLI does not stop its container. Only remove the
        # uniquely named scanner belonging to this invocation, never peers.
        subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=20, check=False)
        raise
    if result.returncode not in {0, 1}:
        raise RuntimeError('Semgrep failed; review is incomplete')
    # Raw scanner output includes source lines and metavariables. Never persist
    # or echo it: publish only rule-authored messages and sanitized locations.
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get('results'), list):
        raise ValueError('invalid Semgrep report')
    return data


def scan(repo: Git, changed: list[str], packs: set[str]) -> tuple[list[dict], list[str], int]:
    findings, errors = [], []
    mappings = {}
    total = 0
    with tempfile.TemporaryDirectory(prefix='review-rules-scan-') as temp:
        root = Path(temp)
        # Empty, trusted ignore overrides Semgrep's default exclusion of tests.
        # Never copy the PR's ignore/config, including malicious nosem comments.
        (root / '.semgrepignore').write_text('# Scan all staged source, including tests.\n')
        for index, path in enumerate(changed):
            if path == '.semgrepignore':
                continue
            try:
                text = repo.text(path)
                total += len(text.encode())
                if total > MAX_TOTAL:
                    raise ValueError('changed-source size budget exceeded')
                if not text:
                    continue
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                if path.endswith('.svelte'):
                    dest = root / (path + '.ts')
                    # Do not overwrite a real changed sibling named .svelte.ts.
                    if path + '.ts' in changed:
                        raise ValueError('Svelte extraction target collides with a source file')
                    dest.write_text(svelte_scripts(text))
                    mappings[str(dest.relative_to(root))] = (path, 0)
                else:
                    dest.write_text(text)
                if path.endswith(('.yml', '.yaml')):
                    # Semgrep bash rules cannot inspect YAML run scalar ASTs.
                    # Put extracted scripts in a separate reserved subtree and
                    # map findings back to the original workflow lines.
                    for n, (first, body) in enumerate(checks.workflow_runs(text)):
                        generated = f'__review_rules_runs__/{index}-{n}.sh'
                        if any(p.startswith('__review_rules_runs__/') for p in changed):
                            raise ValueError('reserved extraction path collision')
                        script = root / generated
                        script.parent.mkdir(parents=True, exist_ok=True)
                        script.write_text(body)
                        mappings[generated] = (path, first - 1)
            except (ValueError, UnicodeError, RuntimeError) as exc:
                # Constant errors only; no blob content in the receipt.
                errors.append(f'file preparation incomplete: {type(exc).__name__}')
        if total > MAX_TOTAL:
            return findings, errors, 0
        eligible_extensions = set().union(*(EXTENSIONS[p] for p in packs))
        eligible = any(p.suffix in eligible_extensions for p in root.rglob('*') if p.is_file())
        if not eligible:
            return findings, errors, 0
        data = docker_scan(root, packs)
        for item in data['results']:
            path = item['path'].removeprefix('./')
            source, offset = mappings.get(path, (path, 0))
            if source not in changed:
                raise ValueError('scanner returned a path outside the changed-file manifest')
            findings.append(checks.finding(item['check_id'], source,
                int(item['start']['line']) + offset, item['extra']['message'], item['extra']['severity']))
        for _ in data.get('errors', []):
            errors.append('Semgrep parse/timeout error; static coverage is incomplete')
        count = len(data.get('paths', {}).get('scanned', []))
        if eligible and count == 0:
            errors.append('Semgrep scanned zero eligible changed targets')
        return findings, errors, count


def escaped(value: str, prop: bool = False) -> str:
    value = str(value).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    return value.replace(':', '%3A').replace(',', '%2C') if prop else value


def publish(report: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / 'review-rules.json').write_text(json.dumps(report, indent=2) + '\n')
    rows = ['## review-rules (shadow)', '', f"Mode: **{report['mode']}**; coverage: **{report['status']}**.",
            f"Changed files: {report['stats']['changed_files']}; scanned targets: {report['stats']['scanned_targets']}; findings: {len(report['findings'])}.",
            '', 'Signals are advisory, not behavioral proofs. Existing required gates are unchanged.', '']
    for error in report['errors']:
        rows.append('- Incomplete: ' + html.escape(error))
    rows += ['', '| Rule | File:line | Severity | Message |', '|---|---|---|---|']
    def cell(value):
        return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ')
    for item in report['findings'][:100]:
        rows.append('| ' + ' | '.join(cell(v) for v in (item['rule_id'], f"{item['file']}:{item['line']}", item['severity'], item['message'])) + ' |')
    if len(report['findings']) > 100:
        rows.append('\nAdditional findings are retained in review-rules.json.')
    summary = '\n'.join(rows) + '\n'
    (output / 'review-rules.md').write_text(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(summary)
    for item in report['findings'][:50]:
        level = 'error' if report['mode'] == 'enforce' and item['rule_id'] in report['promoted_rules'] else 'warning'
        print(f"::{level} file={escaped(item['file'], True)},line={item['line']},title={escaped(item['rule_id'], True)}::{escaped(item['message'])}")
    for error in report['errors'][:10]:
        print('::warning title=review-rules incomplete::' + escaped(error))


def exit_code(report: dict) -> int:
    if report['mode'] != 'enforce':
        return 0
    # No rule is promoted initially. Severity is not a promotion signal. Nits
    # and advisory nets never become blocking merely by flipping the input.
    return int(bool(report['errors']) or any(
        item['rule_id'] in report['promoted_rules'] and item['severity'] != 'INFO'
        for item in report['findings']))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--event', type=Path)
    parser.add_argument('--base', help='Explicit base commit for local validation only')
    parser.add_argument('--migration-ref', help='Explicit default-branch ref for local validation')
    parser.add_argument('--packs', default=','.join(PACKS))
    parser.add_argument('--mode', default='warn')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    mode = args.mode if args.mode in {'warn', 'enforce'} else 'warn'
    report = {'schema_version': 1, 'mode': mode, 'status': 'incomplete',
              'findings': [], 'errors': [], 'promoted_rules': [], 'head': '', 'base': '',
              'stats': {'changed_files': 0, 'scanned_targets': 0}}
    try:
        if args.mode not in {'warn', 'enforce'}:
            raise ValueError('mode must be warn or enforce')
        packs = {p.strip() for p in args.packs.split(',') if p.strip()}
        if not packs or not packs <= set(PACKS):
            raise ValueError('unknown or empty pack selection')
        report['promoted_rules'] = json.loads((RULES / 'promoted-rules.json').read_text())
        repo = Git(args.repo)
        if args.base:
            base, migration_ref = args.base, args.migration_ref
        else:
            if not args.event:
                raise ValueError('event or explicit local base required')
            base, migration_ref = event_refs(json.loads(args.event.read_text()))
        repo.base = repo.merge_base(repo.head, base)
        report.update(head=repo.head, base=repo.base)
        statuses = repo.statuses(repo.base, repo.head)
        changed = [p for p, status in statuses.items() if status != 'D']
        report['stats']['changed_files'] = len(statuses)
        for path in changed:
            try:
                report['findings'].extend(checks.check_text(path, repo.text(path), packs))
            except (ValueError, UnicodeError, RuntimeError) as exc:
                report['errors'].append(f'deterministic file check incomplete: {type(exc).__name__}')
        for action in (lambda: checks.repo_checks(repo, changed, packs, migration_ref),
                       lambda: checks.merge_audit(repo)):
            try:
                report['findings'].extend(action())
            except (ValueError, UnicodeError, RuntimeError) as exc:
                report['errors'].append(f'repository check incomplete: {type(exc).__name__}')
        if changed:
            hits, errors, count = scan(repo, changed, packs)
            report['findings'].extend(hits)
            report['errors'].extend(errors)
            report['stats']['scanned_targets'] = count
    except Exception as exc:
        # Shadow must remain green even on absent Docker, malformed events,
        # missing refs, invalid pack names, scanner failures or timeouts.
        report['errors'].append(f'review engine incomplete: {type(exc).__name__}')
    report['findings'] = sorted({(x['rule_id'], x['file'], x['line']): x for x in report['findings']}.values(),
                                key=lambda x: (x['file'], x['line'], x['rule_id']))
    report['errors'] = sorted(set(report['errors']))
    report['status'] = 'complete' if not report['errors'] else 'incomplete'
    try:
        publish(report, args.output)
    except OSError:
        print('::warning title=review-rules incomplete::Receipt publication failed; coverage is unknown.')
        return 1 if mode == 'enforce' else 0
    return exit_code(report)


if __name__ == '__main__':
    raise SystemExit(main())
