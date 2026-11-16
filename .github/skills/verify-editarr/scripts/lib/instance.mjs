import assert from "node:assert/strict";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { childEnvironment, evidenceRoot, pythonPath, runLogs } from "./prerequisites.mjs";
import { createPortPool, parsePortRange } from "./ports.mjs";

export const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

/** Create one run's scratch data and its uniquely named retained evidence directory. */
export function createRun(root, { portRange, evidenceDirectory = evidenceRoot(root) } = {}) {
  const ports = createPortPool(parsePortRange(portRange));
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-"));
  const evidence = path.join(evidenceDirectory, path.basename(scratch));
  fs.mkdirSync(evidenceDirectory, { recursive: true });
  fs.mkdirSync(evidence);
  return {
    root, scratch, evidence, data: path.join(scratch, "data"), ports,
    log: fs.openSync(path.join(evidence, "server.log"), "wx", 0o600),
    secrets: new Set(), child: null, browser: null, manifest: null, interrupted: false, stopped: false,
    write(name, value) { fs.writeFileSync(path.join(evidence, name), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 }); },
  };
}

export const readInstance = evidence => JSON.parse(fs.readFileSync(path.resolve(evidence, "instance.json"), "utf8"));
const revision = root => execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();

/**
 * Send `signal` to the process group and return whether the group was there to
 * receive it. macOS reports EPERM for a group that has just exited, so EPERM
 * means gone only when `ps` no longer lists the group; otherwise it is rethrown.
 */
function signalGroup(processGroup, signal) {
  try { process.kill(-processGroup, signal); return true; }
  catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code === "EPERM") {
      const listing = spawnSync("ps", ["-axo", "pgid="], { encoding: "utf8", timeout: 5000 });
      const groups = listing.stdout?.trim();
      if (listing.status === 0 && groups && /^\d+(?:\s+\d+)*$/.test(groups) && !groups.split(/\s+/).some(group => Number(group) === processGroup)) return false;
    }
    throw error;
  }
}

const groupAlive = processGroup => signalGroup(processGroup, 0);

function stop(run, signal) {
  if (run.child?.pid && groupAlive(run.child.pid)) signalGroup(run.child.pid, signal);
}

/** Start the source development stack in its own process group on two fresh loopback ports. */
export async function launch(run, { controller = false, community = false } = {}) {
  if (run.interrupted) throw new Error("Interrupted before launch");
  const apiPort = await run.ports.next();
  const webPort = await run.ports.next();
  const python = pythonPath(run.root);
  const executable = controller ? process.execPath : python;
  const args = controller
    ? [path.join(run.root, ".github/skills/verify-editarr/scripts/lib/controller-source.mjs"), python, String(apiPort), String(webPort)]
    : ["scripts/run_development.py", "dev", "--web-host", "127.0.0.1", "--web-port", String(webPort), "--api-host", "127.0.0.1", "--api-port", String(apiPort)];
  if (community) args.push(path.join(run.root, ".github/skills/verify-editarr/scripts/lib/community-source.py"));
  const env = { ...childEnvironment(run.root, run.data), ...run.environment };
  run.child = spawn(executable, args, { cwd: run.root, env, detached: true, stdio: ["ignore", run.log, run.log] });
  await once(run.child, "spawn");
  run.manifest = {
    origin: `http://127.0.0.1:${webPort}`, apiPort, webPort, data: run.data, logs: runLogs(run.data), evidence: run.evidence, processGroup: run.child.pid,
    command: [executable, ...args], cwd: run.root, pythonImportRoot: run.root, revision: revision(run.root), controller,
  };
  run.write("instance.json", run.manifest);
}

function owner(port, processGroup) {
  const listeners = execFileSync("lsof", ["-nP", "-t", `-iTCP:${port}`, "-sTCP:LISTEN"], { encoding: "utf8" }).trim().split(/\s+/);
  assert.ok(listeners.length > 0, `No listener on ${port}`);
  for (const pid of listeners) {
    const group = Number(execFileSync("ps", ["-o", "pgid=", "-p", pid], { encoding: "utf8" }).trim());
    assert.equal(group, processGroup, `Port ${port} is not owned by this run`);
  }
  return listeners.map(Number);
}

async function status(origin) {
  const response = await fetch(`${origin}/api/auth/status`, { signal: AbortSignal.timeout(2000), redirect: "error" });
  assert.equal(response.status, 200);
  return response.json();
}

/** Absolute paths of every log file any process in the group holds open. */
function openLogFiles(processGroup) {
  const pids = spawnSync("pgrep", ["-g", String(processGroup)], { encoding: "utf8" }).stdout.trim().split(/\s+/).filter(Boolean);
  assert.ok(pids.length > 0, `No processes in group ${processGroup}`);
  const listing = spawnSync("lsof", ["-nP", "-p", pids.join(","), "-Fn"], { encoding: "utf8" }).stdout;
  return [...new Set(listing.split("\n").filter(line => /^n\/.*\.(?:log|jsonl)(?:\.\d+)?$/.test(line)).map(line => line.slice(1)))].sort();
}

/** Wait until the instance serves Vite and reports `state` (the fresh setup gate by default). */
export async function ready(run, state = "setup_required") {
  const deadline = Date.now() + 90000;
  let lastError;
  while (Date.now() < deadline && !run.interrupted) {
    assert.equal(run.child.exitCode, null, "Development launcher exited before readiness");
    try {
      assert.equal((await status(run.manifest.origin)).state, state);
      const response = await fetch(run.manifest.origin, { signal: AbortSignal.timeout(2000), redirect: "error" });
      assert.equal(response.status, 200);
      assert.match(await response.text(), /(?:@vite\/client|src\/main)/);
      return;
    } catch (error) { lastError = error; }
    await sleep(200);
  }
  throw new Error(`Instance did not reach the ${state} state: ${lastError?.message ?? "interrupted"}`);
}

