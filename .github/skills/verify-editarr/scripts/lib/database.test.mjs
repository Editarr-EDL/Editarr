import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { query } from "./database.mjs";

const HOLD_MS = 1500;

function seededRun() {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-db-"));
  const db = new DatabaseSync(path.join(data, "scanner.db"));
  db.exec("PRAGMA journal_mode = DELETE; CREATE TABLE t (value TEXT); INSERT INTO t VALUES ('before');");
  db.close();
  return { data };
}

/** A separate process holds an exclusive write lock, as the app's commit does, then commits. */
async function exclusiveWriter(run) {
  const source = `
    const { DatabaseSync } = require("node:sqlite");
    const db = new DatabaseSync(${JSON.stringify(path.join(run.data, "scanner.db"))});
    db.exec("BEGIN EXCLUSIVE; UPDATE t SET value = 'after';");
    console.log("locked");
    setTimeout(() => { db.exec("COMMIT"); db.close(); }, ${HOLD_MS});
  `;
  const child = spawn(process.execPath, ["--no-warnings", "-e", source], { stdio: ["ignore", "pipe", "inherit"] });
  const [chunk] = await once(child.stdout, "data");
  assert.equal(chunk.toString().trim(), "locked");
  return child;
}

test("a read during a writer's exclusive lock waits for the commit instead of failing", async () => {
  const run = seededRun();
  let writer;
  try {
    writer = await exclusiveWriter(run);
    const legacy = new DatabaseSync(path.join(run.data, "scanner.db"), { readOnly: true });
    try { assert.throws(() => legacy.prepare("SELECT value FROM t").all(), /database is locked/); }
    finally { legacy.close(); }
    const started = Date.now();
    assert.deepEqual(query(run, "SELECT value FROM t"), [{ value: "after" }]);
    assert.ok(Date.now() - started >= HOLD_MS / 2, "the read waited for the writer");
  } finally {
    if (writer && writer.exitCode === null) await once(writer, "exit");
    fs.rmSync(run.data, { recursive: true });
  }
  assert.equal(writer.exitCode, 0, "the writer committed");
});

test("reads return plain rows, bind values, and refuse writes", () => {
  const run = seededRun();
  try {
    const [row] = query(run, "SELECT value FROM t WHERE value = ?", "before");
    assert.equal(Object.getPrototypeOf(row), Object.prototype);
    assert.deepEqual(row, { value: "before" });
    assert.throws(() => query(run, "INSERT INTO t VALUES ('x')"), /readonly/);
    assert.deepEqual(query(run, "SELECT count(*) AS n FROM t"), [{ n: 1 }]);
  } finally { fs.rmSync(run.data, { recursive: true }); }
});
