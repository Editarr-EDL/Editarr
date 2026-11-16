import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./verify.mjs", import.meta.url));
const helper = (args, env = process.env) => spawnSync(process.execPath, [script, ...args], { cwd: os.tmpdir(), env, encoding: "utf8", timeout: 240000 });
const withoutPnpm = { ...process.env, PATH: "/usr/bin:/bin" };

test("identity accepts only auth with a port range and blocks before launch", () => {
  for (const args of [["serve", "--identity"], ["probe", "--identity"], ["auth", "--identity", "--ports=9200"]]) {
    assert.equal(helper(args).status, 64, args.join(" "));
  }
  const accepted = helper(["auth", "--identity", "--ports=9200-9219"], withoutPnpm);
  assert.equal(accepted.status, 2, accepted.stderr);
  assert.match(accepted.stderr, /BLOCKED pnpm/);
  fs.rmSync(accepted.stderr.match(/^Evidence: (.+)$/m)[1], { recursive: true });
});

test("auth refuses two journeys it would not both run, before any prerequisite check", () => {
  for (const other of ["--users", "--settings", "--audit", "--audit-inject-failure", "--community", "--plugin-interop", "--reviews"]) {
    const refused = helper(["auth", "--identity", other, "--ports=9200-9219"], withoutPnpm);
    assert.equal(refused.status, 64, `--identity ${other}: ${refused.stderr}`);
    assert.match(refused.stderr, /^Usage: verify\.mjs auth/);
    assert.doesNotMatch(refused.stderr, /BLOCKED|Evidence:/);
  }
  for (const other of ["--users", "--settings", "--audit", "--audit-inject-failure", "--community", "--reviews"]) {
    const refused = helper(["auth", "--plugin-interop", other, "--ports=9200-9219"], withoutPnpm);
    assert.equal(refused.status, 64, `--plugin-interop ${other}: ${refused.stderr}`);
    assert.match(refused.stderr, /^Usage: verify\.mjs auth/);
    assert.doesNotMatch(refused.stderr, /BLOCKED|Evidence:/);
  }
  for (const pair of [["--users", "--settings"], ["--users", "--audit"], ["--community", "--settings"], ["--quick-connect", "--users"], ["--jellyfin-accounts", "--quick-connect"], ["--jellyfin-accounts", "--users"]]) {
    assert.equal(helper(["auth", ...pair, "--ports=9200-9219"], withoutPnpm).status, 64, pair.join(" "));
  }
});

test("auth keeps one journey with its launch options and the additive review journey", () => {
  for (const args of [["--identity", "--controller"], ["--identity", "--fixed-clock"], ["--settings", "--controller"], ["--audit", "--audit-inject-failure"], ["--users", "--reviews"], ["--quick-connect"], ["--jellyfin-accounts"]]) {
    const accepted = helper(["auth", ...args, "--ports=9200-9219"], withoutPnpm);
    assert.equal(accepted.status, 2, `${args.join(" ")}: ${accepted.stderr}`);
    assert.match(accepted.stderr, /BLOCKED pnpm/);
    fs.rmSync(accepted.stderr.match(/^Evidence: (.+)$/m)[1], { recursive: true });
  }
});
