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

    def scan(self, source, invalid_config=False):
        # Do not use test*/fixtures paths: the live rule intentionally excludes
        # those. The scanned input is a production-shaped synthetic source file.
        with tempfile.TemporaryDirectory(prefix='tier0-scan-') as tmp:
            root = Path(tmp)
            target = root / 'src/settings.py'
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

    def test_invalid_configuration_is_a_cli_error_not_a_finding(self):
        result, payload = self.scan('colour = "blue"\n', invalid_config=True)
        self.assertNotIn(result.returncode, (0, 1), 'CLI errors must not masquerade as findings')
        self.assertTrue(payload.get('errors'), 'invalid configuration needs a diagnostic')
        self.assertEqual(payload.get('results'), [])


if __name__ == '__main__':
    unittest.main()
