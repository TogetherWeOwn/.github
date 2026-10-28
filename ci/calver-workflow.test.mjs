// Static acceptance for the future adapter/workflow, using only the published contract.
// Missing files are failures, not skips. No YAML package, network access or caller execution.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");

// Only top-level mapping boundaries are needed; these workflows use ordinary block mappings.
function blocks(source, indent) {
  const lines = source.split("\n");
  const result = new Map();
  const key = new RegExp(`^ {${indent}}([a-zA-Z_][\\w-]*):(?:\\s|$)`);
  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(key);
    if (!match) continue;
    let end = i + 1;
    while (end < lines.length) {
      if (lines[end].trim() && !lines[end].trimStart().startsWith("#") &&
          lines[end].search(/\S/) <= indent) break;
      end++;
    }
    result.set(match[1], lines.slice(i, end).join("\n"));
  }
  return result;
}

function workflow() {
  const paths = readdirSync(new URL(".github/workflows/", root))
    .filter((name) => /calver.*\.ya?ml$/i.test(name)).map((name) => `.github/workflows/${name}`);
  const candidates = paths.map((path) => ({ path, source: read(path) }))
    .filter(({ source }) => /workflow_call\s*:/.test(source));
  assert.equal(candidates.length, 1, "ship one discoverable reusable CalVer workflow");
  return candidates[0];
}

function uses(source) {
  return [...source.matchAll(/^\s*(?:-\s*)?uses:\s*['"]?([^\s'"#]+)['"]?/gm)].map((match) => match[1]);
}

function assertPins(source, label) {
  for (const ref of uses(source)) {
    if (ref.startsWith("./")) continue;
    assert.ok(/@[a-f0-9]{40}$/.test(ref) || /^docker:\/\/.+@sha256:[a-f0-9]{64}$/.test(ref),
      `${label}: ${ref} must be a full immutable SHA/digest pin`);
  }
}

