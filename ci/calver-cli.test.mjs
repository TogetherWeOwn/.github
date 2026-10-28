// Independent CLI acceptance: executes actions/calver/cli.mjs as a real child
// process with a deterministic fetch preloaded through Node --import.
//
// Blind to implementation: this file never imports calver.mjs, github.mjs or
// cli.mjs. It only reads the documented contract (actions/calver/README.md,
// actions/calver/contract.md, actions/calver/action.yml) and reuses the
// engine-fixture conventions from ci/calver-*.test.mjs (fixed SHAs, frozen
// clock, standard GitHub REST JSON shapes).
//
// Zero live network or credentials: every child runs with an isolated env
// (host secrets are never inherited), a fake CALVER_TOKEN sentinel, and a
// fetch replacement that serves standard GitHub REST JSON, fails unknown
// routes with HTTP 500, and records every method/url/body to a trace file.
// The production transport inside the child stays actual.
//
// Run: node --test ci/calver-cli.test.mjs  (from the repository root)
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const CLI_PATH = fileURLToPath(new URL("../actions/calver/cli.mjs", import.meta.url));

const SHA = "a".repeat(40);
const OTHER = "b".repeat(40);
const OLD = "c".repeat(40);
const STAGED_REPO = "TogetherWeOwn/two-web-next";
const DIGEST_REPO = "TogetherWeOwn/two-bot-next";
// Frozen inside every child via the preloader, so allocation is independent of
// the runner's real UTC date. Matches the engine fixtures' clock.
const FROZEN_NOW = "2026-10-28T23:59:58Z";
const FROZEN_DATE = "2026-10-28";
const FAKE_TOKEN = "cli-test-fake-token-9f2c4d";
const EVIDENCE = {
  runId: 901, attempt: 2, workflow: ".github/workflows/staging.yml",
  jobs: ["staging", "acceptance"], artifactId: 77, artifactName: "staging-certificate",
  artifactDigest: "sha256:" + "d".repeat(64),
};

