// Rollback guards of the CENTRAL gradual-promote action.
//
// Ported from two-web-next ci/production-deploy-gate.test.mjs (tests
// 'shared gradual action promotes 5-50-100...' and 'rollback stand-down...'
// plus the live-gate half of 'rollback smoke enforces the same public-route
// set as the gradual live gate' at two-web-next d09b4bee) to this repo,
// pointed at actions/gradual-promote/action.yml. The only intentional
// difference from the originals is the action path: the web copy those tests
// read is retired by the follow-up repoint PR, so without this port the
// rollback chain that runs in production (failure-or-cancelled triggers,
// stand-down outputs, 120s timeout, PREV_VERSION check, no trigger/full-deploy
// side effects) and the final live-traffic gate would merge untested here.
// The traffic-math unit tests and the canary-gate acceptance tests do not read
// these steps (8/8 guard mutants and 4/4 live-gate mutants stay green there),
// so these pins are the ones that fail a broken rollback or live gate.
//
// Run: node --test ci/gradual-promote-action.test.mjs
import assert from "node:assert/strict";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { runPrePromotionCheck, verifierConfig } from "../actions/gradual-promote/pre-promotion-check.mjs";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

test("shared gradual action promotes 5-50-100 with per-stage gates and auto-rollback", () => {
  const action = readFileSync(
    new URL("../actions/gradual-promote/action.yml", import.meta.url),
    "utf8",
  );
  // Ladder order: record rollback target, resolve the new id, then each
  // stage deploys, gates, and rolls back before the next stage starts.
  const order = [
    "Record pre-promote rollback target",
    "Resolve promoted version id",
    "Promote to 5 percent",
    "Health and SLO gate at 5 percent",
    "Live error-rate gate at 5 percent",
    "Automatic rollback after 5 percent gate failure",
    "Promote to 50 percent",
    "Health and SLO gate at 50 percent",
    "Live error-rate gate at 50 percent",
    "Automatic rollback after 50 percent gate failure",
    "Promote to 100 percent",
    "Health and SLO gate at 100 percent (new version)",
    "Health and SLO gate at 100 percent (live traffic)",
    "Live error-rate gate at 100 percent",
    "Automatic rollback after 100 percent gate failure",
  ];
  let at = -1;
  for (const name of order) {
    const next = action.indexOf(`- name: ${name}\n`);
    assert.ok(next > at, `stage out of order or missing: ${name}`);
    at = next;
  }
  // Deterministic specs from code, never interpolated percentages: wrangler
  // refuses a lone TAG@5% (traffic must total 100), so each gradual step
  // names the pre-promote version alongside the new tag.
  assert.equal(
    (action.match(/--deploy-spec "\$VERSION_TAG" (5|50|100) "\$PREV_VERSION"/g) ?? []).length,
    3,
  );
  assert.ok(action.includes("npx wrangler versions deploy --env production"));
  assert.ok(action.includes('npx wrangler rollback "$PREV_VERSION"'));
  // Every rollback runs on any failure or cancellation once a rollback target
  // is recorded: promote API errors and step timeouts included, not just
  // gate failures. Later rollbacks stand down only when an earlier one
  // recorded success (outputs.restored == 'true'); a failed restore leaves
  // the output empty so the next rollback retries (up to three attempts for
  // free). Each restore is time-bounded and refuses an unknown target before
  // wrangler ever runs.
  assert.equal(
    (
      action.match(
        /if: \(failure\(\) \|\| cancelled\(\)\) && steps\.rollback-target\.conclusion == 'success'/g,
      ) ?? []
    ).length,
    3,
  );
  // Success is recorded explicitly after each restore; a wrangler failure
  // under `set -euo pipefail` exits before the echo, leaving no output.
  assert.equal((action.match(/echo "restored=true" >> "\$GITHUB_OUTPUT"/g) ?? []).length, 3);
  assert.ok(action.includes("&& steps.rollback-5.outputs.restored != 'true'"));
  assert.ok(action.includes("&& steps.rollback-50.outputs.restored != 'true'"));
  // A failed rollback must never count as restored: conclusion-based
  // stand-down guards would treat failure as done.
  assert.ok(!action.includes("rollback-5.conclusion == 'skipped'"));
  assert.ok(!action.includes("rollback-50.conclusion == 'skipped'"));
  assert.equal((action.match(/timeout 120 npx wrangler rollback/g) ?? []).length, 3);
  assert.equal(
    (action.match(/: "\$\{PREV_VERSION:\?missing pre-promote rollback target\}"/g) ?? []).length,
    3,
  );
  // Four smoke loops exit 0 on success and exit 1 when retries run out;
  // three rollback steps exit 1 after restoring the pre-promote version.
  assert.equal((action.match(/^            exit 0$/gm) ?? []).length, 4);
  assert.equal((action.match(/^        exit 1$/gm) ?? []).length, 7);
  // Canary stages probe the new version directly; the final stage also probes
  // live traffic. No curl one-offs, no trigger or full deploys.
  // The override pins probes to the new version in the checker's quoted
  // worker="uuid" shape; the checker rejects an unquoted value before any
  // probe, so an unquoted shell would fail every canary gate into rollback.
  assert.equal(
    (action.match(/--version-override "\$WORKER_NAME=\\"\$NEW_VERSION\\""/g) ?? []).length,
    3,
  );
  assert.ok(!action.includes("curl "));
  assert.ok(!action.includes("wrangler triggers deploy"));
  assert.ok(!action.includes("wrangler deploy --env production"));
});

