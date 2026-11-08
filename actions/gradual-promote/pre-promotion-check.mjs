// Run a trusted caller-owned module, never a shell expression. This is a
// freshness boundary, not a sandbox or an implementation of recovery policy.
import { execFileSync, spawn } from "node:child_process";
import { lstatSync, realpathSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";
import { assertErrorRateIdentifiers, assertRollbackVersionId, assertVersionTag, GRADUAL_STEPS } from "./gradual-promote.mjs";

export function verifierConfig(env) {
  const required = env.REQUIRE_PRE_PROMOTION_VERIFIER ?? "false";
  if (!["true", "false"].includes(required)) throw new Error("invalid verifier requirement");
  const timeout = env.PRE_PROMOTION_TIMEOUT_SECONDS ?? "120";
  if (!/^[1-9][0-9]{0,2}$/.test(timeout) || Number(timeout) > 300) {
    throw new Error("verifier timeout must be an integer from 1 to 300 seconds");
  }
  const path = env.PRE_PROMOTION_VERIFIER ?? "";
  if (!path && required === "false") return null;
  // No absolute paths, traversal, empty components, option/pathspec syntax,
  // shell metacharacters or non-module executables. Check every component.
  const parts = path.split("/");
  if (!path.endsWith(".mjs") || parts.some((p) => !/^[A-Za-z0-9_.-]+$/.test(p) || p === "." || p === ".." || p === ".git")) {
    throw new Error("verifier must be a repository-relative checked-in .mjs module");
  }
  if (!env.GITHUB_WORKSPACE || !isAbsolute(env.GITHUB_WORKSPACE)) {
    throw new Error("verifier requires an absolute GITHUB_WORKSPACE");
  }
  let file;
  let workspace;
  try {
    workspace = realpathSync(env.GITHUB_WORKSPACE);
    file = workspace;
    for (const [index, part] of parts.entries()) {
      file = join(file, part);
      const stat = lstatSync(file);
      if (stat.isSymbolicLink() || (index === parts.length - 1 ? !stat.isFile() : !stat.isDirectory())) {
        throw new Error("unsafe file type");
      }
    }
    // Git's index proves the entry is checked in. Ignore ambient GIT_* overrides
    // so a caller's unrelated worktree settings cannot select another index.
    const gitEnv = Object.fromEntries(Object.entries(env).filter(([key]) => !key.startsWith("GIT_")));
    execFileSync("git", ["-C", workspace, "ls-files", "--error-unmatch", "--", path], {
      env: gitEnv, stdio: "ignore", timeout: 5000,
    });
  } catch {
    throw new Error("verifier is unavailable, unsafe or not checked in");
  }
  assertErrorRateIdentifiers({ accountId: env.CLOUDFLARE_ACCOUNT_ID, scriptName: env.WORKER_NAME, versionId: env.NEW_VERSION });
  assertRollbackVersionId(env.PREV_VERSION);
  assertVersionTag(env.VERSION_TAG);
  const percentage = Number(env.PROMOTION_PERCENTAGE);
  if (!GRADUAL_STEPS.includes(percentage) || String(percentage) !== env.PROMOTION_PERCENTAGE) {
    throw new Error("invalid verifier promotion stage");
  }
  return {
    workspace, file, timeoutMs: Number(timeout) * 1000,
    args: ["--candidate-version-id", env.NEW_VERSION, "--candidate-tag", env.VERSION_TAG,
      "--worker-name", env.WORKER_NAME, "--account-id", env.CLOUDFLARE_ACCOUNT_ID,
      "--environment", "production", "--percentage", String(percentage),
      "--previous-version-id", env.PREV_VERSION],
  };
}

export async function runPrePromotionCheck(env, spawnImpl = spawn) {
  const config = verifierConfig(env);
  if (!config) return;
  await new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnImpl(process.execPath, ["--", config.file, ...config.args], {
        cwd: config.workspace, env, shell: false, detached: true, stdio: "ignore",
      });
    } catch {
      reject(new Error("pre-promotion verifier could not start"));
      return;
    }
    let failed = false;
    const kill = () => {
      if (child.pid) {
        try { process.kill(-child.pid, "SIGKILL"); } catch { /* group already exited */ }
      }
    };
    const abort = () => { failed = true; kill(); };
    const timer = setTimeout(abort, config.timeoutMs);
    process.once("SIGTERM", abort);
    process.once("SIGINT", abort);
    const cleanup = () => {
      clearTimeout(timer);
      process.removeListener("SIGTERM", abort);
      process.removeListener("SIGINT", abort);
      kill(); // A verifier must not leave background descendants behind.
    };
    child.once("error", () => {
      cleanup();
      reject(new Error("pre-promotion verifier could not start"));
    });
    child.once("close", (code, signal) => {
      cleanup();
      if (failed || signal || code !== 0) reject(new Error("pre-promotion verifier failed, timed out or was cancelled"));
      else resolve();
    });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runPrePromotionCheck(process.env).catch(() => {
    // Neither module output nor exception details may disclose recovery secrets.
    console.error("::error::pre-promotion verification refused; no promotion permitted");
    process.exitCode = 1;
  });
}
