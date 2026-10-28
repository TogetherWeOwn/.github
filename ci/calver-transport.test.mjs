// Independent production-transport acceptance: real GitHub class, deterministic fetch only.
import assert from "node:assert/strict";
import { test } from "node:test";

const transport = () => import("../actions/calver/github.mjs");
const ORIGIN = "https://api.github.com";
const REPO = "TogetherWeOwn/two-web-next";
const ROOT = `/repos/${REPO}`;
const ID = 123456;
const TOKEN = "offline-transport-token-never-log";
const repo = { id: ID, full_name: REPO, name: "two-web-next", owner: { login: "TogetherWeOwn" }, private: false };
const next = (url) => `<${url}>; rel="next"`;
const response = (body, { status = 200, link, ...options } = {}) => {
  const result = new Response(JSON.stringify(body), { status, headers: {
    "content-type": "application/json", ...(link ? { link } : {}),
  } });
  for (const [key, value] of Object.entries(options)) Object.defineProperty(result, key, { value });
  return result;
};

async function setup(handler, { readOnly = true } = {}) {
  const { GitHub } = await transport();
  const calls = [];
  const api = new GitHub({ token: TOKEN, readOnly, fetchImpl: async (input, init = {}) => {
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
    const method = init.method ?? input.method ?? "GET";
    calls.push({ url: url.href, method });
    assert.equal(url.origin, ORIGIN, "fetch must never leave the GitHub API origin");
    assert.equal(url.username + url.password + url.hash, "", "no URL credentials or fragments");
    assert.equal(init.redirect, "error", "automatic redirects must be disabled before fetch");
    return handler(url, init, calls);
  } });
  return { api, calls };
}

const shapes = [
  { name: "tags array", path: "/tags?per_page=100", key: undefined,
    items: [{ name: "v2026.1028.0", commit: { sha: "a".repeat(40) } },
      { name: "v2026.1028.10", commit: { sha: "b".repeat(40) } }], wrap: (items) => items },
  { name: "jobs object", path: "/actions/runs/901/attempts/2/jobs?filter=all&per_page=100", key: "jobs",
    items: [{ id: 1, name: "staging" }, { id: 2, name: "acceptance" }],
    wrap: (items, total = 2) => ({ total_count: total, jobs: items }) },
  { name: "artifacts object", path: "/actions/runs/901/artifacts?name=certificate&per_page=100", key: "artifacts",
    items: [{ id: 77, name: "certificate" }, { id: 78, name: "certificate" }],
    wrap: (items, total = 2) => ({ total_count: total, artifacts: items }) },
  { name: "compare commits object", path: `/compare/v2026.1027.0...${"a".repeat(40)}?per_page=100`, key: "commits",
    items: [{ sha: "a".repeat(40) }, { sha: "b".repeat(40) }],
    wrap: (items, total = 2) => ({ status: "ahead", total_commits: total, commits: items }) },
];

function pageUrl(shape, { alias = false, page = 2 } = {}) {
  const url = new URL(`${alias ? `/repositories/${ID}` : ROOT}${shape.path}`, ORIGIN);
  url.searchParams.set("page", String(page));
  return url.href;
}

for (const shape of shapes) {
  for (const alias of [false, true]) {
    test(`production pagination follows ${alias ? "verified numeric alias" : "named endpoint"}: ${shape.name}`, async () => {
      const { api, calls } = await setup((url) => {
        if (url.pathname === ROOT) return response(repo);
        const named = `${ROOT}${shape.path.split("?")[0]}`;
        const numeric = `/repositories/${ID}${shape.path.split("?")[0]}`;
        assert.ok([named, numeric].includes(url.pathname), "preserve endpoint suffix");
        const original = new URL(`${ROOT}${shape.path}`, ORIGIN);
        for (const [key, value] of original.searchParams) assert.equal(url.searchParams.get(key), value);
        assert.equal([...url.searchParams].filter(([key]) => key === "page").length <= 1, true);
        if (!url.searchParams.has("page")) return response(shape.wrap([shape.items[0]]), {
          link: `${next(pageUrl(shape, { alias }))}, <${pageUrl(shape, { alias, page: 2 })}>; rel="last"`,
        });
        assert.equal(url.searchParams.get("page"), "2");
        if (alias) assert.ok(calls.some((call) => new URL(call.url).pathname === ROOT),
          "numeric alias must be bound to independently fetched repository metadata before following");
        return response(shape.wrap([shape.items[1]]));
      });
      assert.deepEqual(await api.paginate(`${ROOT}${shape.path}`, shape.key), shape.items);
      assert.equal(calls.filter((c) => new URL(c.url).searchParams.get("page") === "2").length, 1);
      assert.ok(calls.every((call) => call.method === "GET"));
    });
  }
}

