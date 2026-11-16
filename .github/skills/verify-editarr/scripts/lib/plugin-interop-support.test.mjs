import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test, { after } from "node:test";
import { Blocked, actors, background, cleanupPlugin, closePluginRun, exportedCues, launchPlugin, layout, mcf, pluginCheckout, pluginMatrix, withdrawalOrder } from "./plugin-interop-support.mjs";

const FAKE = `import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_ARGV"], "a") as calls:
    calls.write(json.dumps(args) + "\\n")
mode = os.environ["FAKE_MODE"]
evidence = Path(os.environ["FAKE_EVIDENCE"])
if args[0] == "launch":
    print(f"evidence: {evidence}", flush=True)
    if mode == "blocked":
        print("launch failed and was cleaned up: Blocked: docker daemon is not reachable")
        print(f"evidence: {evidence}")
        sys.exit(2)
    if mode == "hang":
        child = subprocess.Popen(["sleep", "60"])
        (evidence / "grandchild.pid").write_text(str(child.pid))
        time.sleep(60)
    scratch = evidence / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "secrets.json").write_text(json.dumps({"password": "admin-secret", "userAPassword": "a-secret", "apiKey": "key-secret"}))
    (evidence / "run.json").write_text(json.dumps({"scratch": str(scratch), "port": 9239, "commit": "c"}))
    if mode != "partial":
        (evidence / "items.json").write_text(json.dumps({"users": {"userA": "a" * 32}, "items": {}, "adminUserId": "AB-CD"}))
    print(f"evidence: {evidence}\\nport: 9239")
elif args[0] == "matrix":
    names = [case["name"] for case in json.loads(Path(args[-2]).read_text())["cases"]]
    if mode == "silent":
        print("matrix failed: URLError: refused")
        sys.exit(1)
    if mode == "empty":
        (evidence / f"matrix-{args[-1]}.json").write_text(json.dumps({"ok": False, "checks": []}))
        sys.exit(1)
    if mode == "renamed":
        names = ["other"]
    ok = mode != "mismatch"
    checks = [{"check": name, "ok": ok, "status": 200, "bodySha256": "f" * 64} for name in names]
    (evidence / f"matrix-{args[-1]}.json").write_text(json.dumps({"ok": ok, "checks": checks}))
    sys.exit(0 if ok else 1)
elif args[0] == "cleanup-label":
    print(f"cleanup-label {args[-1]}: 1 runs, clean: {mode != 'cleanup-fails'}")
    sys.exit(1 if mode == "cleanup-fails" else 0)
`;

const roots = [];
after(() => { for (const root of roots) fs.rmSync(root, { recursive: true, force: true }); });

function fixture(mode) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "plugin-interop-test-"));
  roots.push(root);
  const checkout = path.join(root, "plugin");
  const helper = path.join(checkout, ".github/skills/verify-jellyfin-edl/scripts/verify.py");
  fs.mkdirSync(path.dirname(helper), { recursive: true });
  fs.writeFileSync(helper, FAKE);
  const evidence = path.join(root, "plugin-evidence");
  fs.mkdirSync(evidence);
  const written = {};
  const run = { scratch: path.join(root, "editarr-verify-x"), evidence: path.join(root, "editarr-evidence"), secrets: new Set(), write: (name, value) => { written[name] = value; } };
  fs.mkdirSync(run.scratch);
  fs.mkdirSync(run.evidence);
  const calls = path.join(root, "argv.jsonl");
  Object.assign(process.env, { FAKE_MODE: mode, FAKE_EVIDENCE: evidence, FAKE_ARGV: calls, VERIFY_PLUGIN_CHECKOUT: checkout, VERIFY_PLUGIN_LABELS: "mu44.task=mu-44", VERIFY_PLUGIN_PORT: "9239" });
  const invocations = () => fs.existsSync(calls) ? fs.readFileSync(calls, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line)) : [];
  return { root, run, evidence, written, launched: { evidence }, plugin: pluginCheckout("/x"), invocations, argv: () => invocations().at(-1) };
}

const CLEANUP = ["cleanup-label", "--label", "editarr.verify.run=editarr-verify-x", "--label", "mu44.task=mu-44", "editarr.verify.run=editarr-verify-x"];

