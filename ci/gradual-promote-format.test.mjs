import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

// The consumer checks byte-identical mirrors with Biome 2.6.2. Keep this
// development-only tool isolated from the dependency-free published action.
const biome =
  process.env.BIOME_BIN ??
  fileURLToPath(new URL("./formatting/node_modules/@biomejs/biome/bin/biome", import.meta.url));

test("published pre-promotion verifier matches consumer Biome formatting", () => {
  const source = readFileSync(
    new URL("../actions/gradual-promote/pre-promotion-check.mjs", import.meta.url),
    "utf8",
  );
  const formatted = execFileSync(
    biome,
    [
      "format",
      "--indent-style=space",
      "--indent-width=2",
      "--line-width=100",
      "--stdin-file-path=pre-promotion-check.mjs",
    ],
    { input: source, encoding: "utf8", timeout: 10_000 },
  );
  assert.equal(source, formatted, "format the producer, never edit an immutable consumer mirror");
});