// The --import module created fresh in a tmp dir for every child run. It reads
// its fixture, trace and state paths from the isolated child env, freezes Date,
// replaces global fetch with a deterministic GitHub REST fake, and touches
// nothing else: the production transport in cli.mjs runs unmodified.
const PRELOADER_SOURCE = [
  "import fs from 'node:fs';",
  "",
  "const fixture = JSON.parse(fs.readFileSync(process.env.CALVER_TEST_FIXTURE, 'utf8'));",
  "const tracePath = process.env.CALVER_TEST_TRACE;",
  "const statePath = process.env.CALVER_TEST_STATE;",
  "",
  "if (fixture.frozenNow) {",
  "  const frozen = new Date(fixture.frozenNow).getTime();",
  "  const RealDate = Date;",
  "  class FrozenDate extends RealDate {",
  "    constructor(...args) { if (args.length === 0) { super(frozen); } else { super(...args); } }",
  "    static now() { return frozen; }",
  "  }",
  "  globalThis.Date = FrozenDate;",
  "}",
  "",
  "function loadState() {",
  "  try {",
  "    return JSON.parse(fs.readFileSync(statePath, 'utf8'));",
  "  } catch {",
  "    return { refs: {}, tagObjects: {}, peeled: {}, tagsExtra: [], releases: [] };",
  "  }",
  "}",
  "function saveState(state) { fs.writeFileSync(statePath, JSON.stringify(state)); }",
  "function record(method, url, body) {",
  "  fs.appendFileSync(tracePath, JSON.stringify({ method, url, body: body === undefined ? null : body }) + '\\n');",
  "}",
  "function jsonResponse(obj, status, headers) {",
  "  const merged = { 'content-type': 'application/json' };",
  "  for (const key of Object.keys(headers || {})) { merged[key] = headers[key]; }",
  "  return new Response(JSON.stringify(obj), { status: status || 200, headers: merged });",
  "}",
  "function fullCommit(sha) {",
  "  return { sha, parents: [{ sha: fixture.old }],",
  "    html_url: 'https://github.com/' + process.env.GITHUB_REPOSITORY + '/commit/' + sha,",
  "    commit: { message: 'feat: cli acceptance change',",
  "      author: { name: 'CLI Fixture', date: '2026-10-28T10:00:00Z' },",
  "      committer: { name: 'CLI Fixture', date: '2026-10-28T10:00:00Z' } } };",
  "}",
  "function repoObject() {",
  "  const parts = process.env.GITHUB_REPOSITORY.split('/');",
  "  return { id: 123, name: parts[1], full_name: process.env.GITHUB_REPOSITORY,",
  "    owner: { login: parts[0] }, private: false, visibility: 'public', default_branch: 'main' };",
  "}",
  "",
  "globalThis.fetch = async function (url, init) {",
  "  const options = init || {};",
  "  const method = String(options.method || 'GET').toUpperCase();",
  "  const parsed = new URL(String(url));",
  "  if (parsed.origin !== 'https://api.github.com' || parsed.username || parsed.password || parsed.hash) {",
  "    throw new Error('CLI fixture refused an unsafe API URL');",
  "  }",
  "  const path = decodeURIComponent(parsed.pathname).replace(/\\/$/, '');",
  "  let body;",
  "  if (options.body !== undefined) {",
  "    try { body = JSON.parse(options.body); } catch { body = String(options.body); }",
  "  }",
  "  record(method, String(url), body === undefined ? null : body);",
  "  const repository = process.env.GITHUB_REPOSITORY;",
  "  const root = '/repos/' + repository;",
  "  for (const fault of fixture.faults || []) {",
  "    if ((fault.method || 'GET') === method &&",
  "        (parsed.pathname + parsed.search).indexOf(fault.contains) !== -1) {",
  "      return jsonResponse({ message: fault.message || 'cli-test injected fault' }, fault.status || 500);",
  "    }",
  "  }",
  "  if (method === 'GET' && path === root) { return jsonResponse(repoObject()); }",
  "  if (method === 'GET' && path === root + '/branches/main') {",
  "    return jsonResponse({ name: 'main', commit: { sha: fixture.sha }, protected: true });",
  "  }",
  "  if (method === 'GET' && path.indexOf(root + '/git/commits/') === 0) {",
  "    if (path.slice((root + '/git/commits/').length) !== fixture.sha) {",
  "      return jsonResponse({ message: 'Not Found' }, 404);",
  "    }",
  "    return jsonResponse({ sha: fixture.sha, message: 'feat: cli acceptance change',",
  "      author: { name: 'CLI Fixture', email: 'fixture@example.com', date: '2026-10-28T10:00:00Z' },",
  "      committer: { name: 'CLI Fixture', email: 'fixture@example.com', date: '2026-10-28T10:00:00Z' },",
  "      parents: [{ sha: fixture.old }], tree: { sha: 't'.repeat(40) } });",
  "  }",
  "  if (method === 'GET' && path.indexOf(root + '/compare/') === 0) {",
  "    const pair = path.slice((root + '/compare/').length).split('...');",
  "    if (pair[1] === 'main') {",
  "      return jsonResponse({ status: 'ahead', ahead_by: 1, behind_by: 0, total_commits: 1,",
  "        base_commit: { sha: fixture.old }, merge_base_commit: { sha: fixture.old },",
  "        commits: [fullCommit(fixture.sha)] });",
  "    }",
  "    const status = fixture.digestCompare || 'ahead';",
  "    const ahead = status === 'ahead' || status === 'diverged';",
  "    const behind = status === 'behind' || status === 'diverged';",
  "    const total = status === 'ahead' ? 1 : 0;",
  "    return jsonResponse({ status, ahead_by: ahead ? 1 : 0, behind_by: behind ? 1 : 0,",
  "      total_commits: total, base_commit: { sha: pair[0] }, merge_base_commit: { sha: pair[0] },",
  "      commits: total ? [fullCommit(fixture.sha)] : [] });",
  "  }",
  "  if (method === 'GET' && (path === root + '/actions/runs/901' ||",
  "      path === root + '/actions/runs/901/attempts/2')) {",
  "    if (!fixture.staged) { return jsonResponse({ message: 'Not Found' }, 404); }",
  "    const s = fixture.staged;",
  "    return jsonResponse({ id: s.runId, run_attempt: s.attempt, workflow_id: 81, path: s.workflow,",
  "      name: 'Staging', event: 'push', head_branch: 'main', head_sha: fixture.sha,",
  "      status: 'completed', conclusion: 'success',",
  "      repository: repoObject(), head_repository: repoObject(),",
  "      created_at: '2026-10-28T10:00:00Z', run_started_at: '2026-10-28T10:00:00Z',",
  "      updated_at: '2026-10-28T10:05:00Z',",
  "      html_url: 'https://github.com/' + repository + '/actions/runs/901' });",
  "  }",
  "  if (method === 'GET' && /\\/actions\\/runs\\/901(\\/attempts\\/2)?\\/jobs$/.test(path)) {",
  "    if (!fixture.staged) { return jsonResponse({ message: 'Not Found' }, 404); }",
  "    const s = fixture.staged;",
  "    const jobs = s.jobs.map(function (name, i) {",
  "      return { id: 500 + i, name, run_id: s.runId, run_attempt: s.attempt, head_sha: fixture.sha,",
  "        status: 'completed', conclusion: 'success',",
  "        started_at: '2026-10-28T10:01:00Z', completed_at: '2026-10-28T10:04:00Z',",
  "        steps: [{ name: 'verify', status: 'completed', conclusion: 'success', number: 1 }] };",
  "    });",
  "    return jsonResponse({ total_count: jobs.length, jobs });",
  "  }",
  "  if (method === 'GET' && path === root + '/actions/artifacts/77') {",
  "    if (!fixture.staged) { return jsonResponse({ message: 'Not Found' }, 404); }",
  "    const s = fixture.staged;",
  "    return jsonResponse({ id: s.artifactId, name: s.artifactName, digest: s.artifactDigest,",
  "      expired: false, size_in_bytes: 1024,",
  "      created_at: '2026-10-28T10:04:00Z', updated_at: '2026-10-28T10:04:00Z',",
  "      expires_at: '2099-01-01T00:00:00Z',",
  "      workflow_run: { id: s.runId, repository_id: 123, head_repository_id: 123,",
  "        head_branch: 'main', head_sha: fixture.sha } });",
  "  }",
  "  if (method === 'GET' && (path === root + '/actions/workflows/81' ||",
  "      path === root + '/actions/workflows/staging.yml')) {",
  "    return jsonResponse({ id: 81, path: '.github/workflows/staging.yml', state: 'active' });",
  "  }",
  "  if (method === 'GET' && path === root + '/tags') {",
  "    const pages = fixture.tagPages || [fixture.tags || []];",
  "    const pageNum = parseInt(parsed.searchParams.get('page') || '1', 10);",
  "    const page = pages[pageNum - 1] || [];",
  "    const headers = {};",
  "    if (pageNum < pages.length) {",
  "      const next = 'https://api.github.com' + root + '/tags?per_page=100&page=' + (pageNum + 1);",
  "      headers.Link = '<' + next + '>; rel=\"next\", <' + next + '>; rel=\"last\"';",
  "    }",
  "    let listed = page.map(function (entry) {",
  "      return { name: entry.name, commit: { sha: entry.sha } };",
  "    });",
  "    if (pageNum === pages.length) {",
  "      const state = loadState();",
  "      for (const name of state.tagsExtra) {",
  "        listed.push({ name, commit: { sha: state.peeled[name] } });",
  "      }",
  "    }",
  "    return jsonResponse(listed, 200, headers);",
  "  }",
  "  for (const prefix of [root + '/git/ref/tags/', root + '/git/refs/tags/']) {",
  "    if (method === 'GET' && path.indexOf(prefix) === 0) {",
  "      const name = path.slice(prefix.length);",
  "      const state = loadState();",
  "      const hit = state.refs[name]",
  "        ? { type: 'tag', sha: state.refs[name] }",
  "        : (fixture.refs || {})[name];",
  "      if (!hit) { return jsonResponse({ message: 'Not Found' }, 404); }",
  "      return jsonResponse({ ref: 'refs/tags/' + name, object: { type: hit.type, sha: hit.sha } });",
  "    }",
  "  }",
  "  if (method === 'GET' && path.indexOf(root + '/git/tags/') === 0) {",
  "    const sha = path.slice((root + '/git/tags/').length);",
  "    const state = loadState();",
  "    const hit = (state.tagObjects || {})[sha] || (fixture.tagObjects || {})[sha];",
  "    if (!hit) { return jsonResponse({ message: 'Not Found' }, 404); }",
  "    return jsonResponse(hit);",
  "  }",
  "  if (method === 'GET' && path === root + '/releases') {",
  "    return jsonResponse((fixture.releases || []).concat(loadState().releases || []));",
  "  }",
  "  if (method === 'GET' && path.indexOf(root + '/releases/tags/') === 0) {",
  "    const hit = (fixture.exactReleases || {})[path.slice((root + '/releases/tags/').length)];",
  "    if (!hit) { return jsonResponse({ message: 'Not Found' }, 404); }",
  "    return jsonResponse(hit);",
  "  }",
  "  if (method === 'GET' && path.indexOf(root + '/commits') === 0) {",
  "    return jsonResponse(fixture.commitsList || []);",
  "  }",
  "  if (method === 'POST' && path === root + '/git/tags') {",
  "    const state = loadState();",
  "    const count = Object.keys(state.tagObjects || {}).length +",
  "      Object.keys(fixture.tagObjects || {}).length;",
  "    const sha = (1000 + count).toString(16).padStart(40, '0');",
  "    const created = { sha, tag: body.tag, message: body.message,",
  "      object: { type: body.type, sha: body.object } };",
  "    state.tagObjects = state.tagObjects || {};",
  "    state.tagObjects[sha] = created;",
  "    state.peeled = state.peeled || {};",
  "    state.peeled[body.tag] = body.object;",
  "    saveState(state);",
  "    return jsonResponse(created, 201);",
  "  }",
  "  if (method === 'POST' && path === root + '/git/refs') {",
  "    const state = loadState();",
  "    const name = String(body.ref).replace('refs/tags/', '');",
  "    if (state.refs[name] || ((fixture.refs || {})[name])) {",
  "      return jsonResponse({ message: 'Reference already exists' }, 422);",
  "    }",
  "    state.refs[name] = body.sha;",
  "    state.tagsExtra = state.tagsExtra || [];",
  "    if (state.tagsExtra.indexOf(name) === -1) { state.tagsExtra.push(name); }",
  "    saveState(state);",
  "    return jsonResponse({ ref: body.ref, object: { type: 'tag', sha: body.sha } }, 201);",
  "  }",
  "  if (method === 'POST' && path === root + '/releases') {",
  "    const state = loadState();",
  "    state.releases = state.releases || [];",
  "    const created = Object.assign({}, body, { id: 2000 + state.releases.length,",
  "      html_url: 'https://github.com/' + repository + '/releases/tag/' + body.tag_name });",
  "    state.releases.push(created);",
  "    saveState(state);",
  "    return jsonResponse(created, 201);",
  "  }",
  "  return jsonResponse({ message: 'cli-test fake has no route for ' + method + ' ' + path }, 500);",
  "};",
  "",
].join("\n");

