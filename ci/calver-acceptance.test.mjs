// Independent, offline public-interface acceptance; no production API or implementation inspection.
// The fake speaks ordinary GitHub REST JSON. Unknown reads fail; all writes stay in memory.
import assert from "node:assert/strict";
import { test } from "node:test";

const engine = () => import("../actions/calver/calver.mjs");
const SHA = "a".repeat(40);
const OTHER = "b".repeat(40);
const OLD = "c".repeat(40);
const REPOSITORY = "TogetherWeOwn/two-web-next";
const DATE = "2026-10-28";
const EVIDENCE = {
  runId: 901, attempt: 2, workflow: ".github/workflows/staging.yml",
  jobs: ["staging", "acceptance"], artifactId: 77, artifactName: "staging-certificate",
  artifactDigest: `sha256:${"d".repeat(64)}`,
};
const copy = (value) => structuredClone(value);
const httpError = (status, message = `fixture HTTP ${status}`) => Object.assign(new Error(message), { status });

class FakeGitHub {
  constructor(repository = REPOSITORY) {
    this.repository = repository;
    this.calls = [];
    this.pageVisits = [];
    this.refPages = [[], []];
    this.refs = new Map();
    this.objects = new Map();
    this.releases = new Map();
    this.nextObject = 1000;
    this.nextRelease = 2000;
    this.fault = null;
    this.onRefCreate = null;
    this.compareStatus = "ahead";
    this.unreachable = new Set();
    this.commitMessages = [{ sha: SHA, message: "feat: acceptance change" }];
    this.compareMessages = new Map();
    const [owner, name] = repository.split("/");
    this.repo = { id: 123, name, full_name: repository, owner: { login: owner }, private: false,
      visibility: "public", default_branch: "main" };
    this.run = {
      id: EVIDENCE.runId, run_attempt: EVIDENCE.attempt, workflow_id: 81,
      path: EVIDENCE.workflow, name: "Staging", event: "push", head_branch: "main", head_sha: SHA,
      status: "completed", conclusion: "success", repository: copy(this.repo), head_repository: copy(this.repo),
      created_at: "2026-10-28T10:00:00Z", run_started_at: "2026-10-28T10:00:00Z",
      updated_at: "2026-10-28T10:05:00Z", html_url: `https://github.com/${repository}/actions/runs/901`,
    };
    this.jobs = EVIDENCE.jobs.map((name, i) => ({
      id: 500 + i, name, run_id: EVIDENCE.runId, run_attempt: EVIDENCE.attempt,
      head_sha: SHA, status: "completed", conclusion: "success",
      started_at: "2026-10-28T10:01:00Z", completed_at: "2026-10-28T10:04:00Z",
      steps: [{ name: "verify", status: "completed", conclusion: "success", number: 1 }],
    }));
    this.artifact = {
      id: EVIDENCE.artifactId, name: EVIDENCE.artifactName, digest: EVIDENCE.artifactDigest,
      expired: false, size_in_bytes: 1024, created_at: "2026-10-28T10:04:00Z",
      updated_at: "2026-10-28T10:04:00Z", expires_at: "2099-01-01T00:00:00Z",
      workflow_run: { id: EVIDENCE.runId, repository_id: this.repo.id,
        head_repository_id: this.repo.id, head_branch: "main", head_sha: SHA },
    };
  }

  addRef(name, sha = SHA, type = "commit", page = 0) {
    const ref = { ref: `refs/tags/${name}`, node_id: `ref-${name}`, object: { type, sha } };
    this.refs.set(name, ref);
    this.refPages[page] ??= [];
    this.refPages[page].push(ref);
    return ref;
  }

  replaceRef(name, sha, type = "commit") {
    for (const page of this.refPages) {
      for (let i = page.length - 1; i >= 0; i--) {
        if (page[i].ref === `refs/tags/${name}`) page.splice(i, 1);
      }
    }
    this.refs.delete(name);
    return this.addRef(name, sha, type);
  }

  path(value) {
    const url = new URL(value, "https://api.github.com");
    assert.equal(url.origin, "https://api.github.com", "engine must not follow an arbitrary API origin");
    return decodeURIComponent(url.pathname).replace(/\/$/, "");
  }

  check(method, path, body) {
    this.calls.push({ method, path, ...(body === undefined ? {} : { body: copy(body) }) });
    const error = this.fault?.(method, path, body);
    if (error) throw error;
  }

  mutations() { return this.calls.filter(({ method }) => method !== "GET"); }

  resolve(ref) { return this.refs.get(ref)?.object.sha ?? ref; }

  listedCommit(ref) {
    // GET /tags reports the commit, unlike GET /git/refs which reports the tag object.
    let object = ref.object;
    const visited = new Set();
    while (object.type === "tag" && this.objects.has(object.sha) && !visited.has(object.sha)) {
      visited.add(object.sha);
      object = this.objects.get(object.sha).object;
    }
    return object.sha;
  }

  history(path) {
    const [base] = this.path(path).split("/compare/")[1].split("...");
    return this.compareMessages.get(this.resolve(base)) ?? this.commitMessages;
  }

  commit(sha, message) {
    return { sha, parents: sha === OLD ? [] : [{ sha: OLD }], html_url: `https://github.com/${this.repository}/commit/${sha}`,
      commit: { message: message ?? this.commitMessages.find((c) => c.sha === sha)?.message ?? "fix: previous boundary",
        author: { name: "Fixture", date: "2026-10-28T10:00:00Z" },
        committer: { name: "Fixture", date: "2026-10-28T10:00:00Z" } } };
  }

  async paginate(path, key) {
    this.check("GET", path);
    const p = this.path(path);
    const root = `/repos/${this.repository}`;
    if (new RegExp(`^${root}/git/(?:matching-refs|refs)/tags$`).test(p)) {
      // paginate is the public seam responsible for following Link pages; both must be consumed.
      this.pageVisits.push(...this.refPages.map((_, index) => index + 1));
      return copy(this.refPages.flat());
    }
    if (p === `${root}/tags`) {
      this.pageVisits.push(...this.refPages.map((_, index) => index + 1));
      return this.refPages.flat().map((ref) => ({
        name: ref.ref.slice("refs/tags/".length), commit: { sha: this.listedCommit(ref) },
      }));
    }
    if (new RegExp(`/actions/runs/${this.run.id}(?:/attempts/${this.run.run_attempt})?/jobs$`).test(p)) {
      assert.ok(key === undefined || key === "jobs");
      return copy(this.jobs);
    }
    if (/\/actions\/runs\/901\/artifacts$/.test(p)) {
      assert.ok(key === undefined || key === "artifacts");
      return [copy(this.artifact)];
    }
    if (p === `${root}/releases`) return copy([...this.releases.values()]);
    if (p === `${root}/commits`) return this.commitMessages.map(({ sha }) => this.commit(sha));
    if (p.includes("/compare/")) {
      assert.ok(key === undefined || key === "commits");
      return this.history(path).map(({ sha, message }) => this.commit(sha, message));
    }
    throw httpError(500, `Unmodeled paginate: ${path} (${key ?? "array"})`);
  }

