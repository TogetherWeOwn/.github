#!/usr/bin/env bash
# test_tier0_gate.sh — structural contract for the Tier-0 reusable quality
# gate. Hermetic: python3 + PyYAML only, no network, no Docker.
#
# It pins the properties that make one file safe to reuse across every
# TogetherWeOwn repo: report-only by default, no path-filtered triggers
# (a gate that skips is a hole), SHA-pinned actions, portable runners,
# one aggregator, and valid Semgrep rule packs. The dogfood caller lives
# in this repo's own ci.yml (job `tier-0`, report-only), not in a separate
# caller file.
set -euo pipefail
cd "$(dirname "$0")"

GATE=.github/workflows/tier-0-quality-gate.yml
CALLER=.github/workflows/ci.yml
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "PASS: $1"; }

python3 - "$GATE" "$CALLER" <<'PY'
import re, shlex, sys, yaml
from pathlib import Path

gate_path, caller_path = sys.argv[1], sys.argv[2]
gate = yaml.safe_load(open(gate_path))
caller = yaml.safe_load(open(caller_path))
errors = []
def check(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        errors.append(msg)

# 1. Reusable entrypoint, report-only default. (YAML 1.1 parses the `on:`
# key as boolean True — accept either spelling.)
def triggers(wf):
    on = wf.get('on', wf.get(True, {}))
    return on or {}
wc = triggers(gate).get('workflow_call', {})
check(isinstance(wc, dict) and wc.get('inputs', {}).get('mode', {}).get('default') == 'report-only',
      "workflow_call exists with mode default report-only")

# 2. No path-filtered triggers in either file (rule: required workflows
#    never use paths:/paths-ignore:).
for name, wf in (("gate", gate), ("caller", caller)):
    on = triggers(wf)
    if isinstance(on, dict):
        for trig, cfg in on.items():
            if isinstance(cfg, dict):
                check('paths' not in cfg and 'paths-ignore' not in cfg,
                      f"{name}: trigger '{trig}' has no path filter")

# 3. Advisory in report-only, honest by output. Gates stay green under a
#    report-only continue-on-error (so the caller and its ci-ok stay green),
#    and every job publishes an honest `verdict` output the aggregator reads
#    for its notices and artifact (results lie masked; verdicts do not).
#    detect carries no mask: infra failures stay loud.
jobs = gate.get('jobs', {})
expected_gates = {'lint-types', 'unused-code', 'generator-drift',
                  'workflow-checks', 'security', 'tests'}
check(expected_gates <= set(jobs), f"all six gates present ({sorted(expected_gates)})")
for j, spec in jobs.items():
    if j == 'tier-0-ok':
        continue
    if j == 'detect':
        check('continue-on-error' not in spec,
              "detect sets no continue-on-error (infra failures stay loud)")
    else:
        check(spec.get('continue-on-error') == "${{ inputs.mode == 'report-only' }}",
              f"job {j} continue-on-error tied to report-only mode")
    outs = spec.get('outputs', {}) or {}
    check(outs.get('verdict') == '${{ steps.verdict.outputs.verdict }}',
          f"job {j} publishes an honest verdict output")
    vsteps = [s for s in spec.get('steps', []) if s.get('id') == 'verdict']
    check(len(vsteps) == 1 and vsteps[0].get('if') == 'always()',
          f"job {j} records its verdict in an always-run step")
    check('${{' not in (vsteps[0].get('run') or '') if vsteps else False,
          f"job {j} verdict step uses env indirection, no inline expression")
agg_src = str(jobs.get('tier-0-ok', {}))
check('continue-on-error' not in jobs.get('tier-0-ok', {}),
      "tier-0-ok sets no continue-on-error (enforcing fails honestly)")
check('verdict' in agg_src and 'outputs' in agg_src,
      "tier-0-ok reads honest verdict outputs (not masked results)")
check('enforcing' in agg_src,
      "tier-0-ok still fails failed gates in enforcing mode")
# 3b. Full evidence in Gates 1, 2, 4 and 5: tool steps run when an earlier
#     tool failed (default success() gating would skip them), without
#     turning cancellation into an always-run tool invocation.
for gj in ('lint-types', 'unused-code', 'workflow-checks', 'security'):
    gsteps = jobs[gj].get('steps', [])
    tools = [s for s in gsteps
             if (s.get('name') or '') not in ('Summarise findings',)
             and s.get('id') != 'verdict'
             and 'checkout' not in s.get('uses', '')
             and 'Compute changed files' not in (s.get('name') or '')]
    check(bool(tools), f"gate {gj}: actual tool steps found (non-vacuous coverage)")
    for tool in tools:
        condition = tool.get('if') or ''
        check(condition.startswith('${{ !cancelled()') and
              '||' not in condition and
              not re.search(r'\b(?:success|failure|always)\s*\(', condition),
              f"gate {gj}/{tool.get('name', tool.get('id', '?'))}: "
              "tool runs after a failure but not cancellation")
        check('continue-on-error' not in tool,
              f"gate {gj}/{tool.get('name', '?')}: tool failure is not masked before verdict")
check('min-severity' in str(jobs['workflow-checks']),
      "zizmor audits at a pinned severity floor (help notes stay advisory)")

# 4. Exactly one aggregator, always-running, needs every gate.
agg = jobs.get('tier-0-ok', {})
check(agg.get('if') == '${{ !cancelled() }}', "tier-0-ok uses if: !cancelled()")
check(set(agg.get('needs', [])) == {'detect'} | expected_gates,
      "tier-0-ok needs detect plus all six gates")
aggs = [j for j, s in jobs.items() if 'tier-0' in j and 'needs' in s and j != 'tier-0-ok']
check(not aggs, "no second aggregator")

# 5. Third-party actions pinned by SHA; portable runners; timeouts everywhere.
#    Same-repo local callers (`./...`) carry no ref by construction.
sha = re.compile(r'^[^@]+@[0-9a-f]{40}$')
def uses_entries(obj):
    found = []
    if isinstance(obj, dict):
        if 'uses' in obj and isinstance(obj['uses'], str):
            found.append(obj['uses'])
        for v in obj.values():
            found += uses_entries(v)
    elif isinstance(obj, list):
        for v in obj:
            found += uses_entries(v)
    return found
for u in uses_entries(gate) + uses_entries(caller):
    if u.startswith('./'):
        continue
    check(bool(sha.match(u.split('#')[0].strip())), f"action pinned by SHA: {u}")
for j, spec in jobs.items():
    if 'runs-on' in spec:
        check(spec['runs-on'] == 'ubuntu-latest', f"job {j} runs on ubuntu-latest (portable)")
    check('timeout-minutes' in spec, f"job {j} has timeout-minutes")

# 6. Dogfood caller shape in ci.yml: a `tier-0` job calling the reusable
#    gate file (report-only), fed into the required `ci-ok` aggregator.
#    A caller `uses:` job takes no runs-on/timeout-minutes (GitHub rejects
#    them there); timeouts live inside the reusable workflow. The caller is
#    change-gated (heavy by the timeout rule: Gate 6 budgets 30 minutes).
cjobs = caller.get('jobs', {})
tier0 = cjobs.get('tier-0', {})
check(bool(tier0), "ci.yml has a tier-0 caller job")
check((tier0.get('uses') or '').endswith('tier-0-quality-gate.yml'),
      "tier-0 caller uses the reusable gate file")
check((tier0.get('with') or {}).get('mode') == 'report-only',
      "tier-0 caller runs report-only")
check('timeout-minutes' not in tier0 and 'runs-on' not in tier0,
      "tier-0 caller sets no timeout-minutes/runs-on (rejected on uses: jobs)")
check(bool(re.search(r'needs\.[a-zA-Z0-9_-]+\.outputs', tier0.get('if', ''))),
      "tier-0 caller is change-gated via needs.*.outputs")
check('github.event.pull_request.draft' in (tier0.get('if') or ''),
      "tier-0 caller skips drafts like the other heavy jobs")
ciok = cjobs.get('ci-ok', {})
check('tier-0' in (ciok.get('needs') or []), "ci-ok needs the tier-0 caller")
check('tier-0-selftest' in (ciok.get('needs') or []),
      "ci-ok needs tier-0-selftest (regressions cannot merge green)")
pin_steps = [s for s in cjobs.get('pincheck', {}).get('steps', [])
             if s.get('name') == 'Tier-0 self-pin must carry the current gate files']
check(len(pin_steps) == 1 and bool(pin_steps[0].get('run')),
      "pincheck runs the Tier-0 self-pin freshness check")

# 7. Supply-chain hygiene (dogfood): every checkout drops credentials, and
#    no run: script expands a ${{ }} expression inline (env indirection only).
def steps(obj):
    found = []
    if isinstance(obj, dict):
        if isinstance(obj.get('steps'), list):
            found += obj['steps']
        for v in obj.values():
            found += steps(v)
    elif isinstance(obj, list):
        for v in obj:
            found += steps(v)
    return found
for s in steps(gate):
    uses = s.get('uses', '')
    if 'actions/checkout' in uses:
        check(s.get('with', {}).get('persist-credentials') is False,
              f"checkout step drops credentials ({s.get('name', uses)})")
    run = s.get('run', '')
    if isinstance(run, str) and run:
        check('${{' not in run,
              f"run: block uses env indirection, no inline expression ({s.get('name', '?')})")

# 8. Detect publishes stack outputs; the changed-file list never crosses a
#    job boundary (128 KiB+ environments fail with E2BIG), so no gate may
#    consume it through needs.
det_out = (jobs.get('detect', {}).get('outputs', {})) or {}
for o in ('has-ts', 'has-php', 'has-rust', 'has-go',
          'has-python', 'has-workflows', 'has-migrations'):
    check(o in det_out, f"detect publishes output {o}")
check('changed' not in det_out, "detect publishes no changed-file output (E2BIG)")
check('needs.detect.outputs.changed' not in yaml.dump(gate),
      "no gate consumes needs.detect.outputs.changed")

# 9. Semgrep packs: live rules carry id/languages/patterns; templates are
#    stub callees that match nothing but stay valid config.
import glob
live = 0
for path in sorted(glob.glob('semgrep-rules/*.yaml')):
    doc = yaml.safe_load(open(path))
    check(isinstance(doc, dict) and isinstance(doc.get('rules'), list),
          f"{path} has a rules list")
    for r in doc['rules']:
        tier = (r.get('metadata') or {}).get('tier0')
        if tier == 'live':
            live += 1
            check(all(k in r for k in ('id', 'languages', 'message')) and
                  ('patterns' in r or 'pattern' in r),
                  f"{path}/{r.get('id')} live rule is complete")
        elif tier == 'template-disabled':
            pat = r.get('pattern', '')
            check('__tier0_template' in pat, f"{path}/{r.get('id')} template matches nothing")
        else:
            check(False, f"{path}/{r.get('id')} has tier0 metadata (live|template-disabled)")
check(live >= 2, f"at least two live rules ({live} found)")

# 10. Stack detection on a large changed-file list. The detect step once
# matched through `printf | grep -q` under pipefail: grep exits on the first
# hit, printf takes SIGPIPE on the rest of a list over 64 KiB, and a real
# stack reads as absent. Run the real stack step on such a list.
import os, subprocess, tempfile
stack_run = next(s['run'] for s in gate['jobs']['detect']['steps'] if s.get('id') == 'stack')
with tempfile.TemporaryDirectory() as tmp:
    changed_path = os.path.join(tmp, 'changed.txt')
    output_path = os.path.join(tmp, 'github_output.txt')
    with open(changed_path, 'w') as fh:
        fh.write('src/index.ts\n' + ''.join(f'docs/generated/page_{i:05d}.md\n' for i in range(6000)))
    open(output_path, 'w').close()
    script = stack_run.replace('/tmp/tier0-changed.txt', changed_path)
    run = subprocess.run(['bash', '-c', script], cwd=tmp, capture_output=True, text=True,
                         env={'PATH': os.environ['PATH'], 'GITHUB_OUTPUT': output_path})
    emitted = open(output_path).read().splitlines()
    check(run.returncode == 0 and 'has-ts=true' in emitted,
          "stack detection finds TypeScript on a changed list over 64 KiB")

# 11. Review fixes: a real Semgrep version, a squawk binary that exists,
#     per-job diffing with full history, and the file-based .tier0-tests
#     contract (no changed list through the environment).
env_pins = gate.get('env', {}) or {}
semgrep_v = env_pins.get('SEMGREP_VERSION', '')
check(semgrep_v != '1.129.0' and bool(re.match(r'^\d+\.\d+\.\d+$', semgrep_v or '')),
      f"SEMGREP_VERSION is a real release ({semgrep_v})")
check(semgrep_v == '1.133.0', "Semgrep regression contract stays pinned to 1.133.0")
gate_text = open(gate_path).read()
check(not re.search(r'\bpython3?\s+-m\s+semgrep\b', gate_text),
      "Semgrep uses its supported executable, never the non-entrypoint module")
semgrep_steps = [s for s in jobs['security'].get('steps', [])
                 if re.search(r'\bsemgrep\s+(?:scan|--config)\b', s.get('run') or '')]
check(len(semgrep_steps) == 1, "one executable Semgrep scan step found")
if semgrep_steps:
    semgrep_run = semgrep_steps[0]['run']
    check(len(re.findall(r'(?m)^\s*semgrep\s+scan\b', semgrep_run)) == 2,
          "both local-config and registry-fallback branches use semgrep scan")
    check(bool(re.search(r'\bcommand\s+-v\s+semgrep\b', semgrep_run)),
          "Semgrep executable is discovered before scanning")
    check(bool(re.search(r'\bsemgrep\s+(?:scan\s+)?--version\b', semgrep_run)),
          "Semgrep executable version is checked before scanning")
for path in sorted(glob.glob('semgrep-rules/*.yaml')):
    for rule in yaml.safe_load(open(path))['rules']:
        check('metavariable-regex' not in rule,
              f"{path}/{rule['id']}: no ignored rule-level metavariable-regex")
        if 'secrets-in-code' in path:
            clauses = rule.get('patterns', [])
            assignments = [
                [c['pattern']] if 'pattern' in c else
                [alternative.get('pattern') for alternative in c.get('pattern-either', [])]
                for c in clauses
            ]
            check(any(patterns and all(isinstance(p, str) and '$KEY' in p
                                       for p in patterns) for patterns in assignments) and
                  any(isinstance(c.get('metavariable-regex'), dict) and
                      c['metavariable-regex'].get('metavariable') == '$KEY' and
                      bool(c['metavariable-regex'].get('regex')) for c in clauses),
                  f"{path}/{rule['id']}: assignment and key filter are conjoined")
check('squawk_${SQUAWK_VERSION}_x86_64' not in gate_text,
      "squawk download is not the missing musl tarball")
check('releases/download/v${SQUAWK_VERSION}/squawk-linux-x64' in gate_text,
      "squawk downloads the plain linux-x64 binary")
check('gitleaks diff ' not in gate_text and 'gitleaks detect ' not in gate_text,
      "gitleaks uses only subcommands that exist in 8.30.1 (git/dir/stdin)")
check('gitleaks git --log-opts=' in gate_text,
      "gitleaks scans the PR range via git --log-opts")
for m in re.finditer(r'#\s*shellcheck\s+disable=([A-Z0-9_, ]+)(?::(.*))?', gate_text):
    check(not (m.group(2) or '').strip(),
          "shellcheck directive carries no trailing comment (SC1125)")
for j in ('unused-code', 'security', 'tests'):
    jsteps = jobs[j].get('steps', [])
    checkouts = [s for s in jsteps if 'actions/checkout' in s.get('uses', '')]
    check(any(c.get('with', {}).get('fetch-depth') == 0 for c in checkouts),
          f"job {j} checks out full history for per-job diffing")
    check(any('tier0-changed.txt' in (s.get('run') or '') for s in jsteps),
          f"job {j} recomputes its own changed-file list")
check('TIER0_CHANGED: ' not in gate_text and 'TIER0_CHANGED_FILES: ' not in gate_text,
      "no job passes the changed list through the environment")
check('TIER0_CHANGED_FILE' in gate_text,
      "Gate 6 passes the list as TIER0_CHANGED_FILE")

# 12. Review-round supply-chain contract. Missing installers/checkouts must
#     fail too: checking only the entries that remain would pass a deletion.
def integrity(cond, msg):
    check(cond, f"review integrity: {msg}")

requirements = {
    'lint-types': ('lint', (('ruff', 'RUFF_VERSION'), ('mypy', 'MYPY_VERSION'))),
    'unused-code': ('unused', (('vulture', 'VULTURE_VERSION'),)),
    'security': ('security', (('semgrep', 'SEMGREP_VERSION'),
                              ('setuptools', 'SETUPTOOLS_VERSION'))),
}
pip_installs = []
run_blocks = []
for job, spec in jobs.items():
    for step in spec.get('steps', []):
        script = (step.get('run') or '').replace('\\\n', ' ')
        run_blocks.append(script)
        for match in re.finditer(
                r'(?m)^\s*(?:python[\d.]*\s+-m\s+)?pip[\d.]*\s+install\s+([^\n]+)', script):
            command = re.split(r'\s+(?:\||&&|;)\s*', match[1], maxsplit=1)[0]
            args = [arg for arg in shlex.split(command, comments=True)
                    if not re.fullmatch(r'\d*>&\d+', arg)]
            pip_installs.append((job, args))
integrity(len(pip_installs) == 3 and
          {job for job, _ in pip_installs} == set(requirements),
          "exactly one pip installer per lint, unused and security job")
for job, (family, packages) in requirements.items():
    expected = f'.tier0-gate/tier0/requirements-{family}.txt'
    installs = [args for owner, args in pip_installs if owner == job]
    integrity(len(installs) == 1 and installs[0].count('-r') == 1 and
              installs[0][installs[0].index('-r') + 1:installs[0].index('-r') + 2] == [expected] and
              '--require-hashes' in installs[0] and
              all(arg.startswith('-') or arg == expected for arg in installs[0]),
              f"{job} pip uses --require-hashes -r {expected}, no inline packages")
    path = Path(f'tier0/requirements-{family}.txt')
    integrity(path.is_file(), f"{path} exists")
    text = path.read_text().replace('\\\n', ' ') if path.is_file() else ''
    for package, version_key in packages:
        version = str(env_pins.get(version_key, ''))
        entries = re.findall(rf'(?m)^\s*{re.escape(package)}==([^\s]+)([^\n]*)', text)
        integrity(bool(re.fullmatch(r'\d+(?:\.\d+)+', version)) and
                  len(entries) == 1 and entries[0][0] == version,
                  f"{path} pins {package} to env.{version_key}")
        integrity(len(entries) == 1 and bool(re.search(
                  r'(?:^|\s)--hash(?:=|\s+)sha256:[0-9a-fA-F]{64}(?=\s|$)', entries[0][1])),
                  f"{path} hashes {package}")

# The caller's selftest is an installer too, including quoted venv executables
# that do not begin with bare pip or python -m pip.
selftest_installs = []
punctuation = '();&|<>'
quoted_punctuation = ''.join(chr(0xe000 + index) for index in range(len(punctuation)))
protect = str.maketrans(punctuation, quoted_punctuation)
unprotect = str.maketrans(quoted_punctuation, punctuation)
for step in cjobs.get('tier-0-selftest', {}).get('steps', []):
    script = (step.get('run') or '').replace('\\\n', ' ')
    for line in script.splitlines():
        # Keep quoted/escaped punctuation as argument text while shlex finds
        # real command boundaries. Never execute caller shell text.
        if any(char in line for char in quoted_punctuation):
            integrity(False, 'tier-0-selftest has unsupported installer shell syntax')
            continue
        protected = re.sub(r'''"(?:\\.|[^"\\])*"|'[^']*'|\\.''',
                           lambda match: match[0].translate(protect), line)
        lexer = shlex.shlex(protected, posix=True, punctuation_chars=punctuation)
        lexer.whitespace_split = True
        try:
            words = list(lexer)
        except ValueError:
            integrity(False, 'tier-0-selftest has unsupported installer shell syntax')
            continue
        for index, word in enumerate(words[:-1]):
            # Only executables at line/subshell/chained-command starts count,
            # including python -m pip, not pip words in another command's argv.
            if index and not re.fullmatch(r'[();&|]+', words[index - 1]):
                continue
            if re.fullmatch(r'(?:.*/)?python[\d.]*', word):
                module = words[index + 1:index + 3]
                if (len(module) != 2 or module[0] != '-m' or
                        not re.fullmatch(r'pip[\d.]*', module[1])):
                    continue
                index += 2
            elif not re.fullmatch(r'(?:.*/)?pip[\d.]*', word):
                continue
            args = []
            for position in range(index + 1, len(words)):
                arg = words[position]
                if re.fullmatch(r'[();&|<>]+', arg):
                    if re.search(r'[<>]', arg):
                        if args and args[-1].isdigit():
                            args.pop()  # file descriptor, not a package argument
                        # Only a trailing redirection is supported. Never
                        # silently drop arguments that follow its operand.
                        tail = words[position + 1:]
                        integrity('install' in args and bool(tail) and
                                  not re.fullmatch(r'[();&|<>]+', tail[0]) and
                                  (len(tail) == 1 or
                                   re.fullmatch(r'[();&|]+', tail[1])),
                                  'tier-0-selftest pip supports only trailing redirections')
                    break
                args.append(arg.translate(unprotect))
            if 'install' in args:
                # Include global options in the same strict allowlist as
                # install options. Unknown/value-taking options fail closed.
                args.remove('install')
                selftest_installs.append(args)
integrity(bool(selftest_installs), "tier-0-selftest has pip installers (including venv paths)")
selftest_requirements = []
for index, args in enumerate(selftest_installs):
    files = []
    valid = True
    remaining = iter(args)
    for arg in remaining:
        if arg == '-r':
            path = next(remaining, '')
            valid = valid and bool(path) and Path(path).is_file()
            files.append(path)
        else:
            valid = valid and arg in {'--require-hashes', '--disable-pip-version-check',
                                      '-q', '--quiet', '--no-input', '--no-cache-dir'}
    integrity(valid and bool(files) and '--require-hashes' in args,
              f"tier-0-selftest pip install {index + 1} uses --require-hashes and only -r files")
    selftest_requirements.extend(files)
integrity('tier0/requirements-selftest.txt' in selftest_requirements,
          "tier-0-selftest installs tier0/requirements-selftest.txt")
selftest_path = Path('tier0/requirements-selftest.txt')
integrity(selftest_path.is_file(), f"{selftest_path} exists")
selftest_text = selftest_path.read_text().replace('\\\n', ' ') if selftest_path.is_file() else ''
pyyaml = re.findall(r'(?im)^\s*pyyaml==([^\s]+)([^\n]*)', selftest_text)
integrity(len(pyyaml) == 1 and pyyaml[0][0] == '6.0.2',
          f"{selftest_path} pins pyyaml==6.0.2")
integrity(len(pyyaml) == 1 and bool(re.search(
          r'(?:^|\s)--hash(?:=|\s+)sha256:[0-9a-fA-F]{64}(?=\s|$)', pyyaml[0][1])),
          f"{selftest_path} hashes PyYAML with SHA-256")

for binary in ('actionlint', 'zizmor', 'gitleaks', 'squawk'):
    version_key = binary.upper() + '_VERSION'
    digest_key = binary.upper() + '_SHA256'
    integrity(bool(re.fullmatch(r'[0-9a-fA-F]{64}', str(env_pins.get(digest_key, '')))),
              f"{binary} has an env SHA-256 digest")
    blocks = [script for script in run_blocks if any(
        re.match(r'^\s*curl\s', line) and version_key in line
        for line in script.splitlines())]
    integrity(len(blocks) == 1, f"{binary} has one binary download block")
    lines = blocks[0].splitlines() if len(blocks) == 1 else []
    downloads = [(index, shlex.split(line)) for index, line in enumerate(lines)
                 if re.match(r'^\s*curl\s', line) and version_key in line]
    download_index, args = downloads[0] if len(downloads) == 1 else (-1, [])
    artifact = args[args.index('-o') + 1] if '-o' in args and args.index('-o') + 1 < len(args) else ''
    integrity(bool(artifact), f"{binary} download names its output artifact")
    verified = [index for index, line in enumerate(lines)
                if re.match(r'^\s*(?:echo|printf)\s', line) and artifact and artifact in line
                and re.search(rf'\$(?:\{{{digest_key}\}}|{digest_key}\b)', line)
                and re.search(r'\|\s*sha256sum\s+-c(?:\s|$)', line)]
    integrity(len(verified) == 1,
              f"{binary} verifies its artifact with env.{digest_key} and sha256sum -c")
    verify_index = verified[0] if len(verified) == 1 else -1
    integrity(download_index >= 0 and download_index < verify_index,
              f"{binary} checksum follows its download")
    consumers = [index for index, line in enumerate(lines) if (
        artifact and artifact in line and
        re.match(r'^\s*(?:sudo\s+)?(?:tar|unzip|install|mv|chmod)\s', line)) or
        re.match(rf'^\s*(?:sudo\s+)?(?:\S*/)?{binary}(?:\s|$)', line)]
    integrity(bool(consumers) and verify_index >= 0 and verify_index < min(consumers),
              f"{binary} checksum precedes extraction or use")

integrity(bool(re.fullmatch(r'[0-9a-fA-F]{40}', str(env_pins.get('TIER0_SELF_PIN', '')))),
          "TIER0_SELF_PIN is exactly a 40-hex commit (freshness belongs to pincheck)")
side_checkouts = [(job, step.get('with', {})) for job, spec in jobs.items()
                  for step in spec.get('steps', [])
                  if 'actions/checkout@' in step.get('uses', '') and
                  step.get('with', {}).get('path') == '.tier0-gate']
integrity(len(side_checkouts) == 3 and
          {job for job, _ in side_checkouts} == set(requirements),
          "exactly three .tier0-gate side-checkouts cover lint, unused and security")
for job in requirements:
    configs = [config for owner, config in side_checkouts if owner == job]
    config = configs[0] if len(configs) == 1 else {}
    integrity(len(configs) == 1 and config.get('repository') == 'TogetherWeOwn/.github',
              f"{job} side-checkout uses the org rules repository")
    integrity(config.get('ref') == '${{ env.TIER0_SELF_PIN }}',
              f"{job} side-checkout uses env.TIER0_SELF_PIN")
    expected = {'semgrep-rules', 'tier0'} if job == 'security' else {'tier0'}
    integrity(set(str(config.get('sparse-checkout', '')).split()) == expected,
              f"{job} side-checkout sparsely includes {sorted(expected)}")
    integrity(config.get('sparse-checkout-cone-mode') is False,
              f"{job} side-checkout disables cone mode (root files stay excluded)")

sys.exit(1 if errors else 0)
PY
python3 -m unittest -v test_tier0_tools.py
pass "tier-0 structural and tool-execution contracts hold"