function baseFixture(overrides = {}) {
  return {
    frozenNow: FROZEN_NOW, sha: SHA, old: OLD, other: OTHER,
    staged: {
      runId: EVIDENCE.runId, attempt: EVIDENCE.attempt, workflow: EVIDENCE.workflow,
      jobs: [...EVIDENCE.jobs], artifactId: EVIDENCE.artifactId,
      artifactName: EVIDENCE.artifactName, artifactDigest: EVIDENCE.artifactDigest,
    },
    tags: [], refs: {}, tagObjects: {}, releases: [], exactReleases: {},
    commitsList: [], digestCompare: "ahead", faults: [],
    ...overrides,
  };
}

function stagedInput() {
  return {
    channel: "canary", sha: SHA, operation: "staging-run-901-attempt-2",
    policy: "staged-promotion", evidence: structuredClone(EVIDENCE),
  };
}

// Live-digest fixtures seed the daily-digest tag state; the fake fetch serves
// it through the production transport, which stays actual.
function digestFixture({ tagSha = null, digestCompare = "ahead" } = {}) {
  const tags = tagSha === null ? [] : [{ name: "v2026.1028.0", sha: tagSha }];
  const refs = tagSha === null
    ? {}
    : { "v2026.1028.0": { type: "commit", sha: tagSha } };
  return baseFixture({ staged: null, tags, refs, digestCompare });
}