test("a missing plugin checkout is blocked, not a pass", () => {
  process.env.VERIFY_PLUGIN_CHECKOUT = path.join(os.tmpdir(), "absent-plugin-checkout");
  assert.throws(() => pluginCheckout("/x"), error => error instanceof Blocked && error.blocked && /BLOCKED/.test(error.message));
});

test("a launch the plugin reports as blocked stays blocked and keeps its evidence path", async () => {
  const { run, plugin, evidence } = fixture("blocked");
  await assert.rejects(launchPlugin(plugin, run, "/tree"), error => error.blocked === true && /docker daemon/.test(error.message) && error.evidence === evidence);
});

test("a launch labels the instance, keeps secrets out of argv, and registers them for redaction", async () => {
  const { run, plugin, argv } = fixture("ok");
  const launched = await launchPlugin(plugin, run, "/tree");
  assert.deepEqual(argv(), ["launch", "--label", "editarr.verify.run=editarr-verify-x", "--label", "mu44.task=mu-44", "--interop", "/tree", "--port", "9239"]);
  assert.equal(launched.users.admin, "abcd");
  assert.deepEqual([...run.secrets].sort(), ["a-secret", "admin-secret", "key-secret"]);
  assert.equal(JSON.stringify(argv()).includes("secret"), false);
});

test("a launch whose metadata cannot be read keeps its evidence and label, and cleanup reclaims it by label", async () => {
  const { run, plugin, evidence, argv } = fixture("partial");
  await assert.rejects(launchPlugin(plugin, run, "/tree"), error => error.code === "ENOENT" && error.evidence === evidence && error.label === "editarr.verify.run=editarr-verify-x");
  const cleanup = await cleanupPlugin(plugin, run);
  assert.deepEqual(argv(), CLEANUP);
  assert.equal(cleanup.code, 0);
});

test("a launch timeout kills the helper's process group and cleanup still reclaims it by label", async () => {
  const { run, plugin, evidence, argv } = fixture("hang");
  const started = Date.now();
  await assert.rejects(launchPlugin(plugin, run, "/tree", { timeout: 1500 }), error => /timed out/.test(error.message) && error.evidence === evidence);
  assert.ok(Date.now() - started < 20000, "the timeout did not wait for the helper");
  const grandchild = Number(fs.readFileSync(path.join(evidence, "grandchild.pid"), "utf8"));
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.throws(() => process.kill(grandchild, 0), { code: "ESRCH" }, "the helper's child process died with it");
  await cleanupPlugin(plugin, run);
  assert.deepEqual(argv(), CLEANUP);
});

test("a matrix mismatch fails the phase and keeps the plugin record", async () => {
  const { run, plugin, launched } = fixture("mismatch");
  await assert.rejects(pluginMatrix(plugin, run, launched, "phase", [{ name: "c" }]), /plugin matrix phase: exited 1; recorded ok is not true; failed checks/);
  assert.ok(fs.existsSync(path.join(run.evidence, "plugin-matrix-phase.json")));
});

test("a record left by an earlier invocation is refused before the helper runs", async () => {
  const { run, plugin, launched, evidence, invocations } = fixture("silent");
  fs.writeFileSync(path.join(evidence, "matrix-phase.json"), JSON.stringify({ ok: true, checks: [{ check: "old", ok: true, status: 200, bodySha256: "0" }] }));
  await assert.rejects(pluginMatrix(plugin, run, launched, "phase", [{ name: "new" }]), /already has a record/);
  assert.deepEqual(invocations(), []);
});

test("a failed helper with an empty failing record is not a pass", async () => {
  const { run, plugin, launched } = fixture("empty");
  await assert.rejects(pluginMatrix(plugin, run, launched, "empty", [{ name: "required" }]), /exited 1; recorded ok is not true; recorded cases \[\] are not the 1 submitted/);
});

test("a passing record must name exactly the submitted cases", async () => {
  const { run, plugin, launched } = fixture("renamed");
  await assert.rejects(pluginMatrix(plugin, run, launched, "phase", [{ name: "a" }]), /recorded cases \["other"\] are not the 1 submitted/);
  const ok = fixture("ok");
  assert.deepEqual((await pluginMatrix(ok.plugin, ok.run, ok.launched, "phase", [{ name: "a" }, { name: "b" }])).map(check => check.check), ["a", "b"]);
});

