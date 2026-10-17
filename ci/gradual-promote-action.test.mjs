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
import { readFileSync } from "node:fs";
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
  for (const pct of [5, 50, 100]) {
    const block = stepBlock(`Live error-rate gate at ${pct} percent`);
    // Trailing newline pins the whole line: `... && false` must not match.
    assert.ok(block.includes("if: inputs.error-rate-gate == 'true'\n"), `gate ${pct} if guard`);
    // Same whole-line pin: a trailing `|| true` must not match.
    assert.ok(
      block.includes('run: node "${{ github.action_path }}/error-rate-gate.mjs"\n'),
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
    ]) {
      assert.ok(block.includes(envLine), `gate ${pct} env: ${envLine}`);
    }
    // The gate judges the promoted version, never the rollback target.
    assert.ok(!block.includes("rollback-target"), `gate ${pct} must not read the rollback target`);
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
