"""Real scanner regression: python3 -m unittest -v test_tier0_semgrep.py.

Requires Semgrep 1.133.0; SEMGREP_BIN may select an isolated installation.
No auto-install, registry config, network scan, or real credentials. Kept
separate from the hermetic structural suite because the scanner is optional.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES = ROOT / 'semgrep-rules'


class RealSemgrepContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = os.environ.get('SEMGREP_BIN') or shutil.which('semgrep')
        if not cls.executable:
            raise RuntimeError('Install semgrep==1.133.0 or set SEMGREP_BIN; scanner not run')
        cls.home = tempfile.TemporaryDirectory(prefix='tier0-scanner-home-')
        cls.addClassCleanup(cls.home.cleanup)
        cls.env = {
            **{key: os.environ[key] for key in ('PATH', 'PYTHONPATH', 'PYTHONNOUSERSITE')
               if key in os.environ},
            'HOME': cls.home.name,
            'SEMGREP_SETTINGS_FILE': str(Path(cls.home.name) / 'settings.yml'),
            'SEMGREP_SEND_METRICS': 'off',
            'SEMGREP_ENABLE_VERSION_CHECK': '0',
        }
        result = subprocess.run([cls.executable, '--version'], env=cls.env,
                                text=True, capture_output=True, timeout=30, check=False)
        if result.returncode != 0 or result.stdout.strip() != '1.133.0':
            raise RuntimeError('Requires working Semgrep 1.133.0: ' + result.stdout + result.stderr)

    def scan(self, source, invalid_config=False, filename='settings.py'):
        # Do not use test*/fixtures paths: the live rule intentionally excludes
        # those. The scanned input is a production-shaped synthetic source file.
        with tempfile.TemporaryDirectory(prefix='tier0-scan-') as tmp:
            root = Path(tmp)
            target = root / 'src' / filename
            target.parent.mkdir()
            target.write_text(source)
            config = RULES
            if invalid_config:
                config = root / 'invalid.yaml'
                config.write_text('rules: [}\n')
            result = subprocess.run(
                [self.executable, 'scan', '--config', str(config), '--error',
                 '--json', '--metrics=off', '--disable-version-check',
                 '--no-git-ignore', '--no-rewrite-rule-ids', '--jobs', '1', str(target)],
                cwd=root, env=self.env, text=True, capture_output=True, timeout=60, check=False,
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f'CLI did not return scan JSON (exit {result.returncode}): {result.stderr}')
        return result, payload

    def test_rule_filters_are_not_ignored_by_the_pinned_scanner(self):
        result, payload = self.scan('colour = "blue"\n')
        self.assertEqual(payload.get('errors'), [])
        ignored = re.findall(
            r'(?im)^.*(?:unknown|ignor\w*|skip\w*)[^\n]*metavariable-regex[^\n]*$|'
            r'^.*metavariable-regex[^\n]*(?:unknown|ignor\w*|skip\w*)[^\n]*$',
            result.stderr,
        )
        self.assertEqual(ignored, [], 'scanner must apply every key filter')

    def test_benign_assignment_is_not_a_credential(self):
        result, payload = self.scan('colour = "blue"\n')
        self.assertEqual(payload.get('errors'), [])
        self.assertEqual(len(payload['paths']['scanned']), 1, 'fixture must not be excluded')
        self.assertEqual(result.returncode, 0, 'benign assignment must have exit 0')
        self.assertEqual(payload.get('results'), [], 'benign names must not match')

    def test_synthetic_api_key_is_exactly_one_correct_finding(self):
        result, payload = self.scan('api_key = "synthetic-not-a-real-credential-123456"\n')
        self.assertEqual(payload.get('errors'), [])
        self.assertEqual(result.returncode, 1, 'one credential finding must have exit 1')
        findings = payload.get('results', [])
        self.assertEqual(len(findings), 1, 'one synthetic assignment, one finding')
        self.assertEqual(findings[0]['check_id'], 'tier0-no-hardcoded-credentials')
        self.assertEqual(findings[0]['start']['line'], 1)
        self.assertEqual(len(payload['paths']['scanned']), 1, 'fixture must not be excluded')

    def test_prefixed_db_password_is_exactly_one_correct_finding(self):
        result, payload = self.scan('DB_PASSWORD = "synthetic-not-a-real-credential-123456"\n')
        self.assertEqual(payload.get('errors'), [])
        self.assertEqual(result.returncode, 1, 'prefixed credentials must have exit 1')
        findings = payload.get('results', [])
        self.assertEqual(len(findings), 1, 'one prefixed assignment, one finding')
        self.assertEqual(findings[0]['check_id'], 'tier0-no-hardcoded-credentials')
        self.assertEqual(findings[0]['start']['line'], 1)
        self.assertEqual(len(payload['paths']['scanned']), 1, 'fixture must not be excluded')

    def test_attribute_password_is_exactly_one_correct_finding(self):
        result, payload = self.scan('settings.password = "synthetic-not-a-real-credential-123456"\n')
        self.assertEqual(payload.get('errors'), [])
        self.assertEqual(result.returncode, 1, 'attribute credentials must have exit 1')
        findings = payload.get('results', [])
        self.assertEqual(len(findings), 1, 'one attribute assignment, one finding')
        self.assertEqual(findings[0]['check_id'], 'tier0-no-hardcoded-credentials')
        self.assertEqual(findings[0]['start']['line'], 1)
        self.assertEqual(len(payload['paths']['scanned']), 1, 'fixture must not be excluded')

    def assert_rule_fixture(self, source, filename, rule_id, expected):
        result, payload = self.scan(source, filename=filename)
        self.assertEqual(payload.get('errors'), [])
        self.assertEqual(len(payload['paths']['scanned']), 1, 'fixture must not be excluded')
        self.assertEqual(result.returncode, 1 if expected else 0,
                         'findings, clean scans and CLI errors stay distinguishable')
        # Semgrep also resolves destructured exec in the separate eval rule;
        # each split-rule fixture asserts only its own rule's exact findings.
        findings = [(finding['check_id'], finding['start']['line'])
                    for finding in payload.get('results', []) if finding['check_id'] == rule_id]
        self.assertEqual(findings, expected, 'exact rule, line and finding count required')

    def test_javascript_and_typescript_eval_require_dynamic_input(self):
        cases = (
            ('dynamic', 'eval(userInput);\n', [('tier0-no-eval', 1)]),
            ('literal', 'eval("1+1");\n', []),
        )
        for extension in ('js', 'ts'):
            for fixture, source, expected in cases:
                with self.subTest(language=extension, fixture=fixture):
                    self.assert_rule_fixture(source, f'execute.{extension}', 'tier0-no-eval', expected)

    def test_python_eval_requires_dynamic_input(self):
        cases = (
            ('dynamic', 'eval(user_input)\n', [('tier0-no-eval-python', 1)]),
            ('literal', 'eval("1+1")\n', []),
        )
        for fixture, source, expected in cases:
            with self.subTest(fixture=fixture):
                self.assert_rule_fixture(source, 'execute.py', 'tier0-no-eval-python', expected)

    def test_javascript_and_typescript_shell_exec_require_interpolation(self):
        cases = (
            ('qualified', ('const child_process = require("child_process");\n'
                           'child_process.exec(`ls ${d}`);\n'),
             [('tier0-no-shell-exec-interpolation', 2)]),
            ('destructured', ('const { exec } = require("child_process");\n'
                             'exec(`ls ${d}`);\n'),
             [('tier0-no-shell-exec-interpolation', 2)]),
            ('fixed', ('const { execSync } = require("child_process");\n'
                       'execSync("fixed");\n'), []),
        )
        for extension in ('js', 'ts'):
            for fixture, source, expected in cases:
                with self.subTest(language=extension, fixture=fixture):
                    self.assert_rule_fixture(source, f'execute.{extension}',
                                             'tier0-no-shell-exec-interpolation', expected)

    def test_python_shell_exec_covers_call_popen_and_system(self):
        cases = (
            ('call', 'import subprocess\nsubprocess.call(cmd, shell=True)\n',
             [('tier0-no-shell-exec-interpolation-python', 2)]),
            ('popen', ('import subprocess\n'
                       'subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)\n'),
             [('tier0-no-shell-exec-interpolation-python', 2)]),
            ('system', 'import os\nos.system(cmd)\n',
             [('tier0-no-shell-exec-interpolation-python', 2)]),
            ('argv', 'import subprocess\nsubprocess.call(["ls"])\n', []),
        )
        for fixture, source, expected in cases:
            with self.subTest(fixture=fixture):
                self.assert_rule_fixture(source, 'execute.py',
                                         'tier0-no-shell-exec-interpolation-python', expected)

    def test_python_shell_exec_covers_run_check_output_and_check_call(self):
        cases = (
            ('command', 'cmd, shell=True', [('tier0-no-shell-exec-interpolation-python', 2)]),
            ('interpolated', 'f"ls {directory}", shell=True',
             [('tier0-no-shell-exec-interpolation-python', 2)]),
            ('argv', '["ls", directory]', []),
            ('argv_without_shell', '["ls", directory], shell=False', []),
        )
        for function in ('run', 'check_output', 'check_call'):
            for fixture, arguments, expected in cases:
                with self.subTest(function=function, fixture=fixture):
                    self.assert_rule_fixture(
                        f'import subprocess\nsubprocess.{function}({arguments})\n',
                        'execute.py', 'tier0-no-shell-exec-interpolation-python', expected,
                    )

    def test_python_shell_exec_reports_truthy_and_unknown_shell_flags(self):
        # Scan synthetic text only: none of these command strings is executed.
        rule = 'tier0-no-shell-exec-interpolation-python'
        functions = ('run', 'call', 'check_call', 'check_output', 'Popen')
        expected = [(rule, line) for line in (2, 3, 4, 5, 6)]
        for flag in ('True', '1', '-1', '"enabled"', 'use_shell'):
            with self.subTest(shell=flag):
                source = 'import subprocess\n' + ''.join(
                    f'subprocess.{function}(command, shell={flag})\n'
                    for function in functions
                )
                self.assert_rule_fixture(source, 'execute.py', rule, expected)

    def test_python_shell_exec_reports_every_always_shell_helper(self):
        rule = 'tier0-no-shell-exec-interpolation-python'
        source = ('import os\nimport subprocess\n'
                  'os.system(command)\n'
                  'os.popen(command)\n'
                  'subprocess.getoutput(command)\n'
                  'subprocess.getstatusoutput(command)\n')
        self.assert_rule_fixture(source, 'execute.py', rule,
                                 [(rule, 3), (rule, 4), (rule, 5), (rule, 6)])

    def test_python_shell_exec_allows_falsy_flags_and_plain_argv(self):
        rule = 'tier0-no-shell-exec-interpolation-python'
        functions = ('run', 'call', 'check_call', 'check_output', 'Popen')
        for flag in ('False', '0', 'None', None):
            with self.subTest(shell=flag):
                keyword = '' if flag is None else f', shell={flag}'
                source = 'import subprocess\n' + ''.join(
                    f'subprocess.{function}(["fixed-program", argument]{keyword})\n'
                    for function in functions
                )
                self.assert_rule_fixture(source, 'execute.py', rule, [])

    def test_invalid_configuration_is_a_cli_error_not_a_finding(self):
        result, payload = self.scan('colour = "blue"\n', invalid_config=True)
        self.assertNotIn(result.returncode, (0, 1), 'CLI errors must not masquerade as findings')
        self.assertTrue(payload.get('errors'), 'invalid configuration needs a diagnostic')
        self.assertEqual(payload.get('results'), [])


if __name__ == '__main__':
    unittest.main()
