"""Hermetic file contracts: python3 -m unittest -v test_tier0_files.py.

Execute the actual YAML run blocks with real Git history, no scanners,
installers, network or inherited credentials. Only job-local /tmp paths move.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / '.github/workflows/tier-0-quality-gate.yml'


class FileExecutionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tier0-files-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.changed = self.root / 'tier0-changed.txt'
        self.log = self.root / 'executed.txt'
        self.env = {
            'PATH': '/usr/bin:/bin',
            'HOME': str(self.root / 'home'),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.invalid',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.invalid',
            'EXEC_LOG': str(self.log),
            'GITHUB_OUTPUT': str(self.root / 'github-output'),
        }
        self.git('init', '-q')

    def git(self, *args):
        result = subprocess.run(
            ['git', *args], cwd=self.repo, env=self.env,
            text=True, capture_output=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture')
        return self.git('rev-parse', 'HEAD')

    def step(self, job, name):
        matches = [s for s in self.jobs[job]['steps']
                   if s.get('name', '').startswith(name)]
        self.assertEqual(len(matches), 1, f'one real {job}/{name} step required')
        return matches[0]

    def run_step(self, script, **env):
        # The workflow's absolute scratch file must not collide across tests.
        script = script.replace('/tmp/tier0-changed.txt', str(self.changed))
        return subprocess.run(
            ['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', script],
            cwd=self.repo, env={**self.env, **env},
            text=True, capture_output=True, timeout=15, check=False,
        )

    def test_self_pincheck_reads_the_workflow_pin_and_checks_both_directories(self):
        caller = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        matches = [step for step in caller['jobs']['pincheck']['steps']
                   if step.get('name') == 'Tier-0 self-pin must carry the current gate files']
        self.assertEqual(len(matches), 1, 'one real self-pin freshness step required')
        script = matches[0]['run']
        paths = ('semgrep-rules/example.yaml', 'tier0/requirements-example.txt')
        for path in paths:
            self.write(path, 'before\n')
        pin = self.commit()
        workflow = '.github/workflows/tier-0-quality-gate.yml'
        self.write(workflow, f'env:\n  TIER0_SELF_PIN: "{pin}"\n')
        self.commit()
        result = self.run_step(script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # A missing directory in git diff's pathspec would let its stale pin
        # pass. Repinning to the commit carrying that directory must recover.
        for path in paths:
            with self.subTest(path=path):
                self.write(path, 'after\n')
                current = self.commit()
                result = self.run_step(script)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.write(workflow, f'env:\n  TIER0_SELF_PIN: "{current}"\n')
                self.commit()
                result = self.run_step(script)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for invalid_pin in ('', '0' * 40):
            with self.subTest(pin=invalid_pin or 'missing'):
                self.write(workflow, f'env:\n  TIER0_SELF_PIN: "{invalid_pin}"\n')
                self.commit()
                result = self.run_step(script)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def generators(self, commands):
        self.write('.tier0-generators', commands)
        self.commit()
        return self.run_step(self.step('generator-drift', 'Run declared generators')['run'])

    def test_stdin_reader_cannot_hide_later_generator_drift(self):
        # Inheriting the loop's stdin makes cat swallow the final command,
        # producing a false clean verdict for both untracked and tracked drift.
        self.write('generated.txt', 'before\n')
        self.commit()
        for path in ('new.txt', 'generated.txt'):
            with self.subTest(path=path):
                self.log.write_text('')
                result = self.generators(
                    '# comment\n\n'
                    'printf "first\\n" >> "$EXEC_LOG"; cat >/dev/null\n'
                    f'printf "after\\n" > {path}; '
                    'printf "second\\n" >> "$EXEC_LOG"'
                )
                self.assertEqual(
                    self.log.read_text().splitlines(), ['first', 'second'],
                    f'stdin-consuming generator swallowed its successor; '
                    f'exit={result.returncode}; stdout={result.stdout}',
                )
                self.assertEqual((self.repo / path).read_text(), 'after\n')
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('generator drift', result.stdout)
                self.assertIn(path, self.git('status', '--porcelain'))
                self.log.write_text('')
                self.commit()

    def test_clean_generators_keep_order_comments_and_unterminated_last_line(self):
        # Dropping EOF handling, ignoring order or executing comment lines
        # breaks this clean run; the external trace itself causes no drift.
        result = self.generators(
            '# not shell syntax: (\n\n'
            'printf "first\\n" >> "$EXEC_LOG"\n'
            '# another comment\n'
            'printf "second\\n" >> "$EXEC_LOG"'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['first', 'second'])
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_failed_generator_stops_before_successor(self):
        # A status mask or continuing the loop after a failure must not pass.
        result = self.generators(
            'printf "first\\n" >> "$EXEC_LOG"; exit 23\n'
            'printf "must-not-run\\n" >> "$EXEC_LOG"\n'
        )
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['first'])

    def changed_history(self):
        for path in ('src/deleted.py', 'src/modified.py', 'src/unchanged.py',
                     'migrations/deleted.sql', 'migrations/modified.sql',
                     'migrations/unchanged.sql', 'docs/note.md'):
            self.write(path, 'before\n')
        base = self.commit()
        self.git('rm', 'src/deleted.py', 'migrations/deleted.sql')
        for path in ('src/modified.py', 'src/added.py',
                     'migrations/modified.sql', 'migrations/added.sql', 'docs/note.md'):
            self.write(path, 'after\n')
        self.commit()
        return base

    def produce_changes(self, job, base, event='pull_request'):
        # Stale output must not make a missing-base failure look successful.
        self.changed.unlink(missing_ok=True)
        return self.run_step(
            self.step(job, 'Compute changed files')['run'],
            TIER0_EVENT=event, TIER0_PR_BASE=base,
            TIER0_BEFORE=base if event == 'push' else '',
        )

    def test_scanner_lists_keep_surviving_changes_but_exclude_deletions(self):
        # Passing deleted Python/SQL paths to scanners causes file-not-found
        # errors; filtering every change or listing the whole tree is wrong too.
        base = self.changed_history()
        expected = {'src/modified.py', 'src/added.py', 'migrations/modified.sql',
                    'migrations/added.sql', 'docs/note.md'}
        for job in ('unused-code', 'security'):
            for event, before in (('pull_request', base), ('push', base),
                                  ('workflow_dispatch', '')):
                with self.subTest(job=job, event=event):
                    result = self.produce_changes(job, before, event)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(set(self.changed.read_text().splitlines()), expected)

    def test_detection_and_test_entrypoint_still_receive_deletions(self):
        # Scanner-only filtering must not suppress deletion-triggered stacks
        # or change the repo-owned test harness's full-diff file contract.
        base = self.changed_history()
        expected = {'src/deleted.py', 'src/modified.py', 'src/added.py',
                    'migrations/deleted.sql', 'migrations/modified.sql',
                    'migrations/added.sql', 'docs/note.md'}
        for job in ('detect', 'tests'):
            with self.subTest(job=job):
                result = self.produce_changes(job, base)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(set(self.changed.read_text().splitlines()), expected)
        entrypoint = self.write('.tier0-tests', '''#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s|%s\n' "$TIER0_COVERAGE_THRESHOLD" "$TIER0_MUTATION_BUDGET_MIN" "$TIER0_ENABLE_MUTATION"
cat "$TIER0_CHANGED_FILE"
exit 19
''')
        entrypoint.chmod(0o755)
        step = self.step('tests', 'Diff coverage and mutation')
        result = self.run_step(step['run'], **{
            **step['env'],
            'TIER0_CHANGED_FILE': step['env']['TIER0_CHANGED_FILE'].replace(
                '/tmp/tier0-changed.txt', str(self.changed)),
            'TIER0_COVERAGE_THRESHOLD': '90',
            'TIER0_MUTATION_BUDGET_MIN': '10',
            'TIER0_ENABLE_MUTATION': 'true',
        })
        self.assertEqual(result.returncode, 19, 'repo harness failure must propagate')
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], '90|10|true')
        self.assertEqual(set(lines[1:]), expected)

    def test_missing_base_fails_closed_in_every_changed_list_producer(self):
        self.changed_history()
        for job in ('detect', 'unused-code', 'security', 'tests'):
            for event in ('pull_request', 'push'):
                with self.subTest(job=job, event=event):
                    result = self.produce_changes(job, 'f' * 40, event)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('failing closed', result.stdout)
                    self.assertFalse(self.changed.exists(), 'missing base must not list all files')


if __name__ == '__main__':
    unittest.main()