test("rollback stand-down retries promote failures, cancellations and failed restores", () => {
  const action = readFileSync(
    new URL("../actions/gradual-promote/action.yml", import.meta.url),
    "utf8",
  );
  const guard50 = action.match(
    /- name: Automatic rollback after 50 percent gate failure\n(?:.*\n)*? {6}if: (.*)\n/,
  )?.[1];
  const guard100 = action.match(
    /- name: Automatic rollback after 100 percent gate failure\n(?:.*\n)*? {6}if: (.*)\n/,
  )?.[1];
  assert.ok(guard50, "rollback-50 has an if: guard");
  assert.ok(guard100, "rollback-100 has an if: guard");
  // Both guards run on failure or cancellation with a recorded target.
  for (const guard of [guard50, guard100]) {
    assert.match(guard, /\(failure\(\) \|\| cancelled\(\)\)/);
    assert.match(guard, /steps\.rollback-target\.conclusion == 'success'/);
  }
  // Stand-down is success-explicit: only outputs.restored == 'true' holds the
  // next rollback back. Empty means skipped (earlier stage never ran) or a
  // failed/cancelled restore that never reached the echo.
  const wouldRun50 = (restored5) => restored5 !== "true";
  const wouldRun100 = (restored5, restored50) => restored5 !== "true" && restored50 !== "true";
  assert.ok(guard50.includes("steps.rollback-5.outputs.restored != 'true'"));
  assert.ok(guard100.includes("steps.rollback-5.outputs.restored != 'true'"));
  assert.ok(guard100.includes("steps.rollback-50.outputs.restored != 'true'"));
  const cases = [
    // [label, restored5, restored50, run50, run100]
    // A failed or cancelled restore never reaches the echo, so its output is
    // empty — exactly like a skipped step — and the next rollback retries.
    ["promote failure before any rollback", "", "", true, true],
    ["cancelled before any rollback", undefined, undefined, true, true],
    ["failed 5 percent restore leaves no output so 50 retries", "", "", true, true],
    ["successful 5 percent restore stands down later steps", "true", "", false, false],
    ["successful 50 percent restore stands down the last step", "", "true", true, false],
    ["both restores recorded stand down everything", "true", "true", false, false],
  ];
  for (const [label, restored5, restored50, run50, run100] of cases) {
    assert.equal(wouldRun50(restored5 ?? ""), run50, label);
    assert.equal(wouldRun100(restored5 ?? "", restored50 ?? ""), run100, label);
  }
});

