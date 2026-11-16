import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { parsePortRange } from "./ports.mjs";
import { once } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { inspect, readInstance, sleep } from "./instance.mjs";
import { evidenceRoot } from "./prerequisites.mjs";

const PEERS = 2;
const readJson = file => fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : null;

/** Hold an auth run after its doctor passes until the pair coordinator has inspected every live peer. */
export async function rendezvous(barrier, run) {
  fs.writeFileSync(path.join(barrier, `${process.pid}.ready`), run.evidence);
  const deadline = Date.now() + 180000;
  while (!fs.existsSync(path.join(barrier, "go"))) {
    assert.ok(!run.interrupted, "Interrupted at the pair barrier");
    assert.ok(Date.now() < deadline, "Pair barrier timed out");
    assert.equal(run.child.exitCode, null, "Development launcher exited at the pair barrier");
    await sleep(100);
  }
}

/** Return every way two observed runs failed to stay isolated or to pass. Empty means the pair passed. */
export function pairViolations({ exitCodes, runs, concurrent, afterCleanup }) {
  const violations = [];
  const distinct = (label, values) => { if (new Set(values).size !== values.length) violations.push(`${label} shared between runs`); };
  exitCodes.forEach((code, index) => { if (code !== 0) violations.push(`run ${index} exited ${code}`); });
  runs.forEach((run, index) => {
    if (!run.result?.passed) violations.push(`run ${index} has no passing result`);
    if (!(run.cleanup?.processesStopped && run.cleanup?.scratchRemoved && run.cleanup?.evidenceRetained)) violations.push(`run ${index} cleanup incomplete`);
    if (run.redaction?.findings?.length !== 0) violations.push(`run ${index} redaction scan missing or failed`);
    if (!run.contexts?.length || run.contexts.some(context => context.initialCookies !== 0)) violations.push(`run ${index} browser contexts were not fresh`);
  });
  distinct("evidence directory", runs.map(run => run.evidence));
  distinct("data directory", runs.map(run => run.instance.data));
  distinct("process group", runs.map(run => run.instance.processGroup));
  distinct("origin", runs.map(run => run.instance.origin));
  distinct("port", runs.flatMap(run => [run.instance.apiPort, run.instance.webPort]));
  if (concurrent.length !== runs.length || concurrent.some(report => typeof report === "string")) violations.push("peers were not all healthy at the same time");
  else distinct("listener", concurrent.flatMap(report => [...report.webListeners, ...report.apiListeners]));
  afterCleanup.forEach((outcome, index) => {
    if (outcome === "alive") violations.push(`run ${index} still answers doctor after cleanup`);
    else if (outcome !== "stopped") violations.push(`run ${index} stop not proven after cleanup: ${outcome}`);
  });
  return violations;
}

async function readyPeers(barrier, children) {
  const deadline = Date.now() + 240000;
  for (;;) {
    const ready = fs.readdirSync(barrier).filter(name => name.endsWith(".ready"));
    if (ready.length === PEERS) return ready.sort().map(name => fs.readFileSync(path.join(barrier, name), "utf8"));
    const exited = children.find(child => child.exitCode !== null);
    assert.ok(!exited, `A pair run exited ${exited?.exitCode} before its peer was ready`);
    assert.ok(Date.now() < deadline, "Pair runs did not both become ready");
    await sleep(100);
  }
}

/**
 * Run two `auth` journeys at the same time and prove they share no data,
 * ports, process group, origin, or browser context. A `portRange` "A-B" is split
 * into one disjoint half per run. Returns the exit code.
 */
export async function runPair(root, script, portRange) {
  fs.mkdirSync(evidenceRoot(root), { recursive: true });
  const evidence = fs.mkdtempSync(path.join(evidenceRoot(root), "pair-"));
  const barrier = path.join(evidence, "barrier");
  fs.mkdirSync(barrier);
  const write = (name, value) => fs.writeFileSync(path.join(evidence, name), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  console.log(`Evidence: ${evidence}`);
  const timeline = { started: new Date().toISOString() };
  const range = parsePortRange(portRange);
  const span = range ? Math.floor((range[1] - range[0] + 1) / PEERS) : 0;
  const children = Array.from({ length: PEERS }, (_, index) => {
    const log = fs.openSync(path.join(evidence, `run-${index}.log`), "wx", 0o600);
    const ports = range ? [`--ports=${range[0] + index * span}-${range[0] + (index + 1) * span - 1}`] : [];
    return spawn(process.execPath, [script, "auth", `--barrier=${barrier}`, ...ports], { cwd: root, stdio: ["ignore", log, log] });
  });
  const exits = children.map(child => once(child, "exit").then(([code]) => code ?? 1));
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => children.forEach(child => child.kill("SIGTERM")));
  try {
    const peerEvidence = await readyPeers(barrier, children);
    const instances = peerEvidence.map(readInstance);
    const concurrent = await Promise.all(instances.map(instance => inspect(instance, root).catch(error => error.message)));
    timeline.peersInspected = new Date().toISOString();
    fs.writeFileSync(path.join(barrier, "go"), "");
    const exitCodes = await Promise.all(exits);
    timeline.finished = new Date().toISOString();
    const afterCleanup = await Promise.all(instances.map(instance => inspect(instance, root).then(
      () => "alive", error => /^Recorded process group \d+ is not running$/.test(error.message) ? "stopped" : error.message)));
    const runs = peerEvidence.map((directory, index) => ({
      evidence: directory, instance: instances[index],
      ...Object.fromEntries(["result", "cleanup", "redaction", "contexts"].map(name => [name, readJson(path.join(directory, `${name}.json`))])),
    }));
    const violations = pairViolations({ exitCodes, runs, concurrent, afterCleanup });
    write("result.json", {
      feature: "pair", passed: violations.length === 0, violations, timeline, exitCodes, afterCleanup, concurrentDoctor: concurrent,
      runs: runs.map(({ evidence: directory, instance, contexts }) => ({ evidence: directory, origin: instance.origin, apiPort: instance.apiPort, webPort: instance.webPort, data: instance.data, processGroup: instance.processGroup, contexts })),
    });
    console.log(violations.length ? `FAIL: ${violations.join("; ")}` : "PASS: two simultaneous runs used separate data, ports, process groups, and browser contexts.");
    return violations.length ? 1 : 0;
  } catch (error) {
    children.forEach(child => child.exitCode === null && child.kill("SIGTERM"));
    write("failure.json", { message: error.message, stack: error.stack, exitCodes: await Promise.all(exits) });
    console.error(error.message);
    return 1;
  }
}
