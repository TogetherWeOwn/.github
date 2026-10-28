import { readFileSync, readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const examples = readFileSync(new URL('./examples.md', import.meta.url), 'utf8');
const workflowPins = [...examples.matchAll(/^\s*uses: TogetherWeOwn\/\.github\/\.github\/workflows\/calver\.yml@([0-9a-f]{40})\s*$/gm)].map(match => match[1]);
const toolingPins = [...examples.matchAll(/^\s*tooling-sha: ([0-9a-f]{40})\s*$/gm)].map(match => match[1]);
const actionPins = [...examples.matchAll(/^\s*- uses: TogetherWeOwn\/\.github\/actions\/calver@([0-9a-f]{40})\s*$/gm)].map(match => match[1]);
if (workflowPins.length !== 1 || toolingPins.length !== 1 || actionPins.length !== 2 || [...workflowPins, ...toolingPins, ...actionPins].some(pin => pin !== workflowPins[0])) throw new Error('examples must pin workflow, tooling and both adapter steps to the same full SHA');
const pin = workflowPins[0];
const templates = readdirSync(new URL('../../workflow-templates/', import.meta.url)).filter(name => /calver.*\.ya?ml$/i.test(name));
if (!templates.length) throw new Error('CalVer caller template required');
for (const name of templates) {
  const source = readFileSync(new URL(`../../workflow-templates/${name}`, import.meta.url), 'utf8');
  const refs = [...source.matchAll(/uses:\s*TogetherWeOwn\/\.github\/[^\s@]+@([^\s#]+)/g)];
  if (!refs.length || refs.some(match => match[1] !== pin)) throw new Error(`CalVer template ${name} must use the same immutable tooling pin`);
}
const git = (...args) => execFileSync('git', ['-C', root, ...args], { maxBuffer: 1024 * 1024, timeout: 30000 });
try { git('cat-file', '-e', `${pin}^{commit}`); }
catch { git('fetch', '--no-tags', 'origin', pin); }
const scripts = readdirSync(new URL('./', import.meta.url)).filter(name => name.endsWith('.mjs')).map(name => `actions/calver/${name}`);
for (const path of ['.github/workflows/calver.yml', 'actions/calver/action.yml', ...scripts]) {
  if (!git('show', `${pin}:${path}`).equals(readFileSync(new URL(`../../${path}`, import.meta.url)))) throw new Error(`CalVer examples pin stale tooling: ${path}; repin all examples to the same commit carrying current source`);
}
console.log(`CalVer workflow/action/tooling pins agree: ${pin}`);