test("live error-rate gates run the gate script with the promoted version and exact env", () => {
  // The integration this action delivers: each gate runs between its smoke
  // gate and rollback (order pinned above), only when enabled, judging the
  // PROMOTED version with the env names configFromEnv parses. Without
  // these pins, deleting a gate step, renaming an env var, pointing
  // NEW_VERSION at the rollback target, or appending `&& false` to the
  // guard all leave the suites green while the promotion goes ungated.
  const action = readFileSync(
    new URL("../actions/gradual-promote/action.yml", import.meta.url),
    "utf8",
  );
  assert.equal((action.match(/- name: Live error-rate gate at \d+ percent/g) ?? []).length, 3);
  // The rehearsal fixture override is never wired into the action.
  assert.ok(!action.includes("ERROR_RATE_FIXTURE"));
  const stepBlock = (name) => {
    const start = action.indexOf(`    - name: ${name}\n`);
    assert.ok(start >= 0, `step missing: ${name}`);
    const rest = action.slice(start + 1);
    const next = rest.indexOf("\n    - name: ");
    return next < 0 ? rest : rest.slice(0, next);
  };
  // Each smoke gate records when it finished, 1s past the finish: a
  // whole-second stamp would round down, and the analytics lower bound
  // is inclusive, so probes sent in the final second would still count.
  // The error-rate gate starts its judged window there, so the stage's
  // own probes never count. The stamp must fail closed: an empty value
  // would read as "no recorded start" and revive the trailing window.
  assert.equal(
    (
      action.match(
        /finished_at=\$\(node -e "process\.stdout\.write\(new Date\(Date\.now\(\) \+ 1000\)\.toISOString\(\)\)"\)/g,
      ) ?? []
    ).length,
    3,
  );
  assert.equal(
    (
      action.match(
        /if \[ -z "\$finished_at" \]; then echo "::error::could not stamp the smoke finish time" >&2; exit 1; fi/g,
      ) ?? []
    ).length,
    3,
  );
  assert.equal(
    (action.match(/echo "finished-at=\$finished_at" >> "\$GITHUB_OUTPUT"/g) ?? []).length,
    3,
  );
  for (const [pct, smoke] of [
    [5, "smoke-5"],
    [50, "smoke-50"],
    [100, "smoke-100"],
  ]) {
    const block = stepBlock(`Live error-rate gate at ${pct} percent`);
    // Trailing newline pins the whole line: `... && false` must not match.
    assert.ok(block.includes("if: inputs.error-rate-gate == 'true'\n"), `gate ${pct} if guard`);
    // Same whole-line pin: a trailing `|| true` must not match.
    assert.ok(
      block.includes('run: node "$ACTION_PATH/error-rate-gate.mjs"\n'),
      `gate ${pct} runs the gate script`,
    );
    for (const envLine of [
      "WORKER_NAME: ${{ inputs.worker-name }}",
      "NEW_VERSION: ${{ steps.promoted-version.outputs.id }}",
      "CLOUDFLARE_API_TOKEN: ${{ inputs.api-token }}",
      "CLOUDFLARE_ACCOUNT_ID: ${{ inputs.account-id }}",
      "ERROR_RATE_THRESHOLD: ${{ inputs.error-rate-threshold }}",
      "ERROR_RATE_MIN_REQUESTS: ${{ inputs.error-rate-min-requests }}",
      "ERROR_RATE_WINDOW_MINUTES: ${{ inputs.error-rate-window-minutes }}",
      "ERROR_RATE_SOAK_SECONDS: ${{ inputs.error-rate-soak-seconds }}",
      `ERROR_RATE_WINDOW_START: \${{ steps.${smoke}.outputs.finished-at }}`,
    ]) {
      assert.ok(block.includes(envLine), `gate ${pct} env: ${envLine}`);
    }
    // The gate judges the promoted version, never the rollback target.
    assert.ok(!block.includes("rollback-target"), `gate ${pct} must not read the rollback target`);
    // A swallowed failure would never reach the rollback step.
    assert.ok(!block.includes("continue-on-error"), `gate ${pct} must not swallow its failure`);
  }
});

