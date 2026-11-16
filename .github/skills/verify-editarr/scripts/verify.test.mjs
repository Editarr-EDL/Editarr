import assert from "node:assert/strict";
import childProcess, { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire, syncBuiltinESMExports } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { authenticate, INJECTED_FAILURE } from "./lib/auth-journey.mjs";
import { capture } from "./lib/capture.mjs";
import { synthesizeMedia } from "./lib/fixtures.mjs";
import { cleanup, createRun, inspect, launch, ready, scanEvidence } from "./lib/instance.mjs";
import { pairViolations } from "./lib/pair.mjs";
import { childEnvironment, missingPrerequisites, MODE_REQUIREMENTS } from "./lib/prerequisites.mjs";
import { createPortPool, parsePortRange } from "./lib/ports.mjs";
import { runReviewRepairs } from "./lib/repair-journey.mjs";
import { testPortRange } from "./lib/test-ports.mjs";
import { captureScanState } from "./lib/scan-access-probes.mjs";

const PORTS = testPortRange(import.meta.url);
const script = fileURLToPath(new URL("./verify.mjs", import.meta.url));
const root = path.resolve(path.dirname(script), "../../../..");
const helper = (args, env = process.env) => spawnSync(process.execPath, [script, ...args, ...(["auth", "probe", "serve"].includes(args[0]) && PORTS && !args.some(arg => arg.startsWith("--ports=")) ? [`--ports=${PORTS}`] : [])], { cwd: os.tmpdir(), env, encoding: "utf8", timeout: 240000 });
const evidenceOf = output => output.match(/^Evidence: (.+)$/m)?.[1];
const readJson = (directory, name) => JSON.parse(fs.readFileSync(path.join(directory, name), "utf8"));

test("scan failure diagnostics retain lost queue states without changing the scratch database", () => {
  const run = createRun(root);
  try {
    fs.mkdirSync(run.data);
    run.manifest = { data: run.data };
    const database = path.join(run.data, "scanner.db");
    execFileSync(path.join(root, ".venv/bin/python"), ["-I", "-B", "-c", `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    connection.executescript("""
CREATE TABLE scans (id TEXT, name TEXT, status TEXT, progress_percent REAL,
 progress_stage TEXT, progress_message TEXT, error_message TEXT, worker_pid INTEGER,
 updated_at TEXT, worker_token TEXT);
CREATE TABLE scan_queue (id INTEGER, scan_id TEXT);
INSERT INTO scans VALUES ('lost', 'Lost', 'orphaned', 100, 'orphaned', 'Stopped', NULL, NULL, 'now', 'private-token');
INSERT INTO scans VALUES ('running', 'Running', 'scanning', 25, 'audio', 'Scanning', NULL, 42, 'now', 'private-token');
INSERT INTO scans VALUES ('waiting', 'Waiting', 'queued', 0, 'queued', 'Queued', NULL, NULL, 'now', 'private-token');
INSERT INTO scan_queue VALUES (7, 'running');
""")
`, database], { env: childEnvironment(root, run.data) });
    const before = fs.readFileSync(database);
    captureScanState(run);
    const state = readJson(run.evidence, "scan-ui-state.json");
    assert.deepEqual(state.scans.map(scan => [scan.id, scan.status, scan.queue_id]), [
      ["lost", "orphaned", null], ["running", "scanning", 7], ["waiting", "queued", null],
    ]);
    assert.ok(!JSON.stringify(state).includes("private-token"));
    assert.deepEqual(fs.readFileSync(database), before);
    fs.unlinkSync(database);
    captureScanState(run);
    assert.match(readJson(run.evidence, "scan-ui-state.json").error, /unable to open database/);
    assert.ok(!fs.existsSync(database));
    run.manifest.data = run.scratch;
    captureScanState(run);
    assert.match(readJson(run.evidence, "scan-ui-state.json").error, /must use this run's scratch data/);
  } finally {
    fs.closeSync(run.log);
    fs.rmSync(run.scratch, { recursive: true });
    fs.rmSync(run.evidence, { recursive: true });
  }
});

function observedPair() {
  const run = (index, port) => ({
    evidence: `/evidence/${index}`, instance: { data: `/scratch/${index}/data`, processGroup: 100 + index, origin: `http://127.0.0.1:${port}`, apiPort: port + 1, webPort: port },
    result: { passed: true }, cleanup: { processesStopped: true, scratchRemoved: true, evidenceRetained: true },
    redaction: { findings: [] }, contexts: [{ initialCookies: 0 }, { initialCookies: 0 }],
  });
  return {
    exitCodes: [0, 0], runs: [run(0, 6000), run(1, 7000)],
    concurrent: [{ webListeners: [1], apiListeners: [2] }, { webListeners: [3], apiListeners: [4] }], afterCleanup: ["stopped", "stopped"],
  };
}

test("a missing prerequisite blocks before launch and records the exact gap", () => {
  const result = helper(["probe"], { ...process.env, PATH: "/usr/bin:/bin" });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /BLOCKED pnpm: pnpm is not on PATH\. Remedy \(not run\)/);
  const evidence = evidenceOf(result.stderr);
  assert.deepEqual(fs.readdirSync(evidence), ["blocked.json"]);
  const blocked = readJson(evidence, "blocked.json");
  assert.equal(blocked.status, "BLOCKED");
  assert.equal(blocked.launched, false);
  assert.ok(blocked.missing.some(gap => gap.requirement === "pnpm"));
  fs.rmSync(evidence, { recursive: true });
});