for (const [name, metadata] of [
  ["changed numeric ID", { ...repo, id: ID + 1 }],
  ["changed full_name", { ...repo, full_name: "Elsewhere/another-repo" }],
  ["missing numeric ID", { ...repo, id: undefined }],
  ["missing full_name", { ...repo, full_name: undefined }],
  ["string numeric ID", { ...repo, id: String(ID) }],
]) {
  test(`numeric Link alias refuses ${name} before fetching page two`, async () => {
    const shape = shapes[0];
    const { api, calls } = await setup((url) => url.pathname === ROOT ? response(metadata) :
      response([shape.items[0]], { link: next(pageUrl(shape, { alias: true })) }));
    await assert.rejects(api.paginate(`${ROOT}${shape.path}`));
    assert.equal(calls.filter((c) => new URL(c.url).searchParams.get("page") === "2").length, 0);
    assert.equal(calls.filter((c) => new URL(c.url).pathname === ROOT).length, 1,
      "refusal must come from repository metadata binding, not blanket alias rejection");
  });
}

for (const status of [403, 404, 422]) {
  test(`numeric alias metadata HTTP ${status} is not absence or an alias fallback`, async () => {
    const shape = shapes[0];
    const { api, calls } = await setup((url) => url.pathname === ROOT ? response({ message: "private fixture" }, { status }) :
      response([shape.items[0]], { link: next(pageUrl(shape, { alias: true })) }));
    await assert.rejects(api.paginate(`${ROOT}${shape.path}`), (error) => error.status === status);
    assert.equal(calls.length, 2);
  });
}

const unsafeLinks = [
  ["foreign API origin", `https://untrusted.invalid${ROOT}/tags?per_page=100&page=2`],
  ["non-API GitHub origin", `https://github.com${ROOT}/tags?per_page=100&page=2`],
  ["plain HTTP", `http://api.github.com${ROOT}/tags?per_page=100&page=2`],
  ["changed owner", "https://api.github.com/repos/Other/two-web-next/tags?per_page=100&page=2"],
  ["changed repository", "https://api.github.com/repos/TogetherWeOwn/other/tags?per_page=100&page=2"],
  ["changed endpoint", `${ORIGIN}${ROOT}/releases?per_page=100&page=2`],
  ["changed numeric ID", `${ORIGIN}/repositories/${ID + 1}/tags?per_page=100&page=2`],
  ["numeric alias changed endpoint", `${ORIGIN}/repositories/${ID}/releases?per_page=100&page=2`],
  ["URL username", `https://user@api.github.com${ROOT}/tags?per_page=100&page=2`],
  ["URL password", `https://user:password@api.github.com${ROOT}/tags?per_page=100&page=2`],
  ["URL fragment", `${ORIGIN}${ROOT}/tags?per_page=100&page=2#ignored`],
  ["changed per_page", `${ORIGIN}${ROOT}/tags?per_page=1&page=2`],
  ["removed per_page", `${ORIGIN}${ROOT}/tags?page=2`],
  ["injected filter", `${ORIGIN}${ROOT}/tags?per_page=100&page=2&sha=other`],
  ["duplicate page", `${ORIGIN}${ROOT}/tags?per_page=100&page=2&page=3`],
  ["non-numeric page", `${ORIGIN}${ROOT}/tags?per_page=100&page=tomorrow`],
  ["zero page", `${ORIGIN}${ROOT}/tags?per_page=100&page=0`],
  ["malformed URL", "https://[broken"],
];
for (const [name, url] of unsafeLinks) {
  test(`production Link pagination refuses ${name} without requesting the unsafe target`, async () => {
    const { api, calls } = await setup((request) => request.pathname === ROOT ? response(repo) :
      response([{ name: "v2026.1028.0" }], { link: next(url) }));
    await assert.rejects(api.paginate(`${ROOT}/tags?per_page=100`));
    assert.ok(calls.every((c) => c.url === `${ORIGIN}${ROOT}/tags?per_page=100` || c.url === `${ORIGIN}${ROOT}`),
      "only initial page and optional safe metadata lookup are allowed");
  });
}

for (const link of [
  "not-a-link", "<https://api.github.com/incomplete; rel=\"next\"",
  `${next(`${ORIGIN}${ROOT}/tags?per_page=100&page=2`)}, ${next(`${ORIGIN}${ROOT}/tags?per_page=100&page=3`)}`,
]) {
  test(`malformed or ambiguous Link header refuses: ${link.slice(0, 55)}`, async () => {
    const { api } = await setup(() => response([{ name: "fixture" }], { link }));
    await assert.rejects(api.paginate(`${ROOT}/tags?per_page=100`));
  });
}