test("live-traffic gate at 100 percent probes production without version override", () => {
  // Live-gate half of two-web-next 'rollback smoke enforces the same
  // public-route set as the gradual live gate' (d09b4bee:775), pointed at the
  // central action. smoke-100 is the only probe of unpinned live traffic after
  // the 100% promote and triggers the third rollback; the acceptance suite
  // runs only smoke-5, smoke-50 and smoke-100-override, so without this pin
  // the four live-gate mutants below stay green here (they fail the web test).
  const action = readFileSync(
    new URL("../actions/gradual-promote/action.yml", import.meta.url),
    "utf8",
  );
  const live = action.match(
    /    - name: Health and SLO gate at 100 percent \(live traffic\)\n(?:.*\n)*?      run: \|\n((?:        .*\n)+)/,
  )?.[1];
  assert.ok(live, "smoke-100 run block found");
  assert.match(live, /node bin\/smoke\.mjs /);
  assert.match(live, /for i in 1 2 3 4 5 6/);
  assert.ok(!live.includes("curl "));
  // Live traffic probes the production domain through $DOMAIN with the shared
  // $INDEXABLE flag, and never carries --version-override (the canary
  // override must not leak into the live probe).
  assert.match(live, /\$INDEXABLE/);
  assert.ok(live.includes('node bin/smoke.mjs "https://$DOMAIN"'));
  assert.ok(live.includes('node bin/smoke.mjs "https://$DOMAIN" $INDEXABLE'));
  assert.ok(!live.includes("--version-override"));
  // The gate must fail closed: replacing the probe with `if true` would let
  // the 100% stage pass without probing and suppress the third rollback.
  assert.match(live, /if node bin\/smoke\.mjs "https:\/\/\$DOMAIN" \$INDEXABLE; then/);
});

const PREVIOUS = "12345678-1234-1234-1234-123456789abc";
const PROMOTED = "22345678-1234-1234-1234-123456789abc";