test("a matrix that leaves no record fails with its output, and a background poll never ends the process", async () => {
  const { run, plugin, launched } = fixture("silent");
  const poll = background(pluginMatrix(plugin, run, launched, "gone", [{ name: "c" }]));
  await new Promise(resolve => setTimeout(resolve, 300));
  await assert.rejects(poll, /plugin matrix gone: exited 1; wrote no record: matrix failed: URLError: refused/);
});

test("plugin cleanup runs even when closing the journey's clients throws", async () => {
  const { run, plugin, argv, written } = fixture("ok");
  await assert.rejects(closePluginRun(plugin, run, async () => { throw new Error("dispose failed"); }), /dispose failed/);
  assert.deepEqual(argv(), CLEANUP);
  assert.equal(written["plugin-interop-cleanup.json"].closeError, "dispose failed");
  const journeyError = new Blocked("no docker");
  await assert.rejects(closePluginRun(plugin, run, async () => {}, journeyError), error => error === journeyError);
});

test("a failed plugin cleanup fails the run and names the journey error it follows", async () => {
  const { run, plugin } = fixture("cleanup-fails");
  await assert.rejects(closePluginRun(plugin, run, async () => {}, new Error("matrix broke")), /plugin cleanup exited 1; rerun verify.py cleanup-label editarr.verify.run=editarr-verify-x; after: matrix broke/);
});

test("SIGTERM during a launch kills the helper's group and cleans by label without waiting for the launch", async () => {
  const { root, evidence, argv } = fixture("hang");
  const driver = path.join(root, "driver.mjs");
  fs.writeFileSync(driver, `import fs from "node:fs";
import { launchPlugin, pluginCheckout, withPluginRun } from ${JSON.stringify(new URL("./plugin-interop-support.mjs", import.meta.url).href)};
const root = ${JSON.stringify(root)};
const run = { scratch: root + "/editarr-verify-x", evidence: root + "/editarr-evidence", secrets: new Set(), write: (name, value) => fs.writeFileSync(root + "/" + name, JSON.stringify(value)) };
const plugin = pluginCheckout("/x");
try { await withPluginRun(plugin, run, () => launchPlugin(plugin, run, "/tree"), async () => {}); console.log("passed"); }
catch (error) { console.log("failed: " + error.message); }
`);
  const child = spawn(process.execPath, [driver], { stdio: ["ignore", "pipe", "inherit"] });
  let output = "";
  child.stdout.on("data", chunk => { output += chunk; });
  const exited = new Promise(resolve => child.on("exit", resolve));
  const pidFile = path.join(evidence, "grandchild.pid");
  for (const deadline = Date.now() + 15000; !fs.existsSync(pidFile) || !fs.readFileSync(pidFile, "utf8"); ) {
    assert.ok(Date.now() < deadline, "the fake launch never started");
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  const grandchild = Number(fs.readFileSync(pidFile, "utf8"));
  const signalled = Date.now();
  child.kill("SIGTERM");
  assert.equal(await exited, 0);
  assert.ok(Date.now() - signalled < 10000, `the journey waited ${Date.now() - signalled} ms for a 60 s launch`);
  assert.match(output, /failed: plugin launch interrupted; its process group was killed/);
  assert.throws(() => process.kill(grandchild, 0), { code: "ESRCH" }, "the helper's child process died with it");
  assert.deepEqual(argv(), CLEANUP);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "plugin-interop-cleanup.json"), "utf8")).code, 0);
});

test("after an interrupt only label cleanup starts a helper", async () => {
  const { run, plugin, launched, invocations } = fixture("ok");
  run.interrupted = true;
  await assert.rejects(pluginMatrix(plugin, run, launched, "late", [{ name: "c" }]), /plugin matrix late: exited on interrupt; wrote no record/);
  assert.deepEqual(invocations(), []);
  assert.equal((await cleanupPlugin(plugin, run)).code, 0);
  assert.deepEqual(invocations(), [CLEANUP]);
});