test("auth needs Docker and the pinned Jellyfin image for setup; serve and probe do not", () => {
  assert.ok(MODE_REQUIREMENTS.auth.includes("jellyfinImage"));
  assert.ok(!MODE_REQUIREMENTS.serve.includes("jellyfinImage") && !MODE_REQUIREMENTS.probe.includes("jellyfinImage"));
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-path-"));
  const saved = process.env.PATH;
  try {
    for (const tool of ["pnpm", "lsof", "ps"]) fs.symlinkSync(execFileSync("/usr/bin/which", [tool], { encoding: "utf8" }).trim(), path.join(bin, tool));
    process.env.PATH = bin;
    const gaps = missingPrerequisites("auth", root);
    assert.deepEqual(gaps.filter(gap => gap.requirement !== "chromium").map(gap => gap.requirement), ["jellyfinImage"]);
    assert.match(gaps.find(gap => gap.requirement === "jellyfinImage").detail, /docker cannot inspect jellyfin\/jellyfin@sha256:/);
    assert.deepEqual(missingPrerequisites("serve", root), []);
  } finally {
    process.env.PATH = saved;
    fs.rmSync(bin, { recursive: true });
  }
});

test("a community run blocks before launch when the service validators cannot be bundled", () => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-hide-esbuild-"));
  const preload = path.join(scratch, "hide-esbuild.mjs");
  fs.writeFileSync(preload, `import Module from "node:module";
if (process.argv[1]?.endsWith("community-contract.mjs")) {
  const resolve = Module._resolveFilename;
  Module._resolveFilename = function (request, ...rest) {
    if (request === "esbuild") throw Object.assign(new Error("Cannot find module 'esbuild' (simulated)"), { code: "MODULE_NOT_FOUND" });
    return resolve.call(this, request, ...rest);
  };
}
`);
  try {
    const result = helper(["auth", "--community"], { ...process.env, NODE_OPTIONS: `--import=${pathToFileURL(preload).href}` });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /BLOCKED communityContract: cannot bundle the community service validators: Cannot find module 'esbuild' \(simulated\)\. Remedy \(not run\): `pnpm --dir community-service install/);
    const evidence = evidenceOf(result.stderr);
    assert.deepEqual(fs.readdirSync(evidence), ["blocked.json"]);
    const blocked = readJson(evidence, "blocked.json");
    assert.equal(blocked.launched, false);
    assert.ok(blocked.missing.some(gap => gap.requirement === "communityContract"));
    fs.rmSync(evidence, { recursive: true });
  } finally { fs.rmSync(scratch, { recursive: true, force: true }); }
});

test("child processes resolve data, logs, and the native model cache inside the run scratch", () => {
  const scratch = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-env-")));
  const previous = process.env.HF_HOME;
  process.env.HF_HOME = "/host/hf";
  try {
    const env = childEnvironment(root, path.join(scratch, "data"));
    assert.equal(env.HF_HOME, undefined);
    const code = "import json; from scanner.runtime.runtime_paths import resolve_runtime_paths as r; p = r(); print(json.dumps([str(p.data_dir), str(p.log_dir), str(p.model_cache)]))";
    const roots = JSON.parse(execFileSync(path.join(root, ".venv/bin/python"), ["-P", "-c", code], { cwd: root, env, encoding: "utf8" }));
    for (const value of roots) assert.ok(value.startsWith(`${scratch}/`), value);
  } finally {
    if (previous === undefined) delete process.env.HF_HOME; else process.env.HF_HOME = previous;
    fs.rmSync(scratch, { recursive: true });
  }
});

test("unknown flags are refused with the usage exit code", () => {
  for (const args of [["probe", "--fixed-clock"], ["auth", "--multi-user-verification"], ["serve", "--multi-user-verification"], ["auth", "--require-model-catalog"], ["auth", "--account-link"], ["auth", "--account-deletion"]]) {
    const result = helper(args);
    assert.equal(result.status, 64);
    assert.match(result.stderr, /^Usage:/);
  }
});

test("users UI accepts an explicit port range before prerequisite checks", () => {
  for (const args of [["serve", "--users"], ["auth", "--users", "--ports=9000"], ["auth", "--ports=80-90"], ["auth", "--ports=8970-8960"], ["auth", "--ports=8960-8961"], ["auth", "--ports="], ["auth", "--ports=8960-8979", "--ports=8960-8979"]]) {
    assert.equal(helper(args).status, 64, args.join(" "));
  }
  const accepted = helper(["auth", "--users", "--ports=8960-8979"], { ...process.env, PATH: "/usr/bin:/bin" });
  assert.equal(accepted.status, 2, accepted.stderr);
  assert.match(accepted.stderr, /BLOCKED pnpm/);
  fs.rmSync(evidenceOf(accepted.stderr), { recursive: true });
});

test("a port pool never reuses a selected port and rejects invalid ranges", async () => {
  assert.deepEqual(parsePortRange("8960-8979"), [8960, 8979]);
  assert.throws(() => parsePortRange("65534-65536"));
  const pool = createPortPool(parsePortRange(PORTS));
  const ports = [await pool.next(), await pool.next(), await pool.next()];
  assert.equal(new Set(ports).size, 3);
  if (PORTS) {
    const [first, last] = parsePortRange(PORTS);
    assert.ok(ports.every(port => port >= first && port <= last));
  }
});

test("upgrade accepts a bounded port pool before prerequisite checks", () => {
  const result = helper(["upgrade", "--ports=9240-9259"], { ...process.env, PATH: "/usr/bin:/bin" });
  assert.equal(result.status, 2, result.stderr);
  assert.match(result.stderr, /BLOCKED pnpm/);
  fs.rmSync(evidenceOf(result.stderr), { recursive: true });
  for (const range of ["", "9240", "9259-9240", "9240-9241"]) {
    assert.equal(helper(["upgrade", `--ports=${range}`]).status, 64);
  }
});

test("repair proof accepts only its bounded port option before prerequisites", () => {
  const result = helper(["repairs", "--ports=9240-9259"], { ...process.env, PATH: "/usr/bin:/bin" });
  assert.equal(result.status, 2, result.stderr);
  assert.match(result.stderr, /BLOCKED pnpm/);
  fs.rmSync(evidenceOf(result.stderr), { recursive: true });
  assert.equal(helper(["repairs", "--controller"]).status, 64);
});

test("restore proof accepts only its bounded port option before prerequisites", () => {
  const result = helper(["restore", "--ports=9240-9259"], { ...process.env, PATH: "/usr/bin:/bin" });
  assert.equal(result.status, 2, result.stderr);
  assert.match(result.stderr, /BLOCKED pnpm/);
  fs.rmSync(evidenceOf(result.stderr), { recursive: true });
  assert.equal(helper(["restore", "--multi-user-verification"]).status, 64);
});

test("every journey keeps its evidence under VERIFY_EVIDENCE_ROOT", () => {
  const relative = path.relative(root, fs.mkdtempSync(path.join(root, "web/test-results/verify-root-")));
  try {
    for (const mode of ["restore", "repairs", "auth"]) {
      const result = helper([mode, "--ports=9240-9259"], { ...process.env, PATH: "/usr/bin:/bin", VERIFY_EVIDENCE_ROOT: relative });
      assert.equal(result.status, 2, result.stderr);
      assert.ok(evidenceOf(result.stderr).startsWith(path.join(root, relative) + path.sep), result.stderr);
    }
  } finally { fs.rmSync(path.join(root, relative), { recursive: true, force: true }); }
});

test("repair reruns retain independent evidence under the default evidence root", async () => {
  const checkout = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-repair-evidence-"));
  try {
    assert.equal(await runReviewRepairs(checkout, "9240-9259"), 1);
    const directory = path.join(checkout, "web/test-results/verify-editarr");
    assert.ok(fs.existsSync(directory), "repairs must use the standard evidence root");
    const first = fs.readdirSync(directory);
    assert.equal(first.length, 1);
    const retained = new Map(fs.readdirSync(path.join(directory, first[0]), { recursive: true }).filter(
      name => fs.statSync(path.join(directory, first[0], name)).isFile(),
    ).map(name => [
      name, fs.readFileSync(path.join(directory, first[0], name)),
    ]));
    assert.equal(await runReviewRepairs(checkout, "9240-9259"), 1);
    assert.equal(fs.readdirSync(directory).length, 2);
    for (const [name, bytes] of retained) {
      assert.deepEqual(fs.readFileSync(path.join(directory, first[0], name)), bytes);
    }
    assert.ok(!fs.existsSync(path.join(checkout, "web/test-results/mu-b2r-proof")));
  } finally { fs.rmSync(checkout, { recursive: true, force: true }); }
});

test("pair violations name every shared resource, unproven stop, and incomplete cleanup", () => {
  assert.deepEqual(pairViolations(observedPair()), []);
  const shared = observedPair();
  shared.runs[1].instance.data = shared.runs[0].instance.data;
  shared.runs[1].instance.webPort = shared.runs[0].instance.webPort;
  shared.runs[0].cleanup.scratchRemoved = false;
  shared.runs[1].contexts[1].initialCookies = 1;
  shared.afterCleanup[1] = "alive";
  assert.deepEqual(pairViolations(shared), [
    "run 0 cleanup incomplete", "run 1 browser contexts were not fresh",
    "data directory shared between runs", "port shared between runs", "run 1 still answers doctor after cleanup",
  ]);
  const cases = [
    [observed => { observed.runs[1].evidence = observed.runs[0].evidence; }, "evidence directory shared between runs"],
    [observed => { observed.runs[1].instance.processGroup = observed.runs[0].instance.processGroup; }, "process group shared between runs"],
    [observed => { observed.runs[1].instance.origin = observed.runs[0].instance.origin; }, "origin shared between runs"],
    [observed => { observed.concurrent[1].apiListeners = [1]; }, "listener shared between runs"],
    [observed => { observed.afterCleanup[0] = "lsof failed"; }, "run 0 stop not proven after cleanup: lsof failed"],
  ];
  for (const [change, violation] of cases) {
    const observed = observedPair();
    change(observed);
    assert.deepEqual(pairViolations(observed), [violation]);
  }
  const unhealthy = observedPair();
  unhealthy.concurrent[1] = "Recorded process group 101 is not running";
  assert.deepEqual(pairViolations(unhealthy), ["peers were not all healthy at the same time"]);
});

test("evidence scan finds a planted secret in text and ignores screenshots", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-scan-"));
  try {
    const secret = "correct-horse-battery-staple";
    fs.writeFileSync(path.join(directory, "actions.json"), "{}");
    fs.writeFileSync(path.join(directory, "page.png"), secret);
    assert.deepEqual(scanEvidence(directory, new Set([secret])).findings, []);
    fs.writeFileSync(path.join(directory, "server.log"), `token=${secret}`);
    assert.deepEqual(scanEvidence(directory, new Set([secret])), { secretsChecked: 1, textFiles: 2, imageFiles: 1, findings: [{ file: "server.log", secrets: 1 }] });
  } finally { fs.rmSync(directory, { recursive: true }); }
});

test("EPERM for an absent group does not turn successful cleanup into failure", async context => {
  const run = createRun(root);
  run.child = { pid: 2147483647 };
  context.mock.method(process, "kill", (pid, signal) => {
    assert.equal(pid, -run.child.pid);
    assert.equal(signal, 0);
    throw Object.assign(new Error("kill EPERM"), { code: "EPERM" });
  });
  try {
    await cleanup(run);
    assert.equal(fs.existsSync(run.scratch), false);
    assert.deepEqual(readJson(run.evidence, "cleanup.json"), {
      processesStopped: true, scratchRemoved: true, evidenceRetained: true, errors: [],
    });
  } finally {
    run.child = null;
    await cleanup(run);
    fs.rmSync(run.evidence, { recursive: true });
  }
});

test("a group that exits between the liveness probe and the stop signal counts as stopped", async context => {
  const run = createRun(root);
  run.child = { pid: 2147483647 };
  const signals = [];
  context.mock.method(process, "kill", (pid, signal) => {
    assert.equal(pid, -run.child.pid);
    signals.push(signal);
    if (signals.length === 1) return true;
    throw Object.assign(new Error("kill EPERM"), { code: "EPERM" });
  });
  try {
    await cleanup(run);
    assert.deepEqual(signals.slice(0, 2), [0, "SIGTERM"]);
    assert.deepEqual(readJson(run.evidence, "cleanup.json"), {
      processesStopped: true, scratchRemoved: true, evidenceRetained: true, errors: [],
    });
  } finally {
    run.child = null;
    await cleanup(run);
    fs.rmSync(run.evidence, { recursive: true });
  }
});

test("EPERM for a live group preserves scratch and refuses to signal it", async context => {
  const run = createRun(root);
  run.child = { pid: Number(execFileSync("ps", ["-o", "pgid=", "-p", String(process.pid)], { encoding: "utf8" }).trim()) };
  context.mock.method(process, "kill", (_pid, signal) => {
    assert.equal(signal, 0);
    throw Object.assign(new Error("kill EPERM"), { code: "EPERM" });
  });
  try {
    await assert.rejects(cleanup(run), /kill EPERM/);
    assert.equal(fs.existsSync(run.scratch), true);
    assert.equal(readJson(run.evidence, "cleanup.json").processesStopped, false);
  } finally {
    run.child = null;
    await cleanup(run);
    fs.rmSync(run.evidence, { recursive: true });
  }
});

test("EPERM with unavailable or malformed process evidence fails closed", async context => {
  for (const observation of [{ status: 1, stdout: "" }, { status: 0, stdout: "not a process group" }, { status: 0, stdout: "" }]) {
    const run = createRun(root);
    run.child = { pid: 2147483647 };
    context.mock.method(process, "kill", () => { throw Object.assign(new Error("kill EPERM"), { code: "EPERM" }); });
    context.mock.method(childProcess, "spawnSync", () => observation);
    syncBuiltinESMExports();
    try {
      await assert.rejects(cleanup(run), /kill EPERM/);
      assert.equal(fs.existsSync(run.scratch), true);
    } finally {
      context.mock.restoreAll();
      syncBuiltinESMExports();
      run.child = null;
      await cleanup(run);
      fs.rmSync(run.evidence, { recursive: true });
    }
  }
});

test("captures paint every input and keep run secrets out of ARIA text", async () => {
  const { chromium } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
  const evidence = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-capture-"));
  const secret = "synthetic-api-key-0123456789";
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 400, height: 200 } });
    await page.setContent(`<body style="margin:0;background:#fff"><label>Current API key <input readonly value="${secret}" style="position:absolute;left:20px;top:40px;width:300px;height:40px;border:0"></label><p style="margin-top:120px">shown ${secret}</p></body>`);
    await capture(page, { evidence, secrets: new Set([secret]) }, "masked");
    const aria = fs.readFileSync(path.join(evidence, "masked.aria.txt"), "utf8");
    assert.ok(!aria.includes(secret) && aria.includes("[redacted]"), aria);
    const png = fs.readFileSync(path.join(evidence, "masked.png")).toString("base64");
    await page.setContent(`<img src="data:image/png;base64,${png}">`);
    const pixels = await page.evaluate(async points => {
      const image = document.querySelector("img");
      await image.decode();
      const canvas = Object.assign(document.createElement("canvas"), { width: image.naturalWidth, height: image.naturalHeight });
      const context = canvas.getContext("2d");
      context.drawImage(image, 0, 0);
      return points.map(([x, y]) => [...context.getImageData(x, y, 1, 1).data.slice(0, 3)]);
    }, [[170, 60], [25, 45], [5, 190]]);
    assert.deepEqual(pixels, [[255, 0, 255], [255, 0, 255], [255, 255, 255]]);
  } finally {
    await browser.close();
    fs.rmSync(evidence, { recursive: true });
  }
});

test("synthetic media is byte-stable and probes as two tagged audio tracks", () => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-media-"));
  try {
    const first = synthesizeMedia(root, scratch, path.join(scratch, "data"));
    const second = synthesizeMedia(root, scratch, path.join(scratch, "data"));
    assert.equal(second.sha256, first.sha256);
    assert.equal(first.sha256, createHash("sha256").update(fs.readFileSync(first.file)).digest("hex"));
    assert.deepEqual(first.tracks.map(track => [track.language, track.title]), [["eng", "Main"], ["spa", "Commentary"]]);
    assert.ok(first.file.startsWith(path.join(scratch, "media")));
    assert.equal(fs.existsSync(path.join(scratch, "data")), false);
  } finally { fs.rmSync(scratch, { recursive: true }); }
});

test("a deliberately failed run cleans up its instance and keeps redacted evidence", () => {
  const result = helper(["auth", "--inject-failure"]);
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, new RegExp(INJECTED_FAILURE));
  const evidence = evidenceOf(result.stdout);
  assert.equal(readJson(evidence, "failure.json").message, INJECTED_FAILURE);
  assert.deepEqual(readJson(evidence, "cleanup.json"), { processesStopped: true, scratchRemoved: true, evidenceRetained: true, errors: [] });
  const redaction = readJson(evidence, "redaction.json");
  assert.ok(redaction.secretsChecked >= 1);
  assert.deepEqual(redaction.findings, []);
  for (const name of ["instance.json", "server.log", "failure.png"]) assert.ok(fs.existsSync(path.join(evidence, name)), name);
  assert.equal(fs.existsSync(readJson(evidence, "instance.json").data), false);
  const doctor = helper(["doctor", evidence]);
  assert.equal(doctor.status, 1);
  assert.match(doctor.stderr, /FAIL doctor: Recorded process group \d+ is not running/);
});

test("a rejected browser close still stops the real instance and records cleanup", async () => {
  const run = createRun(root, { portRange: PORTS });
  try {
    await launch(run);
    await ready(run);
    const { processGroup, apiPort, webPort } = run.manifest;
    const logs = `${fs.realpathSync(run.manifest.logs)}${path.sep}`;
    const { logFiles } = await inspect(run.manifest, root);
    assert.ok(logFiles.some(file => file.startsWith(logs)), "runtime logs open in the run's own log directory");
    const elsewhere = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-other-"));
    try {
      await assert.rejects(inspect({ ...run.manifest, data: path.join(elsewhere, "data"), evidence: elsewhere }, root), { message: /log files outside its scratch/ });
    } finally { fs.rmSync(elsewhere, { recursive: true }); }
    run.browser = { close: async () => { throw new Error("Injected browser.close rejection"); } };
    await assert.rejects(cleanup(run), { message: "Injected browser.close rejection" });
    assert.throws(() => process.kill(-processGroup, 0), { code: "ESRCH" });
    for (const port of [apiPort, webPort]) {
      assert.equal(spawnSync("lsof", ["-nP", "-t", `-iTCP:${port}`, "-sTCP:LISTEN"], { encoding: "utf8" }).stdout, "", `listener on ${port}`);
    }
    assert.equal(fs.existsSync(run.scratch), false);
    assert.deepEqual(readJson(run.evidence, "cleanup.json"), {
      processesStopped: true, scratchRemoved: true, evidenceRetained: true, errors: ["Injected browser.close rejection"],
    });
    assert.deepEqual(readJson(run.evidence, "redaction.json").findings, []);
  } finally {
    run.browser = null;
    await cleanup(run);
  }
});

test("the standalone controller cancels and removes models only in its scratch cache", async () => {
  const run = createRun(root, { portRange: PORTS });
  let context;
  try {
    await launch(run, { controller: true });
    await ready(run);
    const account = await authenticate(run);
    context = await run.browser.newContext();
    const origin = run.manifest.origin;
    const login = await context.request.post(`${origin}/api/auth/jellyfin/login`, { headers: { Origin: origin }, data: { username: account.username, password: account.password } });
    assert.equal(login.status(), 200);
    const csrf = (await login.json()).csrf_token;
    run.secrets.add(csrf);
    const headers = { Origin: origin, "X-CSRF-Token": csrf };
    const cache = process.platform === "darwin"
      ? path.join(run.scratch, "home/Library/Caches/Editarr/models")
      : path.join(run.scratch, "cache/editarr/models");
    const cancellation = path.join(cache, ".cancel/faster-whisper-base");
    const artifact = path.join(cache, "artifacts/faster-whisper-base");
    assert.equal(fs.existsSync(cancellation), false);
    assert.equal(fs.existsSync(artifact), false);
    assert.equal((await context.request.post(`${origin}/api/models/faster-whisper-base/cancel`, { headers })).status(), 200);
    assert.ok(fs.statSync(cancellation).isFile());
    for (let attempt = 0; attempt < 2; attempt++) {
      const response = await context.request.delete(`${origin}/api/models/faster-whisper-base`, { headers });
      assert.equal(response.status(), 200);
      assert.equal((await response.json()).removed, false);
      assert.equal(fs.existsSync(cancellation), false);
      assert.equal(fs.existsSync(artifact), false);
    }
    run.write("controller-cache.json", { scratchCache: cache, cancellationCreated: true, cancellationCleared: true, repeatedRemoved: false });
  } finally {
    for (const cookie of await context?.cookies().catch(() => []) ?? []) run.secrets.add(cookie.value);
    await context?.close();
    try { await cleanup(run); } finally { await run.stopJellyfin?.(); }
  }
});