test("composite action accepts a single JSON request through env, not inline shell interpolation", () => {
  const source = read("actions/calver/action.yml");
  assert.match(source, /using:\s*['"]?composite/);
  const inputs = blocks(blocks(source, 0).get("inputs") ?? "", 2);
  assert.ok(inputs.has("request"), "public adapter accepts one request input");
  assert.match(inputs.get("request"), /required:\s*true/);
  assert.match(source, /env:\s*\n(?:[^\n]*\n)*?\s+[A-Z_][A-Z_0-9]*:\s*['"]?\$\{\{\s*inputs\.request\s*\}\}/,
    "request JSON must enter a process via an environment variable");
  for (const block of source.matchAll(/\brun:\s*\|[-+]?\n((?:[ \t]+.*\n?)*)/g)) {
    assert.doesNotMatch(block[1], /\$\{\{\s*inputs\.request\s*\}\}/,
      "never interpolate untrusted request JSON into a shell program");
  }
  assert.match(source, /github\.action_path/, "the action executes its own tooling, never caller scripts");
  assertPins(source, "composite action");
});

test("action outputs are portable plan plus scalar identities, never a notes filename", () => {
  const source = read("actions/calver/action.yml");
  const outputBlock = blocks(source, 0).get("outputs") ?? "";
  const outputs = blocks(outputBlock, 2);
  for (const name of ["plan", "version", "tag", "sha", "date", "provenance"]) {
    assert.ok(outputs.has(name), `missing public ${name} output`);
  }
  for (const name of outputs.keys()) {
    assert.doesNotMatch(name, /notes[-_]?(?:path|file)|(?:path|file)[-_]?notes/i);
  }
  assert.doesNotMatch(source, /(?:notes[-_]path|notes[-_]file)\s*=/i,
    "runner-local notes paths are not action outputs");
});

test("reusable workflow requires explicit runner JSON and cannot default to a paid hosted runner", () => {
  const { source } = workflow();
  assert.match(source, /request:\s*\n/);
  const runnerInput = [...source.matchAll(/^ {6}([\w-]*(?:runner|runs-on)[\w-]*):\s*\n((?: {8}.*\n?)*)/gm)];
  assert.ok(runnerInput.length > 0, "workflow_call exposes an explicit runner input");
  for (const match of runnerInput) {
    assert.match(match[2], /required:\s*true/);
    assert.doesNotMatch(match[2], /default:\s*.+/);
  }
  assert.match(source, /runs-on:\s*\$\{\{\s*fromJSON\(/);
  assert.doesNotMatch(source, /inputs\.(?:visibility|is[-_]private|repository[-_]visibility)/,
    "caller-supplied visibility is not authoritative");
  assert.doesNotMatch(source, /runs-on:[^\n]*(?:windows-|macos-|ubuntu-(?:large|\d+-core))/i);
});

test("reusable workflow checks shared tooling out separately at an immutable SHA", () => {
  const { source } = workflow();
  assertPins(source, "reusable workflow");
  const checkouts = source.split(/\n(?=\s*- (?:name:|uses:))/)
    .filter((block) => /uses:\s*actions\/checkout@/.test(block) && /repository:\s*['"]?TogetherWeOwn\/\.github['"]?\s*(?:#.*)?$/m.test(block));
  assert.ok(checkouts.length > 0, "checkout must explicitly name the public tooling repository");
  for (const checkout of checkouts) {
    assert.match(checkout, /ref:\s*['"]?[a-f0-9]{40}['"]?\s*(?:#.*)?$/m);
    const path = checkout.match(/path:\s*['"]?([\w./-]+)['"]?\s*(?:#.*)?$/m)?.[1];
    assert.ok(path && path !== "." && !path.startsWith("../") && !path.startsWith("/"),
      "shared tooling gets a separate checkout path");
    assert.match(checkout, /persist-credentials:\s*false/);
    assert.ok(source.includes(`${path}/actions/calver`), "releaser invocation uses that shared tooling checkout");
  }
  assert.doesNotMatch(source, /id-token:\s*write|CLOUDFLARE_API_TOKEN|AWS_SECRET_ACCESS_KEY|AZURE_CLIENT_SECRET/,
    "publication needs GitHub permissions, not cloud credentials");
  assert.match(source, /actions:\s*read/);
});

test("plan-only workflow execution cannot acquire contents:write", () => {
  const { source } = workflow();
  const jobs = blocks(blocks(source, 0).get("jobs") ?? "", 2);
  const topPermissions = blocks(source, 0).get("permissions") ?? "";
  assert.doesNotMatch(topPermissions, /contents:\s*write/, "do not grant write to all phases");
  let writers = 0;
  for (const [name, job] of jobs) {
    if (!/contents:\s*write/.test(job)) continue;
    writers++;
    const condition = blocks(job, 4).get("if") ?? "";
    assert.match(condition, /publish/, `${name}: write-capable job must be publish-phase guarded`);
    assert.match(condition, /fromJSON\(.*request/, `${name}: phase comes from the JSON request`);
  }
  assert.ok(writers > 0, "publish job explicitly requests contents:write");
});

test("CalVer workflow templates pin shared tooling to commits carrying the same content", () => {
  const templates = readdirSync(new URL("workflow-templates/", root))
    .filter((name) => /calver.*\.ya?ml$/i.test(name));
  assert.ok(templates.length > 0, "provide a CalVer caller template");
  let toolingPins = 0;
  for (const name of templates) {
    const source = read(`workflow-templates/${name}`);
    assertPins(source, name);
    for (const ref of uses(source)) {
      const match = ref.match(/^TogetherWeOwn\/\.github\/(.+)@([a-f0-9]{40})$/);
      if (!match) continue;
      toolingPins++;
      const path = match[1].startsWith("actions/") ? `${match[1]}/action.yml` : match[1];
      const pinned = spawnSync("git", ["show", `${match[2]}:${path}`], { cwd: root, encoding: "utf8" });
      assert.equal(pinned.status, 0, `${name}: pinned tooling object must be available in full-history CI: ${pinned.stderr}`);
      assert.equal(pinned.stdout, read(path), `${name}: tooling changed after pin; repin in the feature PR`);
      for (const script of readdirSync(new URL("actions/calver/", root)).filter((file) => /\.mjs$/.test(file))) {
        const scriptPath = `actions/calver/${script}`;
        const content = spawnSync("git", ["show", `${match[2]}:${scriptPath}`], { cwd: root, encoding: "utf8" });
        assert.equal(content.status, 0, `${name}: pin must carry shared ${scriptPath}: ${content.stderr}`);
        assert.equal(content.stdout, read(scriptPath), `${name}: stale shared engine/adapter pin for ${scriptPath}`);
      }
    }
  }
  assert.ok(toolingPins > 0, "templates actually invoke the pinned shared CalVer tooling");
});

test("CalVer acceptance is required by ci-ok and cannot be a shadow/continue-on-error job", () => {
  const source = read(".github/workflows/ci.yml");
  const jobs = blocks(blocks(source, 0).get("jobs") ?? "", 2);
  const gate = jobs.get("ci-ok");
  assert.ok(gate, "required ci-ok aggregator exists");
  const dependencies = blocks(gate, 4).get("needs") ?? "";
  const acceptance = [...jobs].filter(([, job]) => /node\s+--test\s[^\n]*ci\/calver/.test(job));
  assert.ok(acceptance.length > 0, "CI runs node --test ci/calver*.test.mjs (or equivalent explicit files)");
  for (const [name, job] of acceptance) {
    assert.match(dependencies, new RegExp(`\\b${name}\\b`), `${name} must feed required ci-ok`);
    assert.doesNotMatch(job, /continue-on-error:\s*true|\|\|\s*true/);
    const runsAll = /ci\/calver\*\.test\.mjs/.test(job);
    for (const file of ["calver-contract.test.mjs", "calver-acceptance.test.mjs", "calver-workflow.test.mjs"]) {
      assert.ok(runsAll || job.includes(`ci/${file}`), `${name}: CI must run ${file}, not only a subset`);
    }
  }
  assert.match(gate, /if:\s*always\(\)/);
  assert.doesNotMatch(blocks(source, 0).get("on") ?? "", /paths(?:-ignore)?:/,
    "workflow-level path filtering must not suppress the required aggregator");
});