/**
 * Read-only doctor: the recorded group owns both loopback listeners, forms auth is
 * enforced, and every log file the group holds is inside its scratch or evidence.
 */
export async function inspect(instance, root) {
  const origin = new URL(instance.origin);
  assert.equal(origin.hostname, "127.0.0.1");
  assert.equal(origin.protocol, "http:");
  assert.equal(Number(origin.port), instance.webPort);
  assert.ok(Number.isInteger(instance.processGroup) && instance.processGroup > 1);
  assert.ok(groupAlive(instance.processGroup), `Recorded process group ${instance.processGroup} is not running`);
  const webListeners = owner(instance.webPort, instance.processGroup);
  const apiListeners = owner(instance.apiPort, instance.processGroup);
  const owned = [path.dirname(instance.data), instance.evidence].filter(Boolean).map(directory => fs.realpathSync(directory) + path.sep);
  const logFiles = openLogFiles(instance.processGroup);
  const foreignLogs = logFiles.filter(file => !owned.some(prefix => file.startsWith(prefix)));
  assert.deepEqual(foreignLogs, [], "Run holds log files outside its scratch and evidence");
  const authentication = await status(instance.origin);
  assert.ok(["setup_required", "unauthenticated"].includes(authentication.state));
  assert.equal(authentication.access ?? null, null, "A signed-out status carries capabilities");
  assert.equal(typeof authentication.sign_in?.jellyfin, "boolean");
  assert.ok(!("registration" in authentication.sign_in), "The status still offers the removed registration");
  assert.ok(!("configuration" in authentication), "The status still carries the removed bypass configuration");
  const current = revision(root);
  if (instance.revision) assert.equal(current, instance.revision, "Checkout revision changed since launch");
  return {
    origin: instance.origin, data: instance.data, processGroup: instance.processGroup,
    webListeners, apiListeners, logFiles, authentication: authentication.state, revision: current,
    dirty: execFileSync("git", ["status", "--porcelain"], { cwd: root, encoding: "utf8" }).length > 0,
  };
}

/** Report evidence files containing any run secret. PNG files are pixels and are counted separately. */
export function scanEvidence(evidence, secrets) {
  const values = [...secrets].filter(value => value.length >= 8);
  const findings = [];
  let textFiles = 0;
  let imageFiles = 0;
  for (const name of fs.readdirSync(evidence)) {
    if (name.endsWith(".png")) { imageFiles++; continue; }
    textFiles++;
    const text = fs.readFileSync(path.join(evidence, name), "utf8");
    const leaked = values.filter(value => text.includes(value)).length;
    if (leaked) findings.push({ file: name, secrets: leaked });
  }
  return { secretsChecked: values.length, textFiles, imageFiles, findings };
}

/** Signal only this run's group, escalating to SIGKILL. Returns whether the group is gone. */
async function stopGroup(run) {
  const alive = () => Boolean(run.child?.pid) && groupAlive(run.child.pid);
  for (const [signal, wait] of [["SIGTERM", 8000], ["SIGKILL", 5000]]) {
    stop(run, signal);
    const deadline = Date.now() + wait;
    while (alive() && Date.now() < deadline) await sleep(100);
  }
  return !alive();
}

/**
 * Close the browser, stop only this run's group, and remove scratch once the group is
 * gone. Every step runs and both records are written before any failure is rethrown.
 * A repeat call after a successful stop does nothing.
 */
export async function cleanup(run) {
  if (run.stopped) return;
  const failures = [];
  try { await run.browser?.close(); } catch (error) { failures.push(error); }
  try {
    run.stopped = await stopGroup(run);
    if (!run.stopped) failures.push(new Error("Run-owned process group survived cleanup; scratch retained"));
  } catch (error) { failures.push(error); }
  if (run.stopped) {
    fs.closeSync(run.log);
    fs.rmSync(run.scratch, { recursive: true });
  }
  const evidenceRetained = fs.existsSync(path.join(run.evidence, "server.log"));
  if (!evidenceRetained) failures.push(new Error("Server evidence did not survive cleanup"));
  run.write("cleanup.json", {
    processesStopped: run.stopped, scratchRemoved: !fs.existsSync(run.scratch), evidenceRetained,
    errors: failures.map(error => error.message),
  });
  const redaction = scanEvidence(run.evidence, run.secrets);
  run.write("redaction.json", redaction);
  if (redaction.findings.length) failures.push(new Error("Run secrets appear in retained evidence"));
  if (failures.length) throw new AggregateError(failures, failures.map(error => error.message).join("; "));
}

const interruptible = new Set();
let interrupted = false;

function interrupt() {
  interrupted = true;
  for (const run of interruptible) {
    run.interrupted = true;
    stop(run, "SIGTERM");
    run.browser?.close().catch(() => {});
  }
}

/**
 * Stop the run's group and close its browser when the helper is interrupted, so the
 * journey fails fast and the caller's cleanup runs. A run registered after an
 * interrupt starts interrupted and refuses to launch.
 */
export function stopOnSignals(run) {
  if (!interruptible.size) for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, interrupt);
  interruptible.add(run);
  run.interrupted ||= interrupted;
}

/**
 * Launch the run's Chromium once. Playwright's own SIGINT and SIGTERM handlers would
 * close it and exit 130 before cleanup, so stopOnSignals owns interrupts instead.
 */
export async function launchBrowser(run) {
  const { chromium } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  run.browser ??= await chromium.launch({ handleSIGINT: false, handleSIGTERM: false });
  if (run.interrupted) throw new Error("Interrupted while launching the browser");
  return run.browser;
}
