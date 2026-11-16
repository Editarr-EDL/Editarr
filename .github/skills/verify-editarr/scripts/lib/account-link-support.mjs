import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { once } from "node:events";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { query } from "./database.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";

export const digest = content => createHash("sha256").update(content).digest("hex");

export const exportsOf = (run, userId) => query(run, "SELECT id, format, target_export_id, destination_path, content_bytes, publication_revision FROM media_item_exports WHERE scope = 'personal' AND target_user_id = ? ORDER BY id", userId);
export const identityOf = (run, userId) => query(run, "SELECT server_id, provider_user_id, link_state FROM user_provider_identities WHERE user_id = ?", userId)[0] ?? null;

export async function startProvider(run, state = "baseline") {
  const child = spawn(pythonPath(run.root), ["-B", path.join(run.root, ".github/skills/verify-editarr/scripts/account_link_provider.py"), state], {
    cwd: run.root, env: childEnvironment(run.root, run.data), stdio: ["pipe", "pipe", "inherit"],
  });
  const [line] = await Promise.race([
    once(readline.createInterface({ input: child.stdout }), "line"),
    once(child, "exit").then(([code]) => { throw new Error(`Synthetic Jellyfin exited ${code}`); }),
  ]);
  const provider = JSON.parse(line);
  run.secrets.add(provider.apiKey);
  for (const user of Object.values(provider.users)) run.secrets.add(user.password);
  return { ...provider, child };
}

/** New users on the run's Jellyfin, shaped like `startProvider`'s provider, for link proofs. */
export async function runJellyfinUsers(run, ...names) {
  const users = {};
  for (const name of names) {
    const password = randomBytes(18).toString("base64url");
    run.secrets.add(password);
    const created = await run.jellyfin.call("POST", "/Users/New", { Name: name, Password: password });
    users[name] = { userId: created.Id.replaceAll("-", "").toLowerCase(), password };
  }
  return { url: run.jellyfin.base, serverId: run.jellyfin.serverId, users, child: null };
}

export async function stopProvider(provider) {
  if (!provider || provider.child.exitCode !== null) return provider?.child.exitCode ?? null;
  provider.child.stdin.end();
  const timer = setTimeout(() => provider.child.kill("SIGTERM"), 5000);
  const [code] = await once(provider.child, "exit");
  clearTimeout(timer);
  return code;
}

/** The old files of `before` still hold their bytes and nothing was written under `newId`. */
export function assertUnmoved(run, userId, before, newId) {
  const now = exportsOf(run, userId);
  assert.deepEqual(now.map(row => [row.id, row.target_export_id, row.destination_path, row.publication_revision]), before.map(row => [row.id, row.target_export_id, row.destination_path, row.publication_revision]));
  for (const row of before.filter(entry => entry.content_bytes)) {
    assert.deepEqual(fs.readFileSync(row.destination_path), Buffer.from(row.content_bytes), "old export bytes kept");
    const moved = path.join(path.dirname(path.dirname(row.destination_path)), newId, path.basename(row.destination_path).replace(row.target_export_id, newId));
    assert.equal(fs.existsSync(moved), false, "no new export left behind");
  }
}

/**
 * Every publication in `before` now lives under `newId` with identical bytes and an
 * advanced revision. Its old file and export-ID directory are gone, except a `kept`
 * old file, which Editarr did not write and leaves in place with its directory.
 */
export function assertMoved(run, userId, before, newId, { kept = [] } = {}) {
  const files = [];
  const keptDirectories = new Set(before.filter(row => kept.includes(row.id)).map(row => path.dirname(row.destination_path)));
  for (const [index, row] of exportsOf(run, userId).entries()) {
    const old = before[index];
    assert.equal(row.id, old.id);
    assert.equal(row.target_export_id, newId);
    assert.equal(path.basename(path.dirname(row.destination_path)), newId);
    assert.equal(path.basename(row.destination_path), path.basename(old.destination_path).replace(old.target_export_id, newId));
    assert.equal(fs.existsSync(old.destination_path), kept.includes(old.id));
    if (!old.content_bytes) {
      assert.equal(fs.existsSync(row.destination_path), false);
      files.push({ id: row.id, format: row.format, record: true, newExportId: newId });
      continue;
    }
    const content = fs.readFileSync(row.destination_path);
    assert.deepEqual(content, Buffer.from(old.content_bytes));
    assert.equal(row.publication_revision, old.publication_revision + 1);
    assert.equal(fs.existsSync(path.dirname(old.destination_path)), keptDirectories.has(path.dirname(old.destination_path)));
    files.push({ id: row.id, format: row.format, sha256: digest(content), oldExportId: old.target_export_id, newExportId: newId, oldFileRemoved: !kept.includes(old.id) });
  }
  return files;
}

const oldFiles = count => `${count} old export ${count === 1 ? "file" : "files"}`;

/** The link section's old-file cleanup line for an account read's `link_cleanup` counts. */
export function cleanupText({ state = "completed", pending = 0, removed = 0, kept = 0 }) {
  return [
    state === "removing" && `Some old export files are not removed yet. ${pending} ${pending === 1 ? "is" : "are"} waiting; Editarr retries their removal at startup and every hour.`,
    removed > 0 && `${oldFiles(removed)} ${removed === 1 ? "was" : "were"} removed.`,
    kept > 0 && `${oldFiles(kept)} ${kept === 1 ? "was" : "were"} left in place because ${kept === 1 ? "it holds" : "they hold"} content Editarr did not write.`,
  ].filter(Boolean).join(" ");
}