function parseOutputs(text) {
  const map = new Map();
  for (const line of text.split("\n")) {
    if (line === "") continue;
    const eq = line.indexOf("=");
    assert.ok(eq > 0, "GITHUB_OUTPUT lines must be name=value, got: " + line);
    map.set(line.slice(0, eq), line.slice(eq + 1));
  }
  return map;
}

function runCli({
  request, repository, token, runnerJson, fixture,
  seedOutput = "", seedSummary = "", tz = "UTC", stateSeed = null,
}) {
  const dir = mkdtempSync(join(tmpdir(), "calver-cli-"));
  try {
    return runCliInDirectory(dir, { request, repository, token, runnerJson, fixture,
      seedOutput, seedSummary, tz, stateSeed });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function runCliInDirectory(dir, {
  request, repository, token, runnerJson, fixture, seedOutput, seedSummary, tz, stateSeed,
}) {
  const preloader = join(dir, "preload.mjs");
  const fixtureFile = join(dir, "fixture.json");
  const traceFile = join(dir, "trace.jsonl");
  const stateFile = join(dir, "state.json");
  const outputFile = join(dir, "output");
  const summaryFile = join(dir, "summary");
  writeFileSync(preloader, PRELOADER_SOURCE);
  writeFileSync(fixtureFile, JSON.stringify(fixture));
  writeFileSync(stateFile, JSON.stringify(stateSeed ?? {
    refs: {}, tagObjects: {}, peeled: {}, tagsExtra: [], releases: [],
  }));
  writeFileSync(outputFile, seedOutput);
  writeFileSync(summaryFile, seedSummary);
  writeFileSync(traceFile, "");
  // Isolated env: only the documented action inputs plus test scaffolding.
  // Host secrets are never inherited.
  const env = {
    CALVER_REQUEST: typeof request === "string" ? request : JSON.stringify(request),
    CALVER_RUNNER_JSON: runnerJson ?? '"ubuntu-latest"',
    CALVER_TOKEN: token ?? FAKE_TOKEN,
    GITHUB_REPOSITORY: repository,
    GITHUB_OUTPUT: outputFile,
    GITHUB_STEP_SUMMARY: summaryFile,
    CALVER_TEST_FIXTURE: fixtureFile,
    CALVER_TEST_TRACE: traceFile,
    CALVER_TEST_STATE: stateFile,
    TZ: tz,
  };
  const spawned = spawnSync(process.execPath, ["--import", preloader, CLI_PATH], {
    env, timeout: 10_000, encoding: "utf8",
  });
  assert.equal(spawned.error, undefined, "child must exit within 10s, got: " + spawned.error);
  assert.notEqual(spawned.status, null, "child must exit instead of timing out");
  const outputText = readFileSync(outputFile, "utf8");
  const summaryText = readFileSync(summaryFile, "utf8");
  const traceText = readFileSync(traceFile, "utf8");
  const trace = traceText.split("\n").filter((line) => line !== "").map((line) => JSON.parse(line));
  return {
    status: spawned.status,
    stdout: spawned.stdout ?? "",
    stderr: spawned.stderr ?? "",
    outputText,
    summaryText,
    traceText,
    trace,
    outputs: parseOutputs(outputText),
    state: JSON.parse(readFileSync(stateFile, "utf8")),
  };
}

function assertAllGet(run, label) {
  assert.ok(run.trace.length > 0, label + " must fetch before deciding");
  for (const call of run.trace) {
    assert.equal(call.method, "GET", label + " must be read-only, got: " + call.method + " " + call.url);
  }
}

function assertApiHostOnly(run, label) {
  for (const call of run.trace) {
    assert.ok(
      call.url.startsWith("https://api.github.com/"),
      label + " must use only the GitHub API, got: " + call.url,
    );
  }
}

function assertNoSecrets(run, secrets, label) {
  const blobs = {
    stdout: run.stdout, stderr: run.stderr,
    output: run.outputText, summary: run.summaryText, trace: run.traceText,
  };
  for (const [name, blob] of Object.entries(blobs)) {
    for (const secret of secrets) {
      assert.ok(!blob.includes(secret), label + " leaks a token into " + name);
    }
  }
}

function assertUntouchedFiles(run, seedOutput, seedSummary, label) {
  assert.equal(run.stdout, "", label + " refusal must not print outputs");
  assert.equal(run.outputText, seedOutput, label + " refusal must not append outputs");
  assert.equal(run.summaryText, seedSummary, label + " refusal must not append a summary");
}

test("canary plan is deterministic, portable and exposes only documented outputs", () => {
  const request = { phase: "plan", input: stagedInput() };
  const first = runCli({ request, repository: STAGED_REPO, fixture: baseFixture() });
  assert.equal(first.status, 0, "plan must succeed, stderr: " + first.stderr);
  assertApiHostOnly(first, "plan");
  assertAllGet(first, "plan");
  const outputs = first.outputs;
  const plan = JSON.parse(outputs.get("plan"));
  assert.equal(plan.version, "2026.1028.0-canary.0");
  assert.equal(plan.tag, "canary/v2026.1028.0-canary.0");
  assert.equal(plan.sha, SHA);
  assert.equal(plan.date, FROZEN_DATE);
  for (const [name, value] of [
    ["version", plan.version], ["tag", plan.tag], ["sha", plan.sha],
    ["date", plan.date], ["release-date", plan.date],
    ["previous-stable-tag", plan.previousStableTag ?? ""],
    ["created", "false"], ["release-url", ""],
    ["provenance", plan.id], ["provenance-id", plan.id],
  ]) {
    assert.equal(outputs.get(name), value, "output " + name + " must match the portable plan");
  }
  assert.deepEqual(JSON.parse(JSON.stringify(plan)), plan, "plan must survive a JSON round-trip");
  for (const name of outputs.keys()) {
    assert.doesNotMatch(
      name, /notes[-_]?(?:path|file)|(?:path|file)[-_]notes/i,
      "runner-local notes paths are never action outputs",
    );
  }
  assert.match(first.summaryText, /## CalVer plan/);
  assert.ok(first.summaryText.includes(plan.version));
  const second = runCli({ request, repository: STAGED_REPO, fixture: baseFixture() });
  assert.equal(second.status, 0, "plan must succeed again, stderr: " + second.stderr);
  assert.equal(second.stdout, first.stdout, "the same frozen inputs must yield byte-identical outputs");
  assertNoSecrets(first, [FAKE_TOKEN], "plan");
});

test("plan appends to existing output/summary files instead of clobbering them", () => {
  const seedOutput = "existing-output=keepme\n";
  const seedSummary = "# existing summary\n";
  const run = runCli({
    request: { phase: "plan", input: stagedInput() },
    repository: STAGED_REPO, fixture: baseFixture(), seedOutput, seedSummary,
  });
  assert.equal(run.status, 0, "plan must succeed, stderr: " + run.stderr);
  assert.ok(run.outputText.startsWith(seedOutput), "existing GITHUB_OUTPUT prefix must be preserved");
  assert.ok(run.outputText.includes("\nplan={"), "fresh plan outputs must be appended");
  assert.ok(run.summaryText.startsWith(seedSummary), "existing summary prefix must be preserved");
  assert.match(run.summaryText, /## CalVer plan/);
});

test("paginated tag history is fully consumed for numeric allocation", () => {
  const tagPages = [
    [{ name: "v2026.1028.2", sha: OLD }],
    [{ name: "v2026.1028.10", sha: OTHER }],
  ];
  const refs = {
    "v2026.1028.2": { type: "commit", sha: OLD },
    "v2026.1028.10": { type: "commit", sha: OTHER },
  };
  const run = runCli({
    request: { phase: "plan", input: stagedInput() },
    repository: STAGED_REPO, fixture: baseFixture({ tags: [], tagPages, refs }),
  });
  assert.equal(run.status, 0, "paginated plan must succeed, stderr: " + run.stderr);
  assert.equal(run.outputs.get("version"), "2026.1028.11-canary.0");
  assert.equal(run.outputs.get("tag"), "canary/v2026.1028.11-canary.0");
  assert.ok(run.trace.some((call) => call.url.includes("page=2")), "both ref pages must be consumed");
  assertAllGet(run, "paginated plan");
});

test("publish dryRun revalidates the saved plan with zero non-GET fetches", () => {
  const planned = runCli({
    request: { phase: "plan", input: stagedInput() },
    repository: STAGED_REPO, fixture: baseFixture(),
  });
  assert.equal(planned.status, 0, "plan must succeed, stderr: " + planned.stderr);
  const plan = JSON.parse(planned.outputs.get("plan"));
  const published = runCli({
    request: { phase: "publish", plan, dryRun: true },
    repository: STAGED_REPO, fixture: baseFixture(),
  });
  assert.equal(published.status, 0, "dryRun must succeed, stderr: " + published.stderr);
  assert.equal(published.outputs.get("created"), "false");
  assert.equal(published.outputs.get("tag"), plan.tag);
  assert.deepEqual(JSON.parse(published.outputs.get("plan")), plan, "dryRun must echo the unchanged plan");
  assert.ok(published.trace.some((call) => /\/git\/refs?\/tags\//.test(call.url)),
    "dryRun must still re-read the exact ref for a collision");
  assertAllGet(published, "dryRun publish");
});

test("real publication creates only the tag and replays without new writes", () => {
  const planned = runCli({
    request: { phase: "plan", input: stagedInput() },
    repository: STAGED_REPO, fixture: baseFixture(),
  });
  assert.equal(planned.status, 0, "plan must succeed, stderr: " + planned.stderr);
  const plan = JSON.parse(planned.outputs.get("plan"));
  const first = runCli({
    request: { phase: "publish", plan },
    repository: STAGED_REPO, fixture: baseFixture(),
  });
  assert.equal(first.status, 0, "publication must succeed, stderr: " + first.stderr);
  assert.equal(first.outputs.get("created"), "true");
  assert.equal(first.outputs.get("tag"), plan.tag);
  assert.match(first.summaryText, /## CalVer publish/);
  const writes = first.trace.filter((call) => call.method !== "GET");
  assert.deepEqual(
    writes.map((call) => call.method + " " + call.url.split("?")[0].replace("https://api.github.com", "")),
    [
      "POST /repos/" + STAGED_REPO + "/git/tags",
      "POST /repos/" + STAGED_REPO + "/git/refs",
    ],
    "canary publication must create an annotated tag object and ref, nothing else",
  );
  assert.ok(!first.trace.some((call) => call.url.includes("/releases")),
    "canary publication must never create a GitHub Release");
  assertApiHostOnly(first, "publication");
  const replay = runCli({
    request: { phase: "publish", plan },
    repository: STAGED_REPO, fixture: baseFixture(), stateSeed: first.state,
  });
  assert.equal(replay.status, 0, "exact replay must succeed, stderr: " + replay.stderr);
  assert.equal(replay.outputs.get("created"), "false");
  assert.equal(replay.outputs.get("tag"), plan.tag);
  assertAllGet(replay, "idempotent replay");
});

test("quiet-day skip stays green and carries no plan", () => {
  // The daily digest tag already resolves to the planned SHA: production is
  // quiet, so there is nothing new to digest.
  const run = runCli({
    request: { phase: "plan", input: { channel: "stable", sha: SHA, operation: "digest-op-1", policy: "live-digest" } },
    repository: DIGEST_REPO, fixture: digestFixture({ tagSha: SHA }),
  });
  assert.equal(run.status, 0, "a classified skip must stay green, stderr: " + run.stderr);
  assert.equal(run.outputs.get("created"), "false");
  assert.equal(run.outputs.get("skipped"), "true");
  assert.equal(run.outputs.get("reason"), "quiet-day");
  assert.ok(!run.outputs.has("plan"), "a skip must not return a plan");
  assert.ok(!run.stdout.includes("plan="), "a skip must not print a plan");
  assert.doesNotMatch(run.summaryText, /## CalVer plan/);
  assertAllGet(run, "quiet-day skip");
  assertNoSecrets(run, [FAKE_TOKEN], "quiet-day skip");
});

test("rollback skip stays green and carries no plan", () => {
  // The existing digest tag is unreachable from the planned SHA: production
  // moved backwards or diverged, so no digest is issued.
  const run = runCli({
    request: { phase: "plan", input: { channel: "stable", sha: SHA, operation: "digest-op-1", policy: "live-digest" } },
    repository: DIGEST_REPO, fixture: digestFixture({ tagSha: OTHER, digestCompare: "diverged" }),
  });
  assert.equal(run.status, 0, "a classified skip must stay green, stderr: " + run.stderr);
  assert.equal(run.outputs.get("created"), "false");
  assert.equal(run.outputs.get("skipped"), "true");
  assert.equal(run.outputs.get("reason"), "rollback");
  assert.ok(!run.outputs.has("plan"), "a skip must not return a plan");
  assertAllGet(run, "rollback skip");
});

test("digest-already-published skip stays green and carries no plan", () => {
  // Today's digest tag already exists on another SHA that still reaches the
  // planned SHA: the daily cadence was already issued.
  const run = runCli({
    request: { phase: "plan", input: { channel: "stable", sha: SHA, operation: "digest-op-1", policy: "live-digest" } },
    repository: DIGEST_REPO, fixture: digestFixture({ tagSha: OTHER, digestCompare: "ahead" }),
  });
  assert.equal(run.status, 0, "a classified skip must stay green, stderr: " + run.stderr);
  assert.equal(run.outputs.get("created"), "false");
  assert.equal(run.outputs.get("skipped"), "true");
  assert.equal(run.outputs.get("reason"), "digest-already-published");
  assert.ok(!run.outputs.has("plan"), "a skip must not return a plan");
  assertAllGet(run, "already-published skip");
});

test("refusals exit nonzero, write nothing and leak no token", () => {
  const seedOutput = "existing-output=keepme\n";
  const seedSummary = "# existing summary\n";
  const cases = {
    "non-JSON request": "not-json-at-all",
    "invalid sha": { phase: "plan", input: { ...stagedInput(), sha: "not-a-sha" } },
    "tampered publish plan": { phase: "publish", plan: { schema: "calver-plan-v1", tag: "canary/v2026.1028.0-canary.0" } },
  };
  for (const [name, request] of Object.entries(cases)) {
    const run = runCli({
      request, repository: STAGED_REPO, fixture: baseFixture(), seedOutput, seedSummary,
    });
    assert.notEqual(run.status, 0, name + " must refuse with a nonzero exit");
    assert.match(run.stderr, /CalVer refused/, name + " must classify the refusal on stderr");
    assertUntouchedFiles(run, seedOutput, seedSummary, name);
    assertNoSecrets(run, [FAKE_TOKEN], name);
  }
});

test("a failed tag-list read fails closed without any mutation", () => {
  const run = runCli({
    request: { phase: "plan", input: stagedInput() },
    repository: STAGED_REPO,
    fixture: baseFixture({ faults: [{ method: "GET", contains: "/tags?per_page=100", status: 500 }] }),
  });
  assert.notEqual(run.status, 0, "an unreadable tag history is never empty history");
  assert.match(run.stderr, /CalVer refused/);
  assertAllGet(run, "failed read");
});

test("child env is isolated from host secrets", () => {
  const hostSecret = "host-secret-sentinel-8k2m";
  const hostToken = "host-token-must-not-leak-4q7w";
  const saved = {
    HOST_CLI_PROBE_SECRET: process.env.HOST_CLI_PROBE_SECRET,
    CALVER_TOKEN: process.env.CALVER_TOKEN,
    GITHUB_TOKEN: process.env.GITHUB_TOKEN,
  };
  process.env.HOST_CLI_PROBE_SECRET = hostSecret;
  process.env.CALVER_TOKEN = hostToken;
  process.env.GITHUB_TOKEN = hostToken;
  try {
    const run = runCli({
      request: { phase: "plan", input: stagedInput() },
      repository: STAGED_REPO, fixture: baseFixture(),
    });
    assert.equal(run.status, 0, "explicit child env must suffice, stderr: " + run.stderr);
    assert.equal(run.outputs.get("version"), "2026.1028.0-canary.0");
    assertNoSecrets(run, [hostSecret, hostToken, FAKE_TOKEN], "isolated child");
  } finally {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("frozen UTC date is independent of the runner timezone", () => {
  const request = { phase: "plan", input: stagedInput() };
  const fixture = baseFixture();
  const runs = ["UTC", "Pacific/Kiritimati", "Pacific/Midway"].map((tz) =>
    runCli({ request, repository: STAGED_REPO, fixture: baseFixture(), tz }),
  );
  assert.deepEqual(fixture, baseFixture(), "fixtures must stay reusable across runs");
  for (const run of runs) {
    assert.equal(run.status, 0, "plan must succeed, stderr: " + run.stderr);
    assert.equal(run.outputs.get("version"), "2026.1028.0-canary.0");
    assert.equal(run.outputs.get("date"), FROZEN_DATE);
  }
  assert.equal(runs[1].stdout, runs[0].stdout, "UTC+14 wall date must not change the frozen plan");
  assert.equal(runs[2].stdout, runs[0].stdout, "UTC-11 wall date must not change the frozen plan");
});

for (const status of [403, 404, 422]) {
  test(`CLI HTTP ${status} refusal keeps response bodies and token out of every output`, () => {
    const privateBody = "offline-private-response-marker";
    const seedOutput = "existing-output=keepme\n";
    const seedSummary = "# existing summary\n";
    const run = runCli({
      request: { phase: "plan", input: stagedInput() },
      repository: STAGED_REPO, seedOutput, seedSummary,
      fixture: baseFixture({ faults: [{ contains: "/tags?per_page=100", status,
        message: `${privateBody} ${FAKE_TOKEN}` }] }),
    });
    assert.notEqual(run.status, 0, "HTTP failure must not become empty history");
    assertUntouchedFiles(run, seedOutput, seedSummary, "HTTP refusal");
    assertAllGet(run, "HTTP refusal");
    assertNoSecrets(run, [FAKE_TOKEN, privateBody], "HTTP refusal");
  });
}

test("stable CLI publish dryRun cannot create a tag or Release", () => {
  const repository = "TogetherWeOwn/kagi-shinobi";
  const planned = runCli({
    request: { phase: "plan", input: { channel: "stable", sha: SHA,
      operation: "offline-direct-release", policy: "direct-release" } },
    repository, fixture: baseFixture(),
  });
  assert.equal(planned.status, 0, planned.stderr);
  const plan = JSON.parse(planned.outputs.get("plan"));
  const run = runCli({ request: { phase: "publish", plan, dryRun: true },
    repository, fixture: baseFixture() });
  assert.equal(run.status, 0, run.stderr);
  assert.equal(run.outputs.get("created"), "false");
  assert.equal(run.outputs.get("tag"), plan.tag);
  assert.deepEqual(JSON.parse(run.outputs.get("plan")), plan);
  assertAllGet(run, "stable dryRun");
  assert.deepEqual(run.state, { refs: {}, tagObjects: {}, peeled: {}, tagsExtra: [], releases: [] });
  assertNoSecrets(run, [FAKE_TOKEN], "stable dryRun");
});
