"""Hermetic execution contracts: python3 -m unittest -v test_tier0_tools.py.

Run the real workflow shell blocks. Only external installers/scanners are
stubbed: no downloads, toolchain installs, credentials or GitHub calls.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / '.github/workflows/tier-0-quality-gate.yml'


class ToolExecutionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tier0-tools-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls.jsonl'
        self.env = {
            'PATH': f'{self.bin}:/usr/bin:/bin',
            'HOME': str(self.root / 'home'),
            'CALL_LOG': str(self.log),
            'GITHUB_PATH': str(self.root / 'github_path'),
            'GITHUB_OUTPUT': str(self.root / 'github_output'),
            'GITHUB_STEP_SUMMARY': str(self.root / 'summary'),
            **{k: str(v) for k, v in self.workflow.get('env', {}).items()},
        }

    def stub(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body)
        path.chmod(0o755)

    def run_step(self, script, **env):
        return subprocess.run(
            ['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', script],
            cwd=self.root, env={**self.env, **env},
            text=True, capture_output=True, timeout=15, check=False,
        )

    def lint_step(self, stack):
        matches = [s['run'] for s in self.workflow['jobs']['lint-types']['steps']
                   if f'outputs.has-{stack}' in s.get('if', '')]
        self.assertEqual(len(matches), 1, f'one real {stack} tool block required')
        return matches[0]

    def test_later_tools_run_without_erasing_an_earlier_failure(self):
        # Removing a per-command status accumulator must fail these cases:
        # tsc -> eslint, ruff -> mypy, and go vet -> gofmt.
        cases = (
            ('ts', 'tsc', 'eslint'),
            ('python', 'ruff', 'mypy'),
            ('go', 'go', 'gofmt'),
        )
        for stack, first, second in cases:
            for failing_tool in (first, second, ''):
                with self.subTest(stack=stack, failing_tool=failing_tool or 'none'):
                    self.log.write_text('')
                    for config in ('tsconfig.json', 'eslint.config.mjs',
                                   'pyproject.toml', 'mypy.ini', 'go.mod'):
                        (self.root / config).touch()
                    for tool in ('tsc', 'eslint', 'go', 'gofmt'):
                        location = (self.root / 'node_modules/.bin' / tool
                                    if tool in ('tsc', 'eslint') else self.bin / tool)
                        self.stub(location,
                                  f'printf \'["{tool}"]\\n\' >> "$CALL_LOG"\n'
                                  f'[[ "$FAIL_TOOL" != "{tool}" ]] || exit 17\n')
                    self.stub(self.bin / 'python3', '''
if [[ "${1-} ${2-}" == '-m pip' ]]; then exit 0; fi
if [[ "${1-} ${2-}" == '-m ruff' || "${1-} ${2-}" == '-m mypy' ]]; then
  printf '["%s"]\\n' "$2" >> "$CALL_LOG"
  [[ "$FAIL_TOOL" != "$2" ]] || exit 17
  exit 0
fi
exit 2
''')
                    result = self.run_step(self.lint_step(stack), FAIL_TOOL=failing_tool)
                    calls = [json.loads(line)[0] for line in self.log.read_text().splitlines()]
                    self.assertEqual(calls, [first, second], result.stdout + result.stderr)
                    if failing_tool:
                        self.assertNotEqual(result.returncode, 0,
                                            'later success must not mask tool failure')
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    verdict = next(s['run'] for s in self.workflow['jobs']['lint-types']['steps']
                                   if s.get('id') == 'verdict')
                    output = Path(self.env['GITHUB_OUTPUT'])
                    output.write_text('')
                    recorded = self.run_step(verdict, TIER0_JOB_STATUS=(
                        'failure' if result.returncode else 'success'))
                    self.assertEqual(recorded.returncode, 0)
                    expected = 'failure' if failing_tool else 'success'
                    self.assertEqual(output.read_text(), f'verdict={expected}\n')

    def test_semgrep_launcher_discovers_user_install_and_preserves_scan_exit(self):
        # A pip user install is not initially on PATH. The workflow must expose
        # and discover its supported executable in BOTH config branches.
        steps = [s['run'] for s in self.workflow['jobs']['security']['steps']
                 if 'Semgrep' in s.get('name', '')]
        self.assertEqual(len(steps), 1)
        user_bin = Path(self.env['HOME']) / '.local/bin'
        self.stub(user_bin / 'semgrep', '''
if [[ "${1-}" == '--version' ]]; then
  printf '["version"]\\n' >> "$CALL_LOG"
  echo 1.133.0
  exit 0
fi
printf '["%s","%s","%s","%s"]\\n' "${1-}" "${2-}" "${3-}" "${4-}" >> "$CALL_LOG"
[[ "${1-}" == scan && "${2-}" == --config && "${4-}" == --error ]] || exit 2
exit "$SCAN_EXIT"
''')
        self.stub(self.bin / 'python3', '''
if [[ "${1-} ${2-}" == '-m pip' ]]; then exit 0; fi
if [[ "${1-} ${2-} ${3-}" == '-m site --user-base' ]]; then
  printf '%s/.local\\n' "$HOME"
  exit 0
fi
if [[ "${1-}" == -c && "${2-}" == 'import sysconfig; print(sysconfig.get_path("scripts"))' ]]; then
  printf '%s/.local/bin\\n' "$HOME"
  exit 0
fi
echo 'unsupported Python module launcher' >&2
exit 2
''')
        (self.root / 'semgrep-rules').mkdir()
        for config, expected in (('semgrep-rules', 'semgrep-rules'),
                                 ('absent-rules', 'auto')):
            for scan_exit in (0, 1, 2):
                with self.subTest(config=config, scan_exit=scan_exit):
                    self.log.write_text('')
                    result = self.run_step(steps[0], TIER0_SEMGREP_CONFIG=config,
                                           SCAN_EXIT=str(scan_exit))
                    calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                    self.assertEqual(calls, [['version'], ['scan', '--config', expected, '--error']],
                                     result.stdout + result.stderr)
                    self.assertEqual(result.returncode, scan_exit,
                                     'clean, findings and CLI errors stay distinguishable')
        for scan_exit in (0, 1, 2):
            with self.subTest(config='absent-rules', cached=True, scan_exit=scan_exit):
                self.log.write_text('')
                (self.root / '.tier0-gate/semgrep-rules').mkdir(parents=True, exist_ok=True)
                result = self.run_step(steps[0], TIER0_SEMGREP_CONFIG='absent-rules',
                                       SCAN_EXIT=str(scan_exit))
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(calls, [['version'], ['scan', '--config', '.tier0-gate/semgrep-rules', '--error']],
                                 result.stdout + result.stderr)
                self.assertEqual(result.returncode, scan_exit,
                                 'clean, findings and CLI errors stay distinguishable')


if __name__ == '__main__':
    unittest.main()
