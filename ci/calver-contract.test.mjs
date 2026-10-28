// Blind acceptance tests authored against actions/calver/contract.md, not implementation.
// Run: node --test ci/calver*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

const engine = () => import("../actions/calver/calver.mjs");

for (const [version, date, patch, canary] of [
  ["2026.101.0", "2026-01-01", 0, null],
  ["2026.109.12", "2026-01-09", 12, null],
  ["2026.930.10", "2026-09-30", 10, null],
  ["2026.1028.0", "2026-10-28", 0, null],
  ["2026.1231.11-canary.10", "2026-12-31", 11, 10],
  ["2024.229.0-canary.0", "2024-02-29", 0, 0],
  ["2000.229.0", "2000-02-29", 0, null],
]) {
  test(`parseVersion accepts canonical ${version}`, async () => {
    const { parseVersion } = await engine();
    assert.deepEqual(parseVersion(version), { date, patch, canary });
  });
}

for (const version of [
  "2026.229.0", "1900.229.0", "2026.431.0", "2026.1131.0", "2026.1301.0",
  "2026.100.0", "2026.001.0", "2026.0101.0", "2026.11.0", "2026.101.00",
  "2026.101.-1", "2026.101.1.0", "2026.101.1e2", "2026.101.1.5",
  "2026.101.0-canary.01", "2026.101.0-canary.-1", "2026.101.0-canary.",
  "2026.101.0-rc.1", "2026.101.0+build.1", "v2026.101.0", "26.101.0",
  "2026.101.0\n", " 2026.101.0", "2026.101.0 ", "２０２６.101.0", "", null,
  "2026.101.9007199254740992", "2026.101.0-canary.9007199254740992",
]) {
  test(`parseVersion refuses invalid/noncanonical ${JSON.stringify(version)}`, async () => {
    const { parseVersion } = await engine();
    assert.throws(() => parseVersion(version));
  });
}

for (const [tag, namespace, version, channel, canary] of [
  ["v2026.1028.2", "", "2026.1028.2", "stable", null],
  ["canary/v2026.1028.2-canary.10", "", "2026.1028.2-canary.10", "canary", 10],
  ["build/v2026.1028.2", "build", "2026.1028.2", "stable", null],
  ["build/canary/v2026.1028.2-canary.10", "build", "2026.1028.2-canary.10", "canary", 10],
]) {
  test(`parseTag recognizes exactly ${tag}`, async () => {
    const { parseTag } = await engine();
    assert.deepEqual(parseTag(tag, namespace), {
      date: "2026-10-28", patch: 2, canary, version, channel,
    });
  });
}

for (const [tag, namespace] of [
  ["v1.2.3", ""], ["v2026.10.28", ""], ["release/v2026.1028.0", ""],
  ["v2026.1028.0-canary.0", ""], ["canary/v2026.1028.0", ""],
  ["build/v2026.1028.0", ""], ["v2026.1028.0", "build"],
  ["canary/build/v2026.1028.0-canary.0", "build"],
  ["v2026.229.0", ""], ["v2026.1028.00", ""], ["v2026.1028.0\n", ""],
]) {
  test(`parseTag ignores unrelated/noncanonical ${JSON.stringify(tag)} in ${namespace || "root"}`, async () => {
    const { parseTag } = await engine();
    assert.equal(parseTag(tag, namespace), null);
  });
}

test("empty and legacy-only history allocate the first stable patch", async () => {
  const { allocateVersion } = await engine();
  for (const tags of [[], ["v1.2.3", "v2026.10.28", "some-release", "build/v2026.1028.99"]]) {
    assert.deepEqual(allocateVersion({ tags, date: "2026-10-28", channel: "stable" }), {
      version: "2026.1028.0", tag: "v2026.1028.0", date: "2026-10-28",
      channel: "stable", namespace: "",
    });
  }
});

test("allocation is numeric, date-local and namespace-local, never lexical", async () => {
  const { allocateVersion } = await engine();
  const tags = ["v2026.1028.9", "v2026.1028.10", "v2026.1027.900", "v2026.1029.80",
    "build/v2026.1028.200", "canary/v2026.1028.99-canary.999"];
  assert.equal(allocateVersion({ tags, date: "2026-10-28", channel: "stable" }).version, "2026.1028.11");
  assert.equal(allocateVersion({ tags, date: "2026-10-28", channel: "stable", namespace: "build" }).tag,
    "build/v2026.1028.201");
});

test("canary N starts at zero and increments numerically on the next stable base", async () => {
  const { allocateVersion } = await engine();
  assert.equal(allocateVersion({ tags: [], date: "2026-10-28", channel: "canary" }).tag,
    "canary/v2026.1028.0-canary.0");
  const tags = ["v2026.1028.9", "canary/v2026.1028.10-canary.9", "canary/v2026.1028.10-canary.10",
    "canary/v2026.1028.9-canary.999", "build/canary/v2026.1028.10-canary.999"];
  assert.equal(allocateVersion({ tags, date: "2026-10-28", channel: "canary" }).version,
    "2026.1028.10-canary.11");
});