for (const [name, modified] of [
  ["removed filter", "per_page=100&page=2"],
  ["changed filter", "filter=latest&per_page=100&page=2"],
]) {
  test(`jobs pagination refuses ${name}`, async () => {
    const shape = shapes[1];
    const { api, calls } = await setup(() => response(shape.wrap([shape.items[0]]), {
      link: next(`${ORIGIN}${ROOT}/actions/runs/901/attempts/2/jobs?${modified}`),
    }));
    await assert.rejects(api.paginate(`${ROOT}${shape.path}`, shape.key));
    assert.equal(calls.length, 1);
  });
}

test("production Link cycle refuses within a bounded number of reads", async () => {
  const { api, calls } = await setup((url) => response([{ name: url.searchParams.get("page") ?? "first" }], {
    link: next(`${ORIGIN}${ROOT}/tags?per_page=100&page=2`),
  }));
  await assert.rejects(api.paginate(`${ROOT}/tags?per_page=100`));
  assert.ok(calls.length <= 3, "cycle must not turn into unbounded requests");
});

for (const shape of shapes.filter((s) => s.key)) {
  for (const [name, secondTotal, secondItems] of [
    ["truncated missing Link", 2, null],
    ["count changed", 3, [shape.items[1]]],
    ["too many rows", 1, [shape.items[1]]],
    ["empty final page", 2, []],
  ]) {
    test(`${shape.name}: ${name} fails closed`, async () => {
      const { api } = await setup((url) => {
        if (url.searchParams.has("page")) return response(shape.wrap(secondItems ?? [], secondTotal));
        return response(shape.wrap([shape.items[0]]), secondItems === null ? {} : { link: next(pageUrl(shape)) });
      });
      await assert.rejects(api.paginate(`${ROOT}${shape.path}`, shape.key));
    });
  }
  test(`${shape.name}: missing collection is not an empty successful history`, async () => {
    const { api } = await setup(() => response({ total_count: 0, total_commits: 0 }));
    await assert.rejects(api.paginate(`${ROOT}${shape.path}`, shape.key));
  });
}

test("raw tag array rejects a non-array page", async () => {
  const { api } = await setup(() => response({ tags: [] }));
  await assert.rejects(api.paginate(`${ROOT}/tags?per_page=100`));
});

for (const method of ["POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"]) {
  test(`production readOnly blocks ${method} before fetch`, async () => {
    const { api, calls } = await setup(() => response({}));
    await assert.rejects(api.request(method, `${ROOT}/git/refs`, { ref: "refs/tags/fixture" }));
    assert.deepEqual(calls, []);
  });
}

test("production readOnly permits GET and returns decoded JSON", async () => {
  const { api, calls } = await setup(() => response(repo));
  assert.deepEqual(await api.request("GET", ROOT), repo);
  assert.equal(calls.length, 1);
});

for (const status of [404, 403, 422]) {
  test(`production request preserves numeric HTTP ${status} without leaking response or token`, async () => {
    const bodySentinel = "private-response-body-never-log";
    const { api } = await setup(() => response({ message: `${bodySentinel} ${TOKEN}` }, { status }));
    await assert.rejects(api.request("GET", ROOT), (error) => {
      assert.equal(error.status, status);
      const visible = `${error.stack}\n${JSON.stringify(error)}\n${String(error)}`;
      assert.equal(visible.includes(TOKEN), false);
      assert.equal(visible.includes(bodySentinel), false);
      return true;
    });
  });
}

for (const status of [301, 302, 307, 308]) {
  test(`production request refuses redirect status ${status}`, async () => {
    const { api, calls } = await setup(() => response({ location: "private redirect" }, { status }));
    await assert.rejects(api.request("GET", ROOT));
    assert.equal(calls.length, 1);
  });
}

test("production request refuses an already-redirected fetch response", async () => {
  const { api } = await setup(() => response(repo, { redirected: true, url: `https://untrusted.invalid${ROOT}` }));
  await assert.rejects(api.request("GET", ROOT));
});

for (const [name, result] of [
  ["malformed JSON", () => new Response("{truncated", { headers: { "content-type": "application/json" } })],
  ["empty successful body", () => new Response("", { status: 200 })],
  ["partial content", () => response(repo, { status: 206 })],
  ["null JSON", () => response(null)],
]) {
  test(`production request refuses incomplete response: ${name}`, async () => {
    const { api } = await setup(result);
    await assert.rejects(api.request("GET", ROOT));
  });
}