  async request(method, path, body) {
    this.check(method, path, body);
    const p = this.path(path);
    const root = `/repos/${this.repository}`;
    if (method === "GET") {
      if (p === root) return copy(this.repo);
      if (p === `${root}/branches/main`) return { name: "main", commit: this.commit(SHA), protected: true };
      if (p === `${root}/git/ref/heads/main` || p === `${root}/git/refs/heads/main`) {
        return { ref: "refs/heads/main", object: { type: "commit", sha: SHA } };
      }
      if (p.startsWith(`${root}/commits/`)) return this.commit(p.slice(`${root}/commits/`.length));
      if (p.startsWith(`${root}/git/commits/`)) return this.commit(p.slice(`${root}/git/commits/`.length));
      if (p.startsWith(`${root}/compare/`)) {
        const pair = p.slice(`${root}/compare/`.length).split("...");
        const unreachable = pair.some((item) => this.unreachable.has(this.resolve(item)));
        const commits = this.history(path).map(({ sha, message }) => this.commit(sha, message));
        return { status: unreachable ? "diverged" : this.compareStatus, ahead_by: 1, behind_by: unreachable ? 1 : 0,
          total_commits: commits.length, base_commit: this.commit(this.resolve(pair[0])),
          merge_base_commit: this.commit(OLD), commits };
      }
      if (p === `${root}/actions/runs/${this.run.id}` ||
          p === `${root}/actions/runs/${this.run.id}/attempts/${this.run.run_attempt}`) return copy(this.run);
      if (new RegExp(`/actions/runs/${this.run.id}(?:/attempts/${this.run.run_attempt})?/jobs$`).test(p)) {
        return { total_count: this.jobs.length, jobs: copy(this.jobs.slice(0, 1)) };
      }
      if (p === `${root}/actions/artifacts/${this.artifact?.id}`) return copy(this.artifact);
      if (p === `${root}/actions/runs/901/artifacts`) return { total_count: 1, artifacts: [copy(this.artifact)] };
      if (p === `${root}/actions/workflows/81` || p === `${root}/actions/workflows/staging.yml`) {
        return { id: 81, path: EVIDENCE.workflow, state: "active" };
      }
      const ref = p.match(new RegExp(`^${root}/git/refs?/tags/(.+)$`));
      if (ref) {
        if (!this.refs.has(ref[1])) throw httpError(404);
        return copy(this.refs.get(ref[1]));
      }
      const object = p.match(new RegExp(`^${root}/git/tags/([a-f0-9]{40})$`));
      if (object) {
        if (!this.objects.has(object[1])) throw httpError(404);
        return copy(this.objects.get(object[1]));
      }
      const release = p.match(new RegExp(`^${root}/releases/tags/(.+)$`));
      if (release) {
        if (!this.releases.has(release[1])) throw httpError(404);
        return copy(this.releases.get(release[1]));
      }
    }
    if (method === "POST" && p === `${root}/git/tags`) {
      assert.equal(body.type, "commit", "publication must annotate a verified commit, not a tree/blob");
      assert.match(body.object, /^[a-f0-9]{40}$/);
      assert.equal(typeof body.message, "string");
      const sha = (this.nextObject++).toString(16).padStart(40, "0");
      const tag = { sha, tag: body.tag, message: body.message,
        tagger: body.tagger, object: { type: body.type, sha: body.object } };
      this.objects.set(sha, tag);
      return copy(tag);
    }
    if (method === "POST" && p === `${root}/git/refs`) {
      assert.match(body.ref, /^refs\/tags\//, "only tag ref creation is permitted");
      const name = body.ref.slice("refs/tags/".length);
      if (this.onRefCreate) await this.onRefCreate(name, body, this);
      if (this.refs.has(name)) throw httpError(422, "Reference already exists");
      return copy(this.addRef(name, body.sha, "tag"));
    }
    if (method === "POST" && p === `${root}/releases`) {
      const ref = this.refs.get(body.tag_name);
      assert.ok(ref, "Release must target an existing verified tag");
      assert.ok(this.objects.has(ref.object.sha), "Release tag must have provenance annotation");
      if (this.releases.has(body.tag_name)) throw httpError(422, "Release already exists");
      const release = { ...copy(body), id: this.nextRelease++,
        html_url: `https://github.com/${this.repository}/releases/tag/${body.tag_name}` };
      this.releases.set(body.tag_name, release);
      return copy(release);
    }
    throw httpError(500, `Unmodeled request: ${method} ${path}`);
  }
}

async function setup(repository = REPOSITORY) {
  const { Releaser } = await engine();
  const api = new FakeGitHub(repository);
  let clock = "2026-10-28T23:59:58Z";
  return { api, releaser: new Releaser({ api, repository, now: () => new Date(clock) }),
    setClock(value) { clock = value; } };
}

const input = (extra = {}) => ({
  channel: "canary", sha: SHA, operation: "staging-run-901-attempt-2",
  policy: "staged-promotion", evidence: copy(EVIDENCE), ...extra,
});

const source = () => {
  const { runId, attempt, workflow, jobs } = copy(EVIDENCE);
  return { runId, attempt, workflow, jobs };
};
const deferredInput = (extra = {}) => input({ evidence: undefined, source: source(), ...extra });

async function deferredSetup() {
  const fixture = await setup();
  fixture.api.run.status = "in_progress";
  fixture.api.run.conclusion = null;
  fixture.api.jobs.forEach((job) => {
    job.status = "in_progress";
    job.conclusion = null;
    job.completed_at = null;
  });
  return fixture;
}

function completeRequiredJobs(api) {
  api.jobs.forEach((job) => {
    job.status = "completed";
    job.conclusion = "success";
    job.completed_at = "2026-10-28T10:04:00Z";
  });
}

async function stableSetup() {
  const fixture = await setup();
  const canary = await fixture.releaser.plan(input());
  await fixture.releaser.publish(canary);
  const stableInput = input({ channel: "stable", sourceCanary: canary.tag });
  const plan = await fixture.releaser.plan(stableInput);
  return { ...fixture, canary, stableInput, plan };
}

function assertNoMutations(api, start = 0) {
  assert.deepEqual(api.calls.slice(start).filter(({ method }) => method !== "GET"), [],
    "read-only planning/dry run must never POST, PATCH, PUT or DELETE");
}

function replaceBoundField(plan, key, value) {
  let found = 0;
  const visit = (object) => {
    if (!object || typeof object !== "object") return;
    for (const name of Object.keys(object)) {
      if (name === key) { object[name] = value; found++; }
      else visit(object[name]);
    }
  };
  visit(plan);
  assert.ok(found > 0, `portable plan must retain its bound ${key} input`);
}

function releaseBody(api) {
  const call = api.calls.findLast(({ method, path }) => method === "POST" && /\/releases$/.test(path));
  assert.ok(call, "stable publication must POST a GitHub Release");
  return call.body;
}

test("offline fake follows both ref pages and rejects unknown endpoints", async () => {
  const api = new FakeGitHub();
  api.addRef("v2026.1028.0", OLD, "commit", 0);
  api.addRef("v2026.1028.10", SHA, "commit", 1);
  assert.equal((await api.paginate(`/repos/${REPOSITORY}/git/matching-refs/tags/`)).length, 2);
  assert.deepEqual(api.pageVisits, [1, 2]);
  await assert.rejects(api.request("GET", `/repos/${REPOSITORY}/not-a-github-route`), { status: 500 });
  assertNoMutations(api);
});

test("fake /tags lists peeled commits while /git/ref retains the annotation object", async () => {
  const api = new FakeGitHub();
  const object = "1".repeat(40);
  api.addRef("v2026.1028.0", object, "tag");
  api.objects.set(object, { sha: object, object: { type: "tag", sha: "2".repeat(40) } });
  api.objects.set("2".repeat(40), { sha: "2".repeat(40), object: { type: "commit", sha: SHA } });
  const tags = await api.paginate(`/repos/${REPOSITORY}/tags`);
  assert.equal(tags[0].commit.sha, SHA);
  const ref = await api.request("GET", `/repos/${REPOSITORY}/git/ref/tags/v2026.1028.0`);
  assert.equal(ref.object.type, "tag");
  assert.equal(ref.object.sha, object);
});

test("tags consumes every page and recursively peels annotated tags", async () => {
  const { api, releaser } = await setup();
  api.addRef("v2026.1027.0", OLD, "commit", 0);
  api.addRef("v2026.1028.10", "1".repeat(40), "tag", 1);
  api.objects.set("1".repeat(40), { sha: "1".repeat(40), object: { type: "tag", sha: "2".repeat(40) } });
  api.objects.set("2".repeat(40), { sha: "2".repeat(40), object: { type: "commit", sha: SHA } });
  const tags = await releaser.tags();
  for (const [name, sha] of [["v2026.1027.0", OLD], ["v2026.1028.10", SHA]]) {
    assert.ok(tags.some((tag) => tag.name === name && tag.sha === sha), `missing peeled ${name}`);
  }
  assert.ok(api.pageVisits.includes(2), "must use the complete paginated refs view");
  assertNoMutations(api);
});

for (const target of ["tree", "blob", "cycle", "deep"]) {
  test(`tags rejects ${target} annotated targets`, async () => {
    const { api, releaser } = await setup();
    const first = "1".repeat(40);
    api.addRef("v2026.1028.0", first, "tag");
    if (target === "cycle") {
      api.objects.set(first, { sha: first, object: { type: "tag", sha: first } });
    } else if (target === "deep") {
      for (let i = 1; i <= 1024; i++) {
        const current = i.toString(16).padStart(40, "0");
        api.objects.set(current, { sha: current, object: { type: "tag", sha: (i + 1).toString(16).padStart(40, "0") } });
      }
      api.replaceRef("v2026.1028.0", "1".padStart(40, "0"), "tag");
    } else {
      api.objects.set(first, { sha: first, object: { type: target, sha: SHA } });
    }
    await assert.rejects(releaser.tags());
    assertNoMutations(api);
  });
}

test("tags rejects ambiguous duplicate names across pages", async () => {
  const { api, releaser } = await setup();
  api.addRef("v2026.1028.0", SHA, "commit", 0);
  api.addRef("v2026.1028.0", OTHER, "commit", 1);
  await assert.rejects(releaser.tags());
});

for (const status of [403, 404, 429, 500]) {
  test(`a failed tag-list HTTP ${status} is not empty history`, async () => {
    const { api, releaser } = await setup();
    api.fault = (_, path) => /\/(?:tags|matching-refs\/tags)\/?(?:\?|$)/.test(path) ? httpError(status) : null;
    await assert.rejects(releaser.plan(input()));
    assertNoMutations(api);
  });
}

for (const status of [403, 404, 429, 503]) {
  test(`annotated-object HTTP ${status} is not an unrelated lightweight tag`, async () => {
    const { api, releaser } = await setup();
    api.addRef("v2026.1028.0", "1".repeat(40), "tag");
    api.fault = (_, path) => path.includes("/git/tags/") ? httpError(status) : null;
    await assert.rejects(releaser.tags());
    assertNoMutations(api);
  });
}

test("planning is read-only, deterministic, JSON portable and consumes the second ref page", async () => {
  const { api, releaser } = await setup();
  api.addRef("v2026.1028.2", OLD, "commit", 0);
  api.addRef("v2026.1028.10", OTHER, "commit", 1);
  const first = await releaser.plan(input());
  const second = await releaser.plan(input());
  assert.equal(first.version, "2026.1028.11-canary.0");
  assert.equal(first.tag, "canary/v2026.1028.11-canary.0");
  assert.equal(first.date, DATE);
  assert.equal(first.sha, SHA);
  assert.deepEqual(first, JSON.parse(JSON.stringify(first)));
  assert.deepEqual(first, second, "the same frozen inputs must yield the same deterministic identity");
  assert.ok(api.pageVisits.includes(2));
  assert.ok(api.calls.some(({ path }) => path.includes("/compare/")), "SHA reachability cannot be inferred from tag names");
  assert.ok(api.calls.some(({ path }) => /\/(?:git\/)?commits\//.test(path)), "SHA must independently resolve to a commit");
  assertNoMutations(api);
});

for (const extra of [
  { sha: "A".repeat(40) }, { sha: "a".repeat(39) }, { sha: "a".repeat(41) }, { sha: "main" },
  { operation: "" }, { operation: "x".repeat(1024 * 1024) },
  { channel: "preview" }, { policy: "unrestricted" }, { namespace: "custom" },
  { latest: "yes" }, { repairDate: "2026-02-29" },
]) {
  test(`plan refuses invalid ${Object.keys(extra).join(",")}: ${String(Object.values(extra)[0]).slice(0, 55)}`, async () => {
    const { api, releaser } = await setup();
    await assert.rejects(releaser.plan(input(extra)));
    assertNoMutations(api);
  });
}

for (const status of ["behind", "diverged"]) {
  test(`planning refuses commit compare status ${status}`, async () => {
    const { api, releaser } = await setup();
    api.compareStatus = status;
    await assert.rejects(releaser.plan(input()));
    assertNoMutations(api);
  });
}

test("an identical main commit is eligible", async () => {
  const { api, releaser } = await setup();
  api.compareStatus = "identical";
  assert.equal((await releaser.plan(input())).sha, SHA);
});

for (const status of [404, 403, 500]) {
  test(`commit resolution HTTP ${status} cannot be replaced by compare/tag evidence`, async () => {
    const { api, releaser } = await setup();
    api.fault = (_, path) => /\/(?:git\/)?commits\//.test(path) ? httpError(status) : null;
    await assert.rejects(releaser.plan(input()));
    assertNoMutations(api);
  });
}

const unsafeEvidence = [
  ["foreign repository", (a) => { a.run.repository.full_name = "untrusted/fork"; a.run.repository.id = 999; }],
  ["foreign head repository", (a) => { a.run.head_repository.full_name = "untrusted/fork"; a.run.head_repository.id = 999; }],
  ["different SHA", (a) => { a.run.head_sha = OTHER; }],
  ["manual dispatch", (a) => { a.run.event = "workflow_dispatch"; }],
  ["pull request", (a) => { a.run.event = "pull_request"; }],
  ["wrong branch", (a) => { a.run.head_branch = "feature"; }],
  ["running source workflow", (a) => { a.run.status = "in_progress"; a.run.conclusion = null; }],
  ["failed run", (a) => { a.run.conclusion = "failure"; }],
  ["wrong workflow", (a) => { a.run.path = ".github/workflows/fault-injection.yml"; }],
  ["different attempt", (a) => { a.run.run_attempt = 3; }],
  ["missing job", (a) => { a.jobs.pop(); }],
  ["duplicate required job", (a) => { a.jobs.push(copy(a.jobs[0])); }],
  ["skipped job", (a) => { a.jobs[1].conclusion = "skipped"; }],
  ["failed job", (a) => { a.jobs[1].conclusion = "failure"; }],
  ["running job", (a) => { a.jobs[1].status = "in_progress"; a.jobs[1].conclusion = null; }],
  ["job from another attempt", (a) => { a.jobs[1].run_attempt = 1; }],
  ["wrong artifact ID", (a) => { a.artifact.id++; }],
  ["wrong artifact name", (a) => { a.artifact.name = "other-certificate"; }],
  ["wrong artifact digest", (a) => { a.artifact.digest = `sha256:${"e".repeat(64)}`; }],
  ["missing artifact digest", (a) => { delete a.artifact.digest; }],
  ["expired artifact", (a) => { a.artifact.expired = true; }],
  ["artifact past expiry timestamp", (a) => { a.artifact.expires_at = "2026-10-27T00:00:00Z"; }],
  ["artifact from another run", (a) => { a.artifact.workflow_run.id++; }],
  ["artifact from another SHA", (a) => { a.artifact.workflow_run.head_sha = OTHER; }],
  ["artifact from another repository", (a) => { a.artifact.workflow_run.repository_id = 999; }],
  ["artifact from another head repository", (a) => { a.artifact.workflow_run.head_repository_id = 999; }],
];

for (const [name, change] of unsafeEvidence) {
  test(`planning refuses unsafe staging evidence: ${name}`, async () => {
    const { api, releaser } = await setup();
    change(api);
    await assert.rejects(releaser.plan(input()));
    assertNoMutations(api);
  });
}

for (const [name, evidence] of [
  ["absent", undefined], ["empty jobs", { ...EVIDENCE, jobs: [] }],
  ["repeated requested job", { ...EVIDENCE, jobs: ["staging", "staging"] }],
  ["noncanonical digest", { ...EVIDENCE, artifactDigest: `SHA256:${"D".repeat(64)}` }],
  ["path traversal", { ...EVIDENCE, workflow: "../staging.yml" }],
  ["zero attempt", { ...EVIDENCE, attempt: 0 }],
]) {
  test(`plan refuses malformed evidence: ${name}`, async () => {
    const { api, releaser } = await setup();
    await assert.rejects(releaser.plan(input({ evidence: copy(evidence) })));
    assertNoMutations(api);
  });
}

test("publish re-fetches run, all required jobs and artifact instead of trusting a saved plan", async () => {
  for (const [name, change] of unsafeEvidence) {
    const { api, releaser } = await setup();
    const plan = await releaser.plan(input());
    const start = api.calls.length;
    change(api);
    await assert.rejects(releaser.publish(JSON.parse(JSON.stringify(plan))), undefined, name);
    assertNoMutations(api, start);
  }
});

for (const status of [403, 404, 429, 500]) {
  test(`evidence HTTP ${status} is not absence and never permits publication`, async () => {
    const { api, releaser } = await setup();
    const plan = await releaser.plan(input());
    const start = api.calls.length;
    api.fault = (_, path) => path.includes("/actions/") ? httpError(status) : null;
    await assert.rejects(releaser.publish(plan));
    assertNoMutations(api, start);
  });
}

for (const [field, value] of [
  ["runId", EVIDENCE.runId + 1], ["attempt", EVIDENCE.attempt + 1],
  ["workflow", ".github/workflows/other-staging.yml"], ["jobs", ["staging"]],
]) {
  test(`full-evidence planning refuses a different source ${field} before any publication`, async () => {
    const selector = { ...source(), [field]: value };
    const independent = await setup();
    independent.api.run.id = selector.runId;
    independent.api.run.run_attempt = selector.attempt;
    independent.api.run.path = selector.workflow;
    independent.api.jobs = independent.api.jobs.filter(({ name }) => selector.jobs.includes(name))
      .map((job) => ({ ...job, run_id: selector.runId, run_attempt: selector.attempt }));
    await independent.releaser.plan(deferredInput({ source: selector }));
    assertNoMutations(independent.api);

    const { api, releaser } = await setup();
    await assert.rejects(releaser.plan(input({ source: selector })));
    assertNoMutations(api);
    assert.equal(api.refs.size, 0);
    assert.equal(api.releases.size, 0);
  });
}

test("matching source and full evidence remain valid through planning and publication", async () => {
  const { api, releaser } = await setup();
  const plan = await releaser.plan(input({ source: source() }));
  assertNoMutations(api);
  const frozen = copy(plan);
  const result = await releaser.publish(JSON.parse(JSON.stringify(plan)));
  assert.equal(result.created, true);
  assert.equal(result.tag, frozen.tag);
  assert.deepEqual(plan, frozen);
  assert.deepEqual([...api.refs.keys()], [plan.tag]);
  const start = api.calls.length;
  assert.equal((await releaser.publish(plan)).created, false);
  assertNoMutations(api, start);
});

test("deferred source planning freezes an eligible active run before jobs and artifact exist", async () => {
  const { api, releaser } = await deferredSetup();
  api.jobs = [];
  api.artifact = null;
  const plan = await releaser.plan(deferredInput());
  assert.deepEqual(plan.source, source());
  assert.equal(plan.version, "2026.1028.0-canary.0");
  assert.equal(plan.sha, SHA);
  assert.deepEqual(plan, JSON.parse(JSON.stringify(plan)));
  assert.deepEqual(plan, await releaser.plan(deferredInput()));
  assert.ok(api.calls.some(({ path }) => path.includes("/actions/runs/901/attempts/2")),
    "the source selector must independently resolve through GitHub");
  assert.ok(!api.calls.some(({ path }) => /\/artifacts(?:\/|\?|$)/.test(path)),
    "pre-build planning must not require an artifact that cannot exist yet");
  assertNoMutations(api);
});

for (const [name, change] of unsafeEvidence.filter(([name]) => [
  "foreign repository", "foreign head repository", "different SHA", "manual dispatch",
  "pull request", "wrong branch", "failed run", "wrong workflow", "different attempt",
].includes(name))) {
  test(`deferred source planning refuses ${name}`, async () => {
    const { api, releaser } = await deferredSetup();
    if (name === "failed run") api.run.status = "completed";
    change(api);
    await assert.rejects(releaser.plan(deferredInput()));
    assertNoMutations(api);
  });
}

for (const [name, value] of [
  ["empty jobs", { ...source(), jobs: [] }],
  ["duplicate jobs", { ...source(), jobs: ["staging", "staging"] }],
  ["zero run", { ...source(), runId: 0 }],
  ["zero attempt", { ...source(), attempt: 0 }],
  ["non-workflow path", { ...source(), workflow: "../staging.yml" }],
]) {
  test(`deferred source planning refuses malformed selector: ${name}`, async () => {
    const { api, releaser } = await deferredSetup();
    await assert.rejects(releaser.plan(deferredInput({ source: value })));
    assertNoMutations(api);
  });
}

for (const status of [403, 404, 429, 500]) {
  test(`deferred source lookup HTTP ${status} is not an eligible active run`, async () => {
    const { api, releaser } = await deferredSetup();
    api.fault = (_, path) => path.includes("/actions/") ? httpError(status) : null;
    await assert.rejects(releaser.plan(deferredInput()), { status });
    assertNoMutations(api);
  });
}

test("deferred canary publishes after named jobs succeed while its successor publisher is active", async () => {
  const { Releaser } = await engine();
  const { api, releaser } = await deferredSetup();
  const plan = JSON.parse(JSON.stringify(await releaser.plan(deferredInput())));
  const frozen = copy(plan);
  completeRequiredJobs(api);
  api.jobs.push({ id: 600, name: "publish-canary", run_id: EVIDENCE.runId,
    run_attempt: EVIDENCE.attempt, head_sha: SHA, status: "in_progress", conclusion: null });
  const publisher = new Releaser({ api, repository: REPOSITORY,
    now: () => new Date("2026-10-29T00:00:10Z") });
  const start = api.calls.length;
  const result = await publisher.publish(plan, { evidence: copy(EVIDENCE) });
  assert.equal(result.created, true);
  assert.equal(result.date, DATE);
  assert.equal(result.tag, frozen.tag);
  assert.deepEqual(plan, frozen, "publication must not mutate the frozen pre-build plan");
  assert.ok(api.calls.slice(start).some(({ path }) => path.includes("/attempts/2/jobs")));
  assert.ok(api.calls.slice(start).some(({ path }) => path.includes("/actions/artifacts/77")));
  assert.deepEqual(api.mutations().map(({ path }) => path.split(`/repos/${REPOSITORY}`)[1]),
    ["/git/tags", "/git/refs"]);
  assert.equal(api.releases.size, 0);
  const message = api.mutations()[0].body.message;
  // The contract fixes the structured payload, not an optional annotation format prefix.
  const annotation = JSON.parse(message.slice(message.indexOf("{")));
  assert.deepEqual(annotation.publicationEvidence, EVIDENCE);
  const annotatedPlan = annotation.plan ?? Object.fromEntries(
    Object.entries(annotation).filter(([key]) => key !== "publicationEvidence"));
  assert.deepEqual(annotatedPlan, frozen, "the annotation retains the unchanged portable plan");
  const replayStart = api.calls.length;
  assert.equal((await publisher.publish(copy(plan), { evidence: copy(EVIDENCE) })).created, false);
  assertNoMutations(api, replayStart);
});

test("deferred canary dry-run requires the same completed-job proof and makes zero writes", async () => {
  const { api, releaser } = await deferredSetup();
  const plan = await releaser.plan(deferredInput());
  completeRequiredJobs(api);
  const start = api.calls.length;
  const result = await releaser.publish(plan, { evidence: copy(EVIDENCE), dryRun: true });
  assert.equal(result.dryRun, true);
  assert.equal(result.created, false);
  assertNoMutations(api, start);
  api.jobs[1].conclusion = "failure";
  await assert.rejects(releaser.publish(plan, { evidence: copy(EVIDENCE), dryRun: true }));
  assertNoMutations(api, start);
});

test("a deferred source selector alone never authorizes publication", async () => {
  const { api, releaser } = await deferredSetup();
  const plan = await releaser.plan(deferredInput());
  completeRequiredJobs(api);
  const start = api.calls.length;
  await assert.rejects(releaser.publish(plan));
  assertNoMutations(api, start);
});

for (const [name, change] of unsafeEvidence.filter(([name]) => name !== "running source workflow")) {
  test(`deferred publication re-fetches and refuses unsafe evidence: ${name}`, async () => {
    const { api, releaser } = await deferredSetup();
    const plan = await releaser.plan(deferredInput());
    completeRequiredJobs(api);
    if (name === "failed run") api.run.status = "completed";
    change(api);
    const start = api.calls.length;
    await assert.rejects(releaser.publish(copy(plan), { evidence: copy(EVIDENCE) }));
    assertNoMutations(api, start);
  });
}

for (const [name, change] of [
  ["job from another run", (api) => { api.jobs[1].run_id++; }],
  ["job from another SHA", (api) => { api.jobs[1].head_sha = OTHER; }],
  ["cancelled completed run", (api) => { api.run.status = "completed"; api.run.conclusion = "cancelled"; }],
]) {
  test(`deferred publication refuses ${name}`, async () => {
    const { api, releaser } = await deferredSetup();
    const plan = await releaser.plan(deferredInput());
    completeRequiredJobs(api);
    change(api);
    const start = api.calls.length;
    await assert.rejects(releaser.publish(plan, { evidence: copy(EVIDENCE) }));
    assertNoMutations(api, start);
  });
}

for (const field of ["runId", "attempt", "workflow", "jobs", "artifactId", "artifactName", "artifactDigest"]) {
  test(`deferred replay refuses changed ${field} even when GitHub validates the replacement proof`, async () => {
    const { api, releaser } = await deferredSetup();
    const plan = await releaser.plan(deferredInput());
    completeRequiredJobs(api);
    await releaser.publish(plan, { evidence: copy(EVIDENCE) });
    const evidence = copy(EVIDENCE);
    if (field === "runId") {
      evidence.runId++;
      api.run.id = evidence.runId;
      api.jobs.forEach((job) => { job.run_id = evidence.runId; });
      api.artifact.workflow_run.id = evidence.runId;
    }
    if (field === "attempt") {
      evidence.attempt++;
      api.run.run_attempt = evidence.attempt;
      api.jobs.forEach((job) => { job.run_attempt = evidence.attempt; });
    }
    if (field === "workflow") {
      evidence.workflow = ".github/workflows/another-staging.yml";
      api.run.path = evidence.workflow;
    }
    if (field === "jobs") {
      evidence.jobs = ["different-certification"];
      api.jobs = [{ ...api.jobs[0], name: evidence.jobs[0] }];
    }
    if (field === "artifactId") { evidence.artifactId++; api.artifact.id = evidence.artifactId; }
    if (field === "artifactName") { evidence.artifactName = "replacement-certificate"; api.artifact.name = evidence.artifactName; }
    if (field === "artifactDigest") { evidence.artifactDigest = `sha256:${"e".repeat(64)}`; api.artifact.digest = evidence.artifactDigest; }
    const start = api.calls.length;
    await assert.rejects(releaser.publish(copy(plan), { evidence }));
    assertNoMutations(api, start);
    assert.equal(api.refs.size, 1);
  });
}

for (const field of ["runId", "attempt", "workflow", "jobs"]) {
  test(`deferred portable plan binds frozen source ${field}`, async () => {
    const { api, releaser } = await deferredSetup();
    const plan = JSON.parse(JSON.stringify(await releaser.plan(deferredInput())));
    completeRequiredJobs(api);
    plan.source[field] = field === "jobs" ? ["staging"] : field === "workflow"
      ? ".github/workflows/other.yml" : plan.source[field] + 1;
    const start = api.calls.length;
    await assert.rejects(releaser.publish(plan, { evidence: copy(EVIDENCE) }));
    assertNoMutations(api, start);
  });
}

test("same-source same-SHA deferred canary under another operation refuses without new writes", async () => {
  const { api, releaser } = await deferredSetup();
  const first = await releaser.plan(deferredInput());
  completeRequiredJobs(api);
  await releaser.publish(first, { evidence: copy(EVIDENCE) });
  const start = api.calls.length;
  await assert.rejects(async () => {
    const second = await releaser.plan(deferredInput({ operation: "another-operation" }));
    await releaser.publish(second, { evidence: copy(EVIDENCE) });
  });
  assertNoMutations(api, start);
  assert.deepEqual([...api.refs.keys()], [first.tag]);
});

test("stable cannot use deferred planning or the active-run canary publication exception", async () => {
  const { api, releaser } = await deferredSetup();
  const canary = await releaser.plan(deferredInput());
  completeRequiredJobs(api);
  await releaser.publish(canary, { evidence: copy(EVIDENCE) });
  const start = api.calls.length;
  await assert.rejects(releaser.plan(deferredInput({ channel: "stable", sourceCanary: canary.tag })));
  await assert.rejects(releaser.plan(input({ channel: "stable", sourceCanary: canary.tag })));
  assertNoMutations(api, start);
  api.run.status = "completed";
  api.run.conclusion = "success";
  const stable = await releaser.plan(input({ channel: "stable", sourceCanary: canary.tag }));
  api.run.status = "in_progress";
  api.run.conclusion = null;
  const beforePublish = api.calls.length;
  await assert.rejects(releaser.publish(stable));
  assertNoMutations(api, beforePublish);
  api.run.status = "completed";
  api.run.conclusion = "success";
  assert.ok((await releaser.publish(stable)).releaseUrl);
});

test("canary publish annotates provenance, creates only a tag, and exact replay is idempotent", async () => {
  const { api, releaser } = await setup();
  const plan = await releaser.plan(input());
  const first = await releaser.publish(JSON.parse(JSON.stringify(plan)));
  assert.equal(first.tag, plan.tag);
  assert.equal(first.version, plan.version);
  assert.equal(first.created, true);
  assert.equal(first.dryRun, false);
  assert.ok(!first.releaseUrl);
  const writes = api.mutations();
  assert.deepEqual(writes.map(({ method, path }) => [method, path.split(`/repos/${REPOSITORY}`)[1]]),
    [["POST", "/git/tags"], ["POST", "/git/refs"]]);
  assert.equal(writes[0].body.object, SHA);
  for (const text of [input().operation, SHA, DATE, plan.version, REPOSITORY]) {
    assert.ok(writes[0].body.message.includes(text), `annotation must bind ${text}`);
  }
  const beforeReplay = api.calls.length;
  const replay = await releaser.publish(plan);
  assert.equal(replay.created, false);
  assert.equal(replay.tag, plan.tag);
  assertNoMutations(api, beforeReplay);
});

test("a serialized plan works on a fresh Releaser in another job", async () => {
  const { Releaser } = await engine();
  const { api, releaser } = await setup();
  const plan = JSON.parse(JSON.stringify(await releaser.plan(input())));
  const secondJob = new Releaser({ api, repository: REPOSITORY, now: () => new Date("2026-10-29T00:00:10Z") });
  const result = await secondJob.publish(plan);
  assert.equal(result.date, DATE);
  assert.equal(result.version, "2026.1028.0-canary.0");
});

test("UTC date freezes at plan time across midnight", async () => {
  const { api, releaser, setClock } = await setup();
  const plan = await releaser.plan(input());
  setClock("2026-10-29T00:00:10Z");
  const result = await releaser.publish(plan);
  assert.equal(result.date, DATE);
  assert.equal(result.tag, "canary/v2026.1028.0-canary.0");
  assert.ok(!api.mutations().some(({ body }) => body?.ref?.includes("1029")));
});

test("a plan/deploy/publish collision refuses rather than silently reallocating", async () => {
  const { api, releaser } = await setup();
  const plan = await releaser.plan(input());
  api.addRef(plan.tag, OTHER);
  await assert.rejects(releaser.publish(plan));
  assert.equal(api.refs.get(plan.tag).object.sha, OTHER);
  assert.deepEqual([...api.refs.keys()], [plan.tag]);
  assert.equal(api.releases.size, 0);
  assert.ok(api.mutations().every(({ method }) => method === "POST"));
});

test("a lightweight same-tag same-SHA ref is not proof of the operation", async () => {
  const { api, releaser } = await setup();
  const plan = await releaser.plan(input());
  api.addRef(plan.tag, SHA);
  await assert.rejects(releaser.publish(plan));
  assert.equal(api.releases.size, 0);
});

test("same SHA under a different operation is not an exact retry", async () => {
  const { releaser } = await setup();
  const plan = await releaser.plan(input());
  await releaser.publish(plan);
  await assert.rejects(async () => {
    const other = await releaser.plan(input({ operation: "different-operation" }));
    await releaser.publish(other);
  });
});

for (const [key, value] of [
  ["sha", OTHER], ["operation", "tampered-operation"], ["date", "2026-10-27"],
  ["namespace", "build"], ["version", "2026.1028.99-canary.0"],
  ["tag", "canary/v2026.1028.99-canary.0"], ["policy", "live-digest"],
  ["repository", "untrusted/fork"],
]) {
  test(`publish rejects a portable plan with tampered ${key}`, async () => {
    const { api, releaser } = await setup();
    const plan = JSON.parse(JSON.stringify(await releaser.plan(input())));
    replaceBoundField(plan, key, value);
    const start = api.calls.length;
    await assert.rejects(releaser.publish(plan));
    assertNoMutations(api, start);
  });
}

test("a plan cannot be published by another repository's Releaser", async () => {
  const { Releaser } = await engine();
  const { releaser } = await setup();
  const plan = await releaser.plan(input());
  const foreign = new FakeGitHub("untrusted/fork");
  const publisher = new Releaser({ api: foreign, repository: "untrusted/fork" });
  await assert.rejects(publisher.publish(plan));
  assertNoMutations(foreign);
});

for (const mode of ["exact annotation and SHA", "foreign annotation", "foreign SHA"]) {
  const exact = mode === "exact annotation and SHA";
  test(`a ref creation race is ${exact ? "accepted only for" : "rejected for"} ${mode}`, async () => {
    const { api, releaser } = await setup();
    const plan = await releaser.plan(input());
    api.onRefCreate = (name, body, fake) => {
      if (exact) fake.addRef(name, body.sha, "tag");
      else {
        const other = "9".repeat(40);
        fake.objects.set(other, { sha: other, tag: name,
          message: mode === "foreign SHA" ? fake.objects.get(body.sha).message : "foreign operation",
          object: { type: "commit", sha: mode === "foreign SHA" ? OTHER : SHA } });
        fake.addRef(name, other, "tag");
      }
    };
    if (exact) assert.equal((await releaser.publish(plan)).tag, plan.tag);
    else await assert.rejects(releaser.publish(plan));
    const refPosts = api.mutations().filter(({ path }) => /\/git\/refs$/.test(path));
    assert.equal(refPosts.length, 1, "do not retry allocation/ref creation after a collision");
    const collision = api.calls.indexOf(refPosts[0]);
    assert.equal(api.calls.slice(collision + 1).filter(({ method, path }) =>
      method === "GET" && /\/git\/refs?\/tags\//.test(path)).length, 1,
    "a ref collision gets one exact re-read, not an unbounded retry loop");
    assert.ok(api.mutations().every(({ method }) => method === "POST"));
  });
}

test("non-collision 422 from annotated tag creation propagates", async () => {
  const { api, releaser } = await setup();
  const plan = await releaser.plan(input());
  api.fault = (method, path) => method === "POST" && /\/git\/tags$/.test(path)
    ? httpError(422, "invalid tagger email") : null;
  await assert.rejects(releaser.publish(plan), { status: 422 });
  assert.equal(api.refs.size, 0);
  assert.equal(api.releases.size, 0);
});

test("non-collision 422 from ref creation propagates without a new version", async () => {
  const { api, releaser } = await setup();
  const plan = await releaser.plan(input());
  api.fault = (method, path) => method === "POST" && /\/git\/refs$/.test(path)
    ? httpError(422, "reference name is invalid") : null;
  await assert.rejects(releaser.publish(plan), { status: 422 });
  assert.equal(api.refs.size, 0);
  assert.equal(api.mutations().filter(({ path }) => /\/git\/refs$/.test(path)).length, 1);
});

for (const status of [403, 429, 500]) {
  test(`exact ref lookup HTTP ${status} is not a missing ref`, async () => {
    const { api, releaser } = await setup();
    const plan = await releaser.plan(input());
    api.fault = (method, path) => method === "GET" && /\/git\/refs?\/tags\//.test(path) ? httpError(status) : null;
    await assert.rejects(releaser.publish(plan), { status });
    assert.equal(api.refs.size, 0);
  });
}

test("stable promotion requires source canary, verifies it, then publishes the existing tag's Release", async () => {
  const { api, releaser, plan, canary } = await stableSetup();
  assert.equal(plan.version, "2026.1028.0");
  assert.notEqual(plan.tag, canary.tag);
  const result = await releaser.publish(plan);
  assert.equal(result.created, true);
  assert.match(result.releaseUrl, /^https:\/\/github\.com\//);
  const body = releaseBody(api);
  assert.equal(body.tag_name, plan.tag);
  assert.equal(body.make_latest, "true", "explicit numeric Latest policy, not the GitHub legacy default");
  assert.ok(!body.target_commitish || body.target_commitish === SHA, "must not create a tag from a branch name");
  assert.equal(typeof body.body, "string", "notes go in the JSON API body");
  const start = api.calls.length;
  const replay = await releaser.publish(plan);
  assert.equal(replay.created, false);
  assert.equal(replay.releaseUrl, result.releaseUrl);
  assertNoMutations(api, start);
});

test("stable staged promotion without source canary fails", async () => {
  const { api, releaser } = await setup();
  await assert.rejects(releaser.plan(input({ channel: "stable" })));
  assertNoMutations(api);
});

for (const mode of ["other SHA", "missing", "lightweight", "different artifact"]) {
  test(`source canary is re-fetched at publish and refuses ${mode}`, async () => {
    const { api, releaser, plan, canary } = await stableSetup();
    const ref = api.refs.get(canary.tag);
    if (mode === "other SHA") api.objects.get(ref.object.sha).object.sha = OTHER;
    if (mode === "lightweight") api.replaceRef(canary.tag, SHA);
    if (mode === "missing") {
      for (const page of api.refPages) {
        for (let i = page.length - 1; i >= 0; i--) if (page[i].ref === ref.ref) page.splice(i, 1);
      }
      api.refs.delete(canary.tag);
    }
    if (mode === "different artifact") {
      const object = api.objects.get(ref.object.sha);
      assert.ok(object.message.includes(EVIDENCE.artifactDigest), "annotation carries source digest");
      object.message = object.message.replaceAll(EVIDENCE.artifactDigest, `sha256:${"e".repeat(64)}`);
    }
    const start = api.calls.length;
    await assert.rejects(releaser.publish(plan));
    assertNoMutations(api, start);
  });
}

test("a source canary from a different completed attempt cannot authorize stable promotion", async () => {
  const { api, releaser, canary } = await stableSetup();
  api.run.run_attempt = 3;
  api.jobs.forEach((job) => { job.run_attempt = 3; });
  const start = api.calls.length;
  await assert.rejects(releaser.plan(input({ channel: "stable", sourceCanary: canary.tag,
    evidence: { ...EVIDENCE, attempt: 3 } })));
  assertNoMutations(api, start);
});

test("a source canary for another artifact cannot authorize a new staging artifact on the same SHA", async () => {
  const { api, releaser, canary } = await stableSetup();
  api.artifact.digest = `sha256:${"e".repeat(64)}`;
  await assert.rejects(releaser.plan(input({ channel: "stable", operation: "promote-different-artifact",
    sourceCanary: canary.tag, evidence: { ...EVIDENCE, artifactDigest: api.artifact.digest } })));
});

test("missing stable Release fails ordinary retry and requires explicit repair without retagging", async () => {
  const { api, releaser, plan } = await stableSetup();
  await releaser.publish(plan);
  api.releases.delete(plan.tag);
  const start = api.calls.length;
  await assert.rejects(releaser.publish(plan));
  assertNoMutations(api, start);
  const beforeRepair = api.calls.length;
  const repaired = await releaser.publish(plan, { repair: true });
  assert.ok(repaired.releaseUrl);
  assert.deepEqual(api.calls.slice(beforeRepair).filter(({ method }) => method !== "GET")
    .map(({ path }) => path.split(`/repos/${REPOSITORY}`)[1]), ["/releases"]);
});

for (const status of [403, 429, 500]) {
  test(`Release lookup HTTP ${status} is not a missing Release, including explicit repair`, async () => {
    const { api, releaser, plan } = await stableSetup();
    await releaser.publish(plan);
    const start = api.calls.length;
    api.fault = (method, path) => method === "GET" && path.includes("/releases/tags/") ? httpError(status) : null;
    await assert.rejects(releaser.publish(plan, { repair: true }), { status });
    assertNoMutations(api, start);
  });
}

test("Release creation failure is surfaced and its half-published tag requires explicit repair", async () => {
  const { api, releaser, plan } = await stableSetup();
  api.fault = (method, path) => method === "POST" && /\/releases$/.test(path) ? httpError(503) : null;
  await assert.rejects(releaser.publish(plan), { status: 503 });
  assert.ok(api.refs.has(plan.tag));
  assert.equal(api.releases.size, 0);
  api.fault = null;
  await assert.rejects(releaser.publish(plan));
  await releaser.publish(plan, { repair: true });
  assert.equal(api.releases.size, 1);
});

test("historical date needs explicit repair and must never advance Latest", async () => {
  const { api, releaser, stableInput } = await stableSetup();
  api.addRef("v2026.1028.9", OLD);
  const historical = await releaser.plan({ ...stableInput, repairDate: "2026-10-27" });
  assert.equal(historical.date, "2026-10-27");
  const start = api.calls.length;
  await assert.rejects(releaser.publish(historical));
  assertNoMutations(api, start);
  await releaser.publish(historical, { repair: true });
  assert.equal(releaseBody(api).make_latest, "false");
});

test("Latest compares stable tags numerically rather than patch strings", async () => {
  const { api, releaser, stableInput } = await stableSetup();
  api.addRef("v2026.1028.9", OLD, "commit", 0);
  api.addRef("v2026.1028.10", OTHER, "commit", 1);
  const plan = await releaser.plan(stableInput);
  assert.equal(plan.version, "2026.1028.11");
  await releaser.publish(plan);
  assert.equal(releaseBody(api).make_latest, "true");
});

test("an explicit latest=never cannot be overridden by a newer numeric version", async () => {
  const { api, releaser, stableInput } = await stableSetup();
  const plan = await releaser.plan({ ...stableInput, latest: "never" });
  await releaser.publish(plan);
  assert.equal(releaseBody(api).make_latest, "false");
});

test("a newer stable tag appearing after plan prevents moving Latest backwards", async () => {
  const { api, releaser, plan } = await stableSetup();
  api.addRef("v2026.1029.0", OLD, "commit", 1);
  await releaser.publish(plan);
  assert.equal(releaseBody(api).make_latest, "false");
  assert.equal(releaseBody(api).tag_name, plan.tag, "do not reallocate the deployed version");
});

test("a legacy daily November stable appearing after an October plan prevents advancing Latest", async () => {
  const { api, releaser, plan } = await stableSetup();
  const frozen = copy(plan);
  const boundary = "v2026.11.01";
  api.addRef(boundary, OLD, "commit", 1);
  api.releases.set(boundary, { id: 99, tag_name: boundary, draft: false, prerelease: false,
    published_at: "2026-11-01T12:00:00Z", html_url: `https://github.com/${REPOSITORY}/releases/tag/${boundary}` });
  const result = await releaser.publish(JSON.parse(JSON.stringify(plan)));
  assert.equal(result.created, true);
  assert.equal(result.date, DATE);
  assert.equal(result.tag, frozen.tag);
  assert.equal(releaseBody(api).tag_name, frozen.tag, "the frozen deployed version is not reallocated");
  assert.equal(releaseBody(api).make_latest, "false", "numeric legacy dates participate in Latest ordering");
  assert.deepEqual(plan, frozen);
});

for (const channel of ["canary", "stable"]) {
  test(`${channel} dry-run revalidates but makes zero mutating requests`, async () => {
    const fixture = channel === "stable" ? await stableSetup() : await setup();
    const plan = fixture.plan ?? await fixture.releaser.plan(input());
    const start = fixture.api.calls.length;
    const result = await fixture.releaser.publish(JSON.parse(JSON.stringify(plan)), { dryRun: true });
    assert.equal(result.dryRun, true);
    assert.equal(result.created, false);
    assert.equal(result.tag, plan.tag);
    assertNoMutations(fixture.api, start);
    fixture.api.run.conclusion = "failure";
    await assert.rejects(fixture.releaser.publish(plan, { dryRun: true }));
    assertNoMutations(fixture.api, start);
  });
}

for (const policy of ["live-digest", "direct-release"]) {
  test(`${policy} cannot bypass staging on an unrelated repository`, async () => {
    const { api, releaser } = await setup("untrusted/arbitrary-product");
    await assert.rejects(releaser.plan(input({ channel: "stable", policy, evidence: undefined })));
    assertNoMutations(api);
  });
}

for (const [repository, namespace] of [
  ["TogetherWeOwn/kagi-shinobi", ""], ["TogetherWeOwn/routeware-shadow-api", "build"],
]) {
  test(`direct-release is scoped to stable ${repository} ${namespace || "root"} releases`, async () => {
    const { api, releaser } = await setup(repository);
    const plan = await releaser.plan(input({ channel: "stable", policy: "direct-release", namespace,
      evidence: undefined }));
    assert.equal(plan.tag, `${namespace ? `${namespace}/` : ""}v2026.1028.0`);
    await releaser.publish(plan);
    assert.equal(releaseBody(api).tag_name, plan.tag);
    if (namespace) assert.equal(releaseBody(api).make_latest, "false", "build namespace never advances Latest");
    await assert.rejects(releaser.plan(input({ policy: "direct-release", namespace, evidence: undefined })));
  });
}

test("Kagi direct-release refuses the same SHA under another operation without a second identity", async () => {
  const { api, releaser } = await setup("TogetherWeOwn/kagi-shinobi");
  const request = input({ channel: "stable", policy: "direct-release", namespace: "",
    evidence: undefined, operation: "direct-release-first" });
  const first = await releaser.plan(request);
  await releaser.publish(first);
  const start = api.calls.length;
  assert.equal((await releaser.publish(copy(first))).created, false, "exact-operation replay stays allowed");
  await assert.rejects(async () => {
    const second = await releaser.plan({ ...request, operation: "direct-release-other" });
    await releaser.publish(second);
  });
  assertNoMutations(api, start);
  assert.deepEqual([...api.refs.keys()], [first.tag]);
  assert.deepEqual([...api.releases.keys()], [first.tag]);
});

for (const channel of ["canary", "stable"]) {
  for (const winner of ["earlier", "later"]) {
    test(`two frozen ${channel} plans for one operation refuse a second identity when ${winner} date publishes first`, async () => {
      const repository = channel === "stable" ? "TogetherWeOwn/kagi-shinobi" : REPOSITORY;
      const fixture = await setup(repository);
      const request = channel === "stable"
        ? input({ channel, policy: "direct-release", evidence: undefined }) : input();
      const earlier = JSON.parse(JSON.stringify(await fixture.releaser.plan(request)));
      fixture.setClock("2026-10-29T00:00:10Z");
      const later = JSON.parse(JSON.stringify(await fixture.releaser.plan(request)));
      assert.notEqual(earlier.tag, later.tag);
      assert.equal(earlier.date, DATE);
      assert.equal(later.date, "2026-10-29");
      assertNoMutations(fixture.api);
      const first = winner === "earlier" ? earlier : later;
      const second = winner === "earlier" ? later : earlier;
      await fixture.releaser.publish(first);
      const start = fixture.api.calls.length;
      const { Releaser } = await engine();
      const nextJob = new Releaser({ api: fixture.api, repository,
        now: () => new Date("2026-10-29T00:00:20Z") });
      await assert.rejects(nextJob.publish(copy(second)), "publication must recheck operation identity, not only exact tag");
      assertNoMutations(fixture.api, start);
      assert.deepEqual([...fixture.api.refs.keys()], [first.tag]);
      assert.deepEqual([...fixture.api.releases.keys()], channel === "stable" ? [first.tag] : []);
      const replay = await nextJob.publish(copy(first));
      assert.equal(replay.created, false);
      assert.equal(replay.tag, first.tag);
      assertNoMutations(fixture.api, start);
    });
  }
}

test("Routeware direct-release cannot silently publish root stable product releases", async () => {
  const { api, releaser } = await setup("TogetherWeOwn/routeware-shadow-api");
  await assert.rejects(releaser.plan(input({ channel: "stable", policy: "direct-release", evidence: undefined })));
  assertNoMutations(api);
});

test("live-digest is stable-only even in the TWO bot repository", async () => {
  const { api, releaser } = await setup("TogetherWeOwn/two-bot-next");
  await assert.rejects(releaser.plan(input({ policy: "live-digest", evidence: undefined })));
  assertNoMutations(api);
});

test("the scoped TWO bot stable live-digest exception does not assert a rollout", async () => {
  const { api, releaser } = await setup("TogetherWeOwn/two-bot-next");
  const plan = await releaser.plan(input({ channel: "stable", policy: "live-digest", evidence: undefined }));
  await releaser.publish(plan);
  assert.equal(api.releases.size, 1);
  assert.ok(api.mutations().every(({ path }) => /\/git\/(tags|refs)$|\/releases$/.test(path)),
    "the releaser must never create deployments, dispatch workflows or edit repository settings");
});

for (const legacy of [false, true]) {
  test(`release notes use ${legacy ? "a legacy daily release" : "the reachable previous stable"} boundary, not future/unreachable tags`, async () => {
    const { api, releaser } = await setup();
    const boundary = legacy ? "v2026.10.27" : "v2026.1027.9";
    const future = "6".repeat(40);
    const unreachable = "7".repeat(40);
    api.addRef(boundary, OLD);
    api.addRef("v2026.1029.0", future, "commit", 1);
    api.addRef("v2026.1028.9", unreachable, "commit", 1);
    api.unreachable.add(unreachable);
    if (legacy) api.releases.set(boundary, { id: 99, tag_name: boundary, draft: false, prerelease: false,
      published_at: "2026-10-27T12:00:00Z", html_url: `https://github.com/${REPOSITORY}/releases/tag/${boundary}` });
    const after = { sha: SHA, message: "feat: AFTER-ELIGIBLE-BOUNDARY" };
    const before = { sha: OLD, message: "fix: DO-NOT-INCLUDE-PRE-BOUNDARY" };
    const poison = { sha: OTHER, message: "fix: DO-NOT-INCLUDE-FUTURE-OR-UNREACHABLE" };
    api.commitMessages = [after, before];
    api.compareMessages.set(OLD, [after]);
    api.compareMessages.set(future, [poison]);
    api.compareMessages.set(unreachable, [poison]);
    const canary = await releaser.plan(input());
    await releaser.publish(canary);
    const plan = await releaser.plan(input({ channel: "stable", sourceCanary: canary.tag }));
    await releaser.publish(plan);
    const notes = releaseBody(api).body;
    assert.ok(notes.includes("AFTER-ELIGIBLE-BOUNDARY"));
    assert.ok(!notes.includes("DO-NOT-INCLUDE-PRE-BOUNDARY"));
    assert.ok(!notes.includes("DO-NOT-INCLUDE-FUTURE-OR-UNREACHABLE"));
  });
}

for (const legacyFirst of [false, true]) {
  test(`mixed canonical and legacy history selects the newer reachable legacy boundary (${legacyFirst ? "legacy" : "canonical"} listed first)`, async () => {
    const { api, releaser } = await setup();
    const canonical = "v2025.1231.0";
    const legacy = "v2026.10.27";
    const refs = [[canonical, OLD], [legacy, OTHER]];
    if (legacyFirst) refs.reverse();
    refs.forEach(([tag, sha], page) => api.addRef(tag, sha, "commit", page));
    api.releases.set(legacy, { id: 99, tag_name: legacy, draft: false, prerelease: false,
      published_at: "2026-10-27T12:00:00Z", html_url: `https://github.com/${REPOSITORY}/releases/tag/${legacy}` });
    const after = { sha: SHA, message: "feat: AFTER-NEWER-LEGACY-BOUNDARY" };
    const between = { sha: OTHER, message: "fix: DO-NOT-REPEAT-BETWEEN-RELEASES" };
    const before = { sha: OLD, message: "fix: DO-NOT-REPEAT-BEFORE-BOTH-RELEASES" };
    api.commitMessages = [after, between, before];
    api.compareMessages.set(OLD, [after, between]);
    api.compareMessages.set(OTHER, [after]);
    const canary = await releaser.plan(input());
    await releaser.publish(canary);
    const plan = await releaser.plan(input({ channel: "stable", sourceCanary: canary.tag }));
    assert.equal(plan.date, DATE);
    assert.equal(plan.version, "2026.1028.0");
    await releaser.publish(plan);
    const notes = releaseBody(api).body;
    assert.ok(notes.includes("AFTER-NEWER-LEGACY-BOUNDARY"));
    assert.ok(!notes.includes("DO-NOT-REPEAT-BETWEEN-RELEASES"), "older canonical tags must not outrank newer legacy dates");
    assert.ok(!notes.includes("DO-NOT-REPEAT-BEFORE-BOTH-RELEASES"));
  });
}

test("large release notes go through JSON body or an explicit bound, never a truncated success", async () => {
  const { api, releaser, stableInput } = await stableSetup();
  const marker = "FINAL-RELEASE-NOTE-SENTINEL-593c";
  api.commitMessages = [{ sha: SHA, message: `feat: large release ${"x".repeat(33 * 1024 * 1024)} ${marker}` }];
  try {
    const plan = await releaser.plan(stableInput);
    await releaser.publish(plan);
  } catch (error) {
    assert.match(String(error.message), /limit|large|size|bound|bytes|length|exceed/i);
    assert.equal(api.releases.size, 0);
    return;
  }
  const body = releaseBody(api);
  assert.equal(typeof body.body, "string");
  assert.ok(body.body.includes(marker), "Release notes silently truncated a >32 MiB input");
  assert.ok(body.body.includes("x".repeat(1024)));
});
