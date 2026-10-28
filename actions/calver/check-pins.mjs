import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const examples = readFileSync(new URL('./examples.md', import.meta.url), 'utf8');
const workflowPins = [...examples.matchAll(/^\s*uses: TogetherWeOwn\/\.github\/\.github\/workflows\/calver\.yml@([0-9a-f]{40})\s*$/gm)].map(match => match[1]);
const toolingPins = [...examples.matchAll(/^\s*tooling-sha: ([0-9a-f]{40})\s*$/gm)].map(match => match[1]);
const actionPins = [...examples.matchAll(/^\s*- uses: TogetherWeOwn\/\.github\/actions\/calver@([0-9a-f]{40})\s*$/gm)].map(match => match[1]);
if (workflowPins.length !== 1 || toolingPins.length !== 1 || actionPins.length !== 2 || [...workflowPins, ...toolingPins, ...actionPins].some(pin => pin !== workflowPins[0])) throw new Error('examples must pin workflow, tooling and both adapter steps to the same full SHA');
const pin = workflowPins[0];
const git = (...args) => execFileSync('git', ['-C', root, ...args], { maxBuffer: 1024 * 1024, timeout: 30000 });
try { git('cat-file', '-e', `${pin}^{commit}`); }
catch { git('fetch', '--no-tags', 'origin', pin); }
for (const path of ['.github/workflows/calver.yml', 'actions/calver/action.yml', 'actions/calver/calver.mjs', 'actions/calver/github.mjs', 'actions/calver/cli.mjs']) {
  if (!git('show', `${pin}:${path}`).equals(readFileSync(new URL(`../../${path}`, import.meta.url)))) throw new Error(`CalVer examples pin stale tooling: ${path}; repin all examples to the same commit carrying current source`);
}
console.log(`CalVer workflow/action/tooling pins agree: ${pin}`);