// Execute the manifest's shell, not a restatement of its node command. Only
// Wrangler is faked; the copied helper and analytics gate are the shipped bytes.
function manifestHarness(t, pathName) {
  const root = mkdtempSync(join(tmpdir(), "promotion-manifest-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const tools = join(root, "tools");
  const actionPath = join(root, pathName);
  mkdirSync(tools);
  mkdirSync(actionPath);
  symlinkSync(process.execPath, join(tools, "node"));
  symlinkSync("/usr/bin/timeout", join(tools, "timeout"));
  symlinkSync("/usr/bin/git", join(tools, "git"));
  for (const name of ["gradual-promote.mjs", "error-rate-gate.mjs", "pre-promotion-check.mjs"]) {
    copyFileSync(new URL(`../actions/gradual-promote/${name}`, import.meta.url), join(actionPath, name));
  }
  writeFileSync(join(tools, "npx"), `#!${process.execPath}
import { appendFileSync } from "node:fs";
const args = process.argv.slice(2);
appendFileSync(process.env.NPX_LOG, JSON.stringify(args) + "\\n");
if (process.env.NPX_STATUS) process.exit(Number(process.env.NPX_STATUS));
if (args[1] === "deployments") console.log(process.env.DEPLOYMENTS);
else if (args[1] === "versions" && args[2] === "list") console.log(process.env.VERSIONS);
else if (args[1] !== "rollback" && !(args[1] === "versions" && args[2] === "deploy")) process.exit(2);
`, { mode: 0o700 });
  const action = readFileSync(new URL("../actions/gradual-promote/action.yml", import.meta.url), "utf8");
  const steps = action.split(/\n(?=    - name: )/).slice(1);
  const fixture = join(root, "analytics.json");
  const output = join(root, "output");
  const log = join(root, "npx.log");
  const bindings = {
    "github.action_path": actionPath,
    "inputs.worker-name": "fixture-worker",
    "inputs.version-tag": "release-1",
    "inputs.api-token": "",
    "inputs.account-id": "a".repeat(32),
    "steps.rollback-target.outputs.target": PREVIOUS,
    "steps.promoted-version.outputs.id": PROMOTED,
    "inputs.pre-promotion-verifier": "",
    "inputs.require-pre-promotion-verifier": "false",
    "inputs.pre-promotion-timeout-seconds": "120",
    "inputs.error-rate-threshold": "0.05",
    "inputs.error-rate-min-requests": "20",
    "inputs.error-rate-window-minutes": "10",
    "inputs.error-rate-soak-seconds": "0",
    ...Object.fromEntries([5, 50, 100].map((pct) => [
      `steps.smoke-${pct}.outputs.finished-at`, new Date(Date.now() - 60_000).toISOString(),
    ])),
  };
  function run(id, overrides = {}, errors = 0) {
    const step = steps.find((s) => s.includes(`      id: ${id}\n`));
    assert.ok(step, `step ${id} exists`);
    const match = step.match(/^      run: (\|\n((?:        .*\n)+)|([^\n]+)\n)/m);
    assert.ok(match, `${id} has a shell block`);
    const rawScript = match[2] ? match[2].split("\n").map((line) => line.slice(8)).join("\n") : match[3];
    const script = rawScript.replaceAll("${{ github.action_path }}", actionPath);
    const env = {};
    for (const [, name, expression] of step.matchAll(/^        ([A-Z_]+): \$\{\{ (.+) \}\}$/gm)) {
      assert.ok(Object.hasOwn(bindings, expression), `known binding ${expression}`);
      env[name] = bindings[expression];
    }
    writeFileSync(output, "");
    writeFileSync(log, "");
    writeFileSync(fixture, JSON.stringify({ data: { viewer: { accounts: [{
      workersInvocationsAdaptive: [{ dimensions: { scriptVersion: PROMOTED }, sum: { requests: 100, errors } }],
    }] } } }));
    const result = spawnSync("/bin/bash", ["--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script], {
      cwd: root,
      env: {
        PATH: tools,
        GITHUB_OUTPUT: output,
        GITHUB_WORKSPACE: root,
        ...Object.fromEntries([...step.matchAll(/^        ([A-Z_]+): "([^"\n]*)"$/gm)].map(([, key, value]) => [key, value])),
        NPX_LOG: log,
        DEPLOYMENTS: JSON.stringify([{ created_on: "2026-01-01", versions: [{ version_id: PREVIOUS, percentage: 100 }] }]),
        VERSIONS: JSON.stringify([{ id: PROMOTED, annotations: { "workers/tag": "release-1" } }]),
        ERROR_RATE_FIXTURE: fixture,
        ...env,
        ...overrides,
        PATH: tools, // Never let verifier fixtures escape the mocked Wrangler.
      },
      encoding: "utf8",
      timeout: 10_000,
    });
    assert.ifError(result.error);
    return {
      ...result,
      output: readFileSync(output, "utf8"),
      calls: readFileSync(log, "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line)),
    };
  }
  return { run, steps, root };
}

for (const pathName of ["shared action with spaces", "shared action ü \"quoted\" 'single' $literal [one]"]) {
  test(`manifest resolves every action-owned script under ${pathName}`, (t) => {
    const { run, steps } = manifestHarness(t, pathName);
    const ids = ["rollback-target", "promoted-version", "deploy-5", "deploy-50", "deploy-100", "error-rate-5", "error-rate-50", "error-rate-100"];
    const target = run("rollback-target");
    assert.equal(target.status, 0, target.stderr);
    assert.equal(steps.filter((s) => s.includes("ACTION_PATH: ${{ github.action_path }}")).length, 8);
    for (const id of ids) {
      assert.ok(steps.find((s) => s.includes(`      id: ${id}\n`)).includes("ACTION_PATH: ${{ github.action_path }}"), id);
    }
    assert.equal(target.output, `target=${PREVIOUS}\n`);
    assert.deepEqual(target.calls, [["wrangler", "deployments", "list", "--env", "production", "--name", "fixture-worker", "--json"]]);
    const promoted = run("promoted-version");
    assert.equal(promoted.status, 0, promoted.stderr);
    assert.equal(promoted.output, `id=${PROMOTED}\n`);
    assert.deepEqual(promoted.calls, [["wrangler", "versions", "list", "--env", "production", "--name", "fixture-worker", "--json"]]);
    for (const pct of [5, 50, 100]) {
      const deploy = run(`deploy-${pct}`);
      assert.equal(deploy.status, 0, deploy.stderr);
      assert.deepEqual(deploy.calls, [["wrangler", "versions", "deploy", "--env", "production", "--name", "fixture-worker",
        ...(pct < 100 ? [`${PREVIOUS}@${100 - pct}`] : []), "--version-tag", `release-1@${pct}%`, "--yes"]]);
      const gate = run(`error-rate-${pct}`);
      assert.equal(gate.status, 0, gate.stderr);
      assert.match(gate.stdout, /verdict=pass/);
      assert.deepEqual(gate.calls, []);
      const failedGate = run(`error-rate-${pct}`, {}, 40);
      assert.equal(failedGate.status, 1, failedGate.stderr);
      assert.match(failedGate.stdout, /verdict=fail/);
    }
  });
}

test("manifest script failures remain failures and rollback records only successful restores", (t) => {
  const { run } = manifestHarness(t, "shared action with spaces");
  for (const id of ["rollback-target", "promoted-version"]) {
    const malformed = run(id, { DEPLOYMENTS: "not-json", VERSIONS: "not-json" });
    assert.equal(malformed.status, 1, malformed.stderr);
    assert.equal(malformed.output, "");
    const failedWrangler = run(id, { NPX_STATUS: "7" });
    assert.notEqual(failedWrangler.status, 0);
    assert.equal(failedWrangler.output, "");
  }
  for (const pct of [5, 50, 100]) {
    assert.equal(run(`deploy-${pct}`, { NPX_STATUS: "7" }).status, 7);
    const badSpec = run(`deploy-${pct}`, { VERSION_TAG: "invalid tag" });
    assert.notEqual(badSpec.status, 0);
    assert.deepEqual(badSpec.calls, [], "bad helper output must never reach Wrangler");
    const restored = run(`rollback-${pct}`);
    assert.equal(restored.status, 1, "rollback still fails the job after restoring");
    assert.equal(restored.output, "restored=true\n");
    assert.deepEqual(restored.calls, [["wrangler", "rollback", PREVIOUS, "--name", "fixture-worker", "--yes"]]);
    const failedRestore = run(`rollback-${pct}`, { NPX_STATUS: "7" });
    assert.equal(failedRestore.status, 7);
    assert.equal(failedRestore.output, "");
    const missingTarget = run(`rollback-${pct}`, { PREV_VERSION: "" });
    assert.notEqual(missingTarget.status, 0);
    assert.deepEqual(missingTarget.calls, []);
  }
});

function checkedInVerifier(root, source = "process.exit(0);") {
  mkdirSync(join(root, "checks"), { recursive: true });
  writeFileSync(join(root, "checks", "recovery.mjs"), source);
  execFileSync("/usr/bin/git", ["init", "-q", root]);
  execFileSync("/usr/bin/git", ["-C", root, "add", "checks/recovery.mjs"]);
}

function verifierEnv(root, overrides = {}) {
  return { PATH: process.env.PATH, GITHUB_WORKSPACE: root,
    PRE_PROMOTION_VERIFIER: "checks/recovery.mjs", REQUIRE_PRE_PROMOTION_VERIFIER: "true",
    PRE_PROMOTION_TIMEOUT_SECONDS: "1", CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
    WORKER_NAME: "fixture-worker", VERSION_TAG: "release-1", NEW_VERSION: PROMOTED,
    PREV_VERSION: PREVIOUS, PROMOTION_PERCENTAGE: "5", ...overrides };
}

test("verifier rejects missing, unsafe, untracked and malformed configuration", (t) => {
  const { root } = manifestHarness(t, "action");
  checkedInVerifier(root);
  assert.equal(verifierConfig(verifierEnv(root)).timeoutMs, 1000);
  assert.equal(verifierConfig({}), null, "unchanged callers have no callback");
  for (const path of ["", "/tmp/recovery.mjs", "../recovery.mjs", "checks/../checks/recovery.mjs",
    "checks//recovery.mjs", "checks/./recovery.mjs", "checks/recovery.mjs;true", "checks/$(id).mjs",
    "checks/recovery.js", "checks/missing.mjs", ".git/config.mjs", "checks/ü.mjs"]) {
    assert.throws(() => verifierConfig(verifierEnv(root, { PRE_PROMOTION_VERIFIER: path })), undefined, path);
  }
  writeFileSync(join(root, "checks", "untracked.mjs"), "process.exit(0)");
  symlinkSync(join(root, "checks", "recovery.mjs"), join(root, "checks", "link.mjs"));
  symlinkSync(join(root, "checks"), join(root, "linked"));
  for (const path of ["checks/untracked.mjs", "checks/link.mjs", "linked/recovery.mjs"]) {
    assert.throws(() => verifierConfig(verifierEnv(root, { PRE_PROMOTION_VERIFIER: path })));
  }
  for (const timeout of ["", "0", "301", "-1", "1.5", "1e2", " 1", "1;true"]) {
    assert.throws(() => verifierConfig(verifierEnv(root, { PRE_PROMOTION_TIMEOUT_SECONDS: timeout })));
  }
  for (const overrides of [{ REQUIRE_PRE_PROMOTION_VERIFIER: "yes" }, { NEW_VERSION: "unknown" },
    { PREV_VERSION: "unknown" }, { VERSION_TAG: "bad tag" }, { WORKER_NAME: "--bad" },
    { PROMOTION_PERCENTAGE: "05" }, { PROMOTION_PERCENTAGE: "10" }, { GITHUB_WORKSPACE: "." }]) {
    assert.throws(() => verifierConfig(verifierEnv(root, overrides)));
  }
});

test("injected verifier process receives exact argv, environment and failure status without a shell", async (t) => {
  const { root } = manifestHarness(t, "action");
  checkedInVerifier(root);
  for (const pct of [5, 50, 100]) {
    const env = verifierEnv(root, { PROMOTION_PERCENTAGE: String(pct), RECOVERY_CREDENTIAL: "not-logged" });
    await runPrePromotionCheck(env, (exe, args, options) => {
      assert.equal(exe, process.execPath);
      assert.deepEqual(args, ["--", join(root, "checks/recovery.mjs"),
        "--candidate-version-id", PROMOTED, "--candidate-tag", "release-1",
        "--worker-name", "fixture-worker", "--account-id", "a".repeat(32),
        "--environment", "production", "--percentage", String(pct), "--previous-version-id", PREVIOUS]);
      assert.equal(options.shell, false);
      assert.equal(options.stdio, "ignore");
      assert.equal(options.detached, true);
      assert.equal(options.env.RECOVERY_CREDENTIAL, "not-logged");
      const child = new EventEmitter();
      queueMicrotask(() => child.emit("close", 0, null));
      return child;
    });
  }
  for (const event of ["nonzero", "signal", "error", "throw"]) {
    await assert.rejects(runPrePromotionCheck(verifierEnv(root), () => {
      if (event === "throw") throw new Error("private detail");
      const child = new EventEmitter();
      queueMicrotask(() => {
        if (event === "error") child.emit("error", new Error("private detail"));
        else child.emit("close", event === "nonzero" ? 7 : null, event === "signal" ? "SIGTERM" : null);
      });
      return child;
    }), /pre-promotion verifier/);
  }
});

test("manifest executes verification immediately before every mutation and suppresses secret output", (t) => {
  const { root, run, steps } = manifestHarness(t, "shared action $literal");
  checkedInVerifier(root, `import { appendFileSync } from "node:fs";
console.log("private-verifier-output"); console.error("private-verifier-error");
appendFileSync(process.env.NPX_LOG, JSON.stringify(["verify", ...process.argv.slice(2)]) + "\\n");`);
  for (const pct of [5, 50, 100]) {
    const block = steps.find((s) => s.includes(`      id: deploy-${pct}\n`));
    assert.match(block, /node "\$ACTION_PATH\/pre-promotion-check.mjs"\n        npx wrangler versions deploy/);
    assert.ok(!block.includes("continue-on-error"));
    const result = run(`deploy-${pct}`, verifierEnv(root, { PROMOTION_PERCENTAGE: String(pct) }));
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.calls[0][0], "verify");
    assert.ok(result.calls[0].includes(PROMOTED));
    assert.ok(result.calls[0].includes(PREVIOUS));
    assert.deepEqual(result.calls[0].slice(-4), ["--percentage", String(pct), "--previous-version-id", PREVIOUS]);
    assert.deepEqual(result.calls[1].slice(0, 3), ["wrangler", "versions", "deploy"]);
    assert.equal(result.calls.length, 2);
    assert.doesNotMatch(result.stdout + result.stderr, /private-verifier/);
  }
});

for (const mode of ["missing", "nonzero", "timeout"]) {
  for (const failedStage of [5, 50, 100]) {
    test(`verifier ${mode} at ${failedStage} stops promotion and retains recorded rollback`, (t) => {
      const { root, run, steps } = manifestHarness(t, "action");
      checkedInVerifier(root, `const pct = process.argv[process.argv.indexOf("--percentage") + 1];
if (pct === "${failedStage}") {
  ${mode === "timeout" ? "setInterval(() => {}, 100);" : "process.exit(9);"}
}`);
      const promoted = [];
      for (const pct of [5, 50, 100]) {
        const env = verifierEnv(root, { PROMOTION_PERCENTAGE: String(pct) });
        if (mode === "missing" && pct === failedStage) env.PRE_PROMOTION_VERIFIER = "checks/missing.mjs";
        const result = run(`deploy-${pct}`, env);
        if (result.status !== 0) {
          assert.equal(pct, failedStage);
          assert.deepEqual(result.calls, [], "refusal must precede Wrangler");
          const rollback = run(`rollback-${pct}`);
          assert.equal(rollback.output, "restored=true\n");
          assert.equal(rollback.status, 1, "restore must not erase failure");
          assert.deepEqual(rollback.calls, [["wrangler", "rollback", PREVIOUS, "--name", "fixture-worker", "--yes"]]);
          // Native success() skips subsequent deploys after this failed step.
          const block = steps.find((s) => s.includes(`      id: deploy-${pct}\n`));
          assert.ok(!block.includes("      if:"), "default success() semantics remain intact");
          break;
        }
        promoted.push(pct);
      }
      assert.deepEqual(promoted, [5, 50, 100].filter((pct) => pct < failedStage));
    });
  }
}

test("cancellation terminates verifier group and refuses the pending promotion", async (t) => {
  const { root } = manifestHarness(t, "action");
  checkedInVerifier(root, `import { writeFileSync } from "node:fs";
writeFileSync(process.env.READY_FILE, "ready"); setInterval(() => {}, 100);`);
  const ready = join(root, "ready");
  // A test-only injected spawn signals the helper after its child starts.
  await assert.rejects(runPrePromotionCheck(verifierEnv(root, { READY_FILE: ready }), (...args) => {
    const child = spawn(...args);
    child.once("spawn", () => process.emit("SIGTERM"));
    return child;
  }), /cancelled/);
});
