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
import re, sys, yaml

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
gate_text = open(gate_path).read()
check('squawk_${SQUAWK_VERSION}_x86_64' not in gate_text,
      "squawk download is not the missing musl tarball")
check('releases/download/v${SQUAWK_VERSION}/squawk-linux-x64' in gate_text,
      "squawk downloads the plain linux-x64 binary")
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

sys.exit(1 if errors else 0)
PY
pass "tier-0 structural contract holds"