test("publishing a stable base resets canaries onto the next patch", async () => {
  const { allocateVersion } = await engine();
  const tags = ["v2026.1028.0", "canary/v2026.1028.0-canary.7"];
  assert.equal(allocateVersion({ tags, date: "2026-10-28", channel: "canary" }).version,
    "2026.1028.1-canary.0");
});

for (const invalid of [
  { date: "2026-02-29" }, { date: "2026-1-01" }, { date: "2026-10-28T00:00:00Z" },
  { channel: "preview" }, { namespace: "arbitrary" },
]) {
  test(`allocateVersion refuses ${JSON.stringify(invalid)}`, async () => {
    const { allocateVersion } = await engine();
    assert.throws(() => allocateVersion({ tags: [], date: "2026-10-28", channel: "stable", ...invalid }));
  });
}

test("allocation cannot round an exhausted safe numeric counter into a collision", async () => {
  const { allocateVersion } = await engine();
  for (const [tags, channel] of [
    [["v2026.1028.9007199254740991"], "stable"],
    [["canary/v2026.1028.0-canary.9007199254740991"], "canary"],
  ]) {
    assert.throws(() => allocateVersion({ tags, date: "2026-10-28", channel }));
  }
});

test("runner policy accepts only ubuntu-latest on public repos", async () => {
  const { validateRunner } = await engine();
  assert.equal(validateRunner('"ubuntu-latest"', "public", undefined), "ubuntu-latest");
  for (const runner of [undefined, "", "ubuntu-latest", '"ubuntu-24.04"', '"ubuntu-latest-4-cores"',
    '["ubuntu-latest"]', '["self-hosted","linux"]', '"windows-latest"', '{}', 'null']) {
    assert.throws(() => validateRunner(runner, "public", '["self-hosted","linux"]'));
  }
});

test("private runner labels must match explicit configuration without paid fallback", async () => {
  const { validateRunner } = await engine();
  const labels = ["self-hosted", "linux", "release-fixture"];
  const configured = JSON.stringify(labels);
  assert.deepEqual(validateRunner(configured, "private", configured), labels);
  for (const [runner, visibility, config] of [
    [configured, "private", undefined], ['"ubuntu-latest"', "private", configured],
    ['["self-hosted","linux","other"]', "private", configured],
    ['["self-hosted","linux"]', "private", configured],
    ['["self-hosted","linux","release-fixture","extra"]', "private", configured],
    ['"ubuntu-latest"', "private", '"ubuntu-latest"'],
    [configured, "unknown", configured], [configured, undefined, configured],
  ]) {
    assert.throws(() => validateRunner(runner, visibility, config));
  }
});

test("notes group conventional changes and preserve BREAKING CHANGE information", async () => {
  const { renderNotes } = await engine();
  const notes = await renderNotes([
    { sha: "a".repeat(40), message: "feat(api): add search" },
    { sha: "b".repeat(40), message: "fix: avoid duplicate uploads" },
    { sha: "c".repeat(40), message: "feat!: replace token format\n\nBREAKING CHANGE: old tokens require reissue" },
    { sha: "d".repeat(40), message: "docs: document migration" },
  ]);
  assert.equal(typeof notes, "string");
  for (const text of ["add search", "avoid duplicate uploads", "old tokens require reissue", "document migration"]) {
    assert.ok(notes.includes(text), `notes lost ${text}`);
  }
  assert.match(notes, /break(?:ing)?/i);
  assert.match(notes, /feature|feat/i);
  assert.match(notes, /fix/i);
});

test("notes accept empty history without inventing changes", async () => {
  const { renderNotes } = await engine();
  assert.equal(typeof await renderNotes([]), "string");
});

test("notes larger than 32 MiB are preserved or fail explicitly, never silently truncated", async () => {
  const { renderNotes } = await engine();
  const marker = "FINAL-NOTE-SENTINEL-7f391";
  const message = `feat: large offline fixture ${"x".repeat(33 * 1024 * 1024)} ${marker}`;
  assert.ok(Buffer.byteLength(message) > 32 * 1024 * 1024);
  let notes;
  try {
    notes = await renderNotes([{ sha: "a".repeat(40), message }]);
  } catch (error) {
    assert.match(String(error.message), /limit|large|size|bound|bytes|length|exceed/i,
      "oversize refusal must identify the explicit history/notes bound");
    return;
  }
  assert.equal(typeof notes, "string");
  assert.ok(notes.includes(marker), "renderNotes silently dropped the end of a >32 MiB message");
  assert.ok(notes.length >= 33 * 1024 * 1024, "renderNotes silently shortened the oversized change title");
});