test("a refused API request names the request without Playwright's call log of credentials", async () => {
  const run = { root: path.resolve(new URL("../../../../..", import.meta.url).pathname), manifest: { origin: "http://127.0.0.1:9238" }, secrets: new Set() };
  const api = actors(run);
  try {
    const call = await api.actor();
    await assert.rejects(call("GET", "/api/auth/status"), error => error.message === "GET /api/auth/status: apiRequestContext.fetch: connect ECONNREFUSED 127.0.0.1:9238");
  } finally { await api.close(); }
});

const PERSONAL = "p".repeat(64);
const DEFAULT = "d".repeat(64);
const answers = (personalUntil, defaultFrom) => [
  { status: 200, bodySha256: PERSONAL, firstSentAt: 0, firstReceivedAt: 20, lastSentAt: personalUntil - 20, lastReceivedAt: personalUntil },
  { status: 200, bodySha256: DEFAULT, firstSentAt: defaultFrom - 20, firstReceivedAt: defaultFrom, lastSentAt: defaultFrom - 20, lastReceivedAt: defaultFrom },
];
const span = (state, serving, firstSentAt, lastSentAt) => ({ state, serving, firstSentAt, firstReceivedAt: firstSentAt + 20, lastSentAt, lastReceivedAt: lastSentAt + 20 });
const order = (editarr, plugin) => withdrawalOrder({ enforceSentAt: 1000, editarr, plugin, personalSha256: PERSONAL, defaultSha256: DEFAULT });

test("a withdrawal that fails before Editarr's first poll passes when the default follows the policy change", () => {
  assert.deepEqual(order([span("failed", false, 1070, 1070)], answers(1040, 1060)), []);
});

test("a withdrawal Editarr reports serving, then failed, passes when the default follows the last serving poll", () => {
  assert.deepEqual(order([span("queued", true, 1070, 1220), span("failed", false, 1300, 1300)], answers(1200, 1270)), []);
});

test("a plugin that falls back while Editarr still reports the personal file serving fails", () => {
  assert.deepEqual(order([span("queued", true, 1070, 1220), span("failed", false, 1300, 1300)], answers(1100, 1150)),
    ["the plugin served the default at 1150, before an Editarr poll that still saw the personal file serving was sent at 1220"]);
});

test("a plugin that falls back before the policy change fails even when Editarr's first poll saw the withdrawal", () => {
  assert.deepEqual(order([span("failed", false, 1070, 1070)], answers(900, 980)), ["the plugin served the default at 980, before the policy change was sent at 1000"]);
});

test("a withdrawal needs the personal file then the default, and Editarr's record of it", () => {
  assert.match(order([span("failed", false, 1070, 1070)], answers(1040, 1060).reverse())[0], /did not serve the personal file, then only the default/);
  assert.match(order([span("failed", false, 1070, 1070)], [answers(1040, 1060)[0]])[0], /did not serve the personal file, then only the default/);
  assert.match(order([span("queued", true, 1070, 1220)], answers(1200, 1270))[0], /Editarr never recorded the withdrawal/);
});

test("exported cues list EDL contributor subcategories and MCF subcategory severities in order", () => {
  const edl = "## Editarr Simple EDL\n## violence: Imported skip\n1.500 3.000 3\n## drugs: Imported skip\n4.500 6.000 3";
  assert.deepEqual(exportedCues("edl", Buffer.from(edl)), ["violence", "drugs"]);
  assert.deepEqual(exportedCues("mcf", Buffer.from(mcf("t", 1, 3).replace("drugs=low", "drugs=high"))), ["violence=low", "drugs=high"]);
  assert.deepEqual(exportedCues("mcf", Buffer.from(mcf("t", 2))), ["violence=low"]);
});

test("layout follows the export-layout contract and never names the legacy file as a selection", () => {
  const paths = layout("/m/Movies/Interop Movie/Interop Movie.mkv", "edl", "ab".repeat(16));
  assert.deepEqual(paths, {
    legacy: "/m/Movies/Interop Movie/Interop Movie.edl",
    default: "/m/Movies/Interop Movie/edl/Interop Movie.edl",
    personal: `/m/Movies/Interop Movie/edl/${"ab".repeat(16)}/Interop Movie.user-${"ab".repeat(16)}.edl`,
  });
});
