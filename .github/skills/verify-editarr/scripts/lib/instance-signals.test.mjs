import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { testPortRange } from "./test-ports.mjs";

const scripts = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const root = path.resolve(scripts, "../../../..");
const PORTS = testPortRange(import.meta.url);
const instance = pathToFileURL(path.join(scripts, "lib/instance.mjs")).href;

const JOURNEY = `
import { cleanup, createRun, launch, launchBrowser, ready, sleep, stopOnSignals } from ${JSON.stringify(instance)};
const run = createRun(${JSON.stringify(root)}, { portRange: ${JSON.stringify(PORTS)}, evidenceDirectory: process.argv[2] });
stopOnSignals(run);
try {
  await launch(run);
  await ready(run);
  const page = await (await launchBrowser(run)).newPage();
  await page.goto(run.manifest.origin);
  console.log(JSON.stringify({ group: run.manifest.processGroup, evidence: run.evidence }));
  for (;;) { await page.goto(run.manifest.origin, { timeout: 5000 }); await sleep(200); }
} catch (error) {
  run.write("failure.json", { message: error.message });
} finally {
  await cleanup(run);
}
const late = createRun(${JSON.stringify(root)}, { portRange: ${JSON.stringify(PORTS)}, evidenceDirectory: process.argv[2] });
stopOnSignals(late);
try { await launch(late); late.write("late.json", { launched: true }); }
catch (error) { late.write("late.json", { launched: false, message: error.message }); }
finally { await cleanup(late); }
process.exitCode = 1;
`;

function descendants(pid) {
  const rows = spawnSync("ps", ["-axo", "pid=,ppid=,command="], { encoding: "utf8" }).stdout.trim().split("\n")
    .map(line => line.trim().split(/\s+/)).map(([child, parent, ...command]) => ({ pid: Number(child), ppid: Number(parent), command: command.join(" ") }));
  const found = [];
  for (let frontier = [pid]; frontier.length;) {
    const next = rows.filter(row => frontier.includes(row.ppid));
    found.push(...next);
    frontier = next.map(row => row.pid);
  }
  return found;
}

const alive = pid => { try { process.kill(pid, 0); return true; } catch (error) { return error.code === "EPERM"; } };
const readJson = (directory, name) => JSON.parse(fs.readFileSync(path.join(directory, name), "utf8"));

for (const signal of ["SIGINT", "SIGTERM"]) {
  test(`${signal} during a browser journey runs cleanup and leaves no owned process`, { timeout: 180_000 }, async () => {
    const evidence = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-signal-"));
    const script = path.join(evidence, "journey.mjs");
    fs.writeFileSync(script, JOURNEY);
    const child = spawn(process.execPath, [script, evidence], { stdio: ["ignore", "pipe", "inherit"] });
    const exited = new Promise(resolve => child.once("exit", resolve));
    try {
      const line = await new Promise((resolve, reject) => {
        child.stdout.once("data", resolve);
        exited.then(code => {
          const failures = fs.readdirSync(evidence, { recursive: true }).filter(file => path.basename(file) === "failure.json");
          reject(new Error(`The journey exited ${code} before it was ready: ${failures.map(file => readJson(evidence, file).message).join("; ")}`));
        });
      });
      const { group, evidence: runEvidence } = JSON.parse(line.toString());
      const owned = descendants(child.pid);
      assert.ok(owned.some(row => /chrom/i.test(row.command)), "Chromium runs under the journey");
      child.kill(signal);
      const code = await exited;
      assert.equal(code, 1, "the journey reports failure instead of Playwright's exit 130");
      assert.deepEqual(readJson(runEvidence, "cleanup.json"), { processesStopped: true, scratchRemoved: true, evidenceRetained: true, errors: [] });
      assert.throws(() => process.kill(-group, 0), { code: "ESRCH" });
      assert.deepEqual(owned.filter(row => alive(row.pid)).map(row => row.command), []);
      const late = fs.readdirSync(evidence).map(name => path.join(evidence, name)).find(directory => fs.existsSync(path.join(directory, "late.json")));
      assert.deepEqual(readJson(late, "late.json"), { launched: false, message: "Interrupted before launch" });
      assert.equal(readJson(late, "cleanup.json").scratchRemoved, true);
    } finally {
      if (child.exitCode === null) { child.kill("SIGKILL"); await exited; }
      fs.rmSync(evidence, { recursive: true, force: true });
    }
  });
}

test("every journey launches Chromium through launchBrowser, which leaves interrupts to stopOnSignals", () => {
  const launches = fs.readdirSync(scripts, { recursive: true })
    .filter(file => file.endsWith(".mjs") && !file.endsWith(".test.mjs") && file !== path.join("lib", "instance.mjs"))
    .filter(file => /chromium\.launch\(/.test(fs.readFileSync(path.join(scripts, file), "utf8")));
  assert.deepEqual(launches, []);
});
