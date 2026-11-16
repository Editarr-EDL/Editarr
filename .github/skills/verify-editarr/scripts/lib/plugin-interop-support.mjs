import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { SIGN_IN_PATH } from "./run-jellyfin.mjs";

export const digest = content => createHash("sha256").update(content).digest("hex");
export const fileDigest = file => digest(fs.readFileSync(file));
export const FORMATS = ["edl", "mcf"];

/** A labelled run of the plugin's verify.py whose exit 2 means a missing prerequisite. */
export class Blocked extends Error {
  constructor(message) { super(`BLOCKED: ${message}`); this.blocked = true; }
}

/** The public plugin checkout: `VERIFY_PLUGIN_CHECKOUT`, else `../jellyfin-edl-public` beside this checkout. */
export function pluginCheckout(root) {
  const checkout = path.resolve(process.env.VERIFY_PLUGIN_CHECKOUT || path.join(root, "../jellyfin-edl-public"));
  const helper = path.join(checkout, ".github/skills/verify-jellyfin-edl/scripts/verify.py");
  if (!fs.existsSync(helper)) throw new Blocked(`plugin helper not found at ${helper}`);
  return { checkout, helper };
}

/** The Docker label that marks every plugin run this Editarr run starts; cleanup reclaims runs by it. */
export const pluginRunLabel = run => `editarr.verify.run=${path.basename(run.scratch)}`;

const killGroup = child => { try { process.kill(-child.pid, "SIGKILL"); } catch {} };

/**
 * Run the plugin helper with the run's Docker labels; resolve its exit code and output.
 * A timeout kills the helper's whole process group, so no Docker command it started
 * outlives it, and resolves `timedOut` with the output so far. Every helper but
 * `cleanup-label` joins `run.pluginHelpers` for `withPluginRun` to kill on an interrupt;
 * once the run is interrupted only `cleanup-label` starts.
 */
export function pluginHelper(plugin, run, args, timeout = 20 * 60 * 1000) {
  const labels = [pluginRunLabel(run), ...(process.env.VERIFY_PLUGIN_LABELS || "").split(",").filter(Boolean)];
  const argv = ["-B", plugin.helper, args[0], ...labels.flatMap(label => ["--label", label]), ...args.slice(1)];
  const interruptible = args[0] !== "cleanup-label";
  if (interruptible && run.interrupted) return Promise.resolve({ code: null, timedOut: false, interrupted: true, output: "interrupted before start\n" });
  return new Promise(resolve => {
    const child = spawn("python3", argv, { cwd: plugin.checkout, detached: true, stdio: ["ignore", "pipe", "pipe"] });
    if (interruptible) (run.pluginHelpers ??= new Set()).add(child);
    let output = "";
    let timedOut = false;
    child.stdout.on("data", chunk => { output += chunk; });
    child.stderr.on("data", chunk => { output += chunk; });
    const timer = setTimeout(() => { timedOut = true; killGroup(child); }, timeout);
    child.on("error", error => { output += `${error.message}\n`; });
    child.on("close", code => {
      clearTimeout(timer);
      run.pluginHelpers?.delete(child);
      const interrupted = interruptible && Boolean(run.interrupted) && !timedOut;
      resolve({ code: timedOut || interrupted ? null : code ?? 1, timedOut, interrupted, output });
    });
  });
}

/** Start `promise` in the background: a rejection waits for the later `await` instead of ending the process. */
export function background(promise) {
  promise.catch(() => {});
  return promise;
}

/**
 * Launch the plugin's Jellyfin over `tree`; return its evidence, port, container, users, items, and secrets.
 * Every failure carries the evidence path the helper printed first; `cleanupPlugin` needs only the run label.
 */
export async function launchPlugin(plugin, run, tree, { timeout } = {}) {
  const port = process.env.VERIFY_PLUGIN_PORT;
  const launched = await pluginHelper(plugin, run, ["launch", "--interop", tree, ...(port ? ["--port", port] : [])], timeout);
  fs.writeFileSync(path.join(run.evidence, "plugin-launch.log"), launched.output, { mode: 0o600 });
  const evidence = /^evidence: (.+)$/m.exec(launched.output)?.[1];
  const fail = error => Object.assign(error, { evidence, label: pluginRunLabel(run) });
  if (launched.timedOut) throw fail(new Error(`plugin launch timed out; its process group was killed`));
  if (launched.interrupted) throw fail(new Error(`plugin launch interrupted; its process group was killed`));
  if (launched.code === 2) throw fail(new Blocked(`plugin launch: ${launched.output.trim().split("\n").at(-2) ?? ""}`));
  if (launched.code !== 0 || !evidence) throw fail(new Error(`plugin launch exited ${launched.code}`));
  try {
    const read = name => JSON.parse(fs.readFileSync(path.join(evidence, name), "utf8"));
    const record = read("run.json");
    const hidden = JSON.parse(fs.readFileSync(path.join(record.scratch, "secrets.json"), "utf8"));
    for (const value of Object.values(hidden)) run.secrets.add(value);
    const { users, items, adminUserId } = read("items.json");
    return { evidence, record, users: { ...users, admin: adminUserId.replaceAll("-", "").toLowerCase() }, items, hidden };
  } catch (error) { throw fail(error); }
}

/** Run the plugin's read-only doctor (pinned server version, active plugin, this build's DLL, loopback port). */
export async function doctorPlugin(plugin, run, launched) {
  const record = path.join(launched.evidence, "doctor.json");
  fs.rmSync(record, { force: true });
  const result = await pluginHelper(plugin, run, ["doctor", launched.evidence], 5 * 60 * 1000);
  if (fs.existsSync(record)) fs.copyFileSync(record, path.join(run.evidence, "plugin-doctor.json"));
  assert.equal(result.code, 0, `plugin doctor: ${result.output.trim()}`);
}

/**
 * Write `cases`, run the plugin matrix, and return its checks. The phase passes only
 * when this invocation exits 0 and writes a passing record whose case names are exactly
 * the cases written, in order; a record left by an earlier invocation is refused.
 */
export async function pluginMatrix(plugin, run, launched, label, cases) {
  const output = path.join(launched.evidence, `matrix-${label}.json`);
  if (fs.existsSync(output)) throw new Error(`plugin matrix ${label} already has a record at ${output}; phase labels must be new`);
  const file = path.join(run.scratch, `matrix-${label}.json`);
  fs.writeFileSync(file, JSON.stringify({ cases }));
  const result = await pluginHelper(plugin, run, ["matrix", launched.evidence, file, label], 5 * 60 * 1000);
  fs.writeFileSync(path.join(run.evidence, `plugin-matrix-${label}.log`), result.output, { mode: 0o600 });
  const recorded = fs.existsSync(output) ? JSON.parse(fs.readFileSync(output, "utf8")) : null;
  if (recorded) fs.copyFileSync(output, path.join(run.evidence, `plugin-matrix-${label}.json`));
  const checks = Array.isArray(recorded?.checks) ? recorded.checks : [];
  const failed = checks.filter(check => check.ok !== true).map(check => ({ check: check.check, status: check.status, sha256: check.bodySha256, answers: check.answers, digestsSeen: check.digestsSeen, statusesSeen: check.statusesSeen }));
  const problems = [
    result.code !== 0 && `exited ${result.timedOut ? "on timeout" : result.interrupted ? "on interrupt" : result.code}`,
    !recorded && "wrote no record",
    recorded && recorded.ok !== true && "recorded ok is not true",
    recorded && failed.length && `failed checks ${JSON.stringify(failed)}`,
    recorded && JSON.stringify(checks.map(check => check.check)) !== JSON.stringify(cases.map(entry => entry.name)) && `recorded cases ${JSON.stringify(checks.map(check => check.check))} are not the ${cases.length} submitted`,
  ].filter(Boolean);
  if (problems.length) throw new Error(`plugin matrix ${label}: ${problems.join("; ")}: ${result.output.trim()}`);
  return checks;
}

/** Clean every plugin run carrying this run's label, including a launch that failed or timed out partway. */
export async function cleanupPlugin(plugin, run) {
  const label = pluginRunLabel(run);
  const result = await pluginHelper(plugin, run, ["cleanup-label", label]);
  return { label, code: result.timedOut ? null : result.code, timedOut: result.timedOut, output: result.output.trim().split("\n").slice(-6) };
}

/**
 * Close the journey's own resources, then clean the plugin even when closing threw.
 * A cleanup failure fails the run and names the journey error it follows; otherwise the
 * journey error, then the close error, is rethrown.
 */
export async function closePluginRun(plugin, run, close, failure) {
  let closeError;
  try { await close(); } catch (error) { closeError = error; }
  const cleanup = await cleanupPlugin(plugin, run);
  run.write("plugin-interop-cleanup.json", { ...cleanup, closeError: closeError?.message ?? null });
  if (cleanup.code !== 0) {
    const first = failure ?? closeError;
    throw new Error(`plugin cleanup exited ${cleanup.code}; rerun verify.py cleanup-label ${cleanup.label}${first ? `; after: ${first.message}` : ""}`);
  }
  if (failure) throw failure;
  if (closeError) throw closeError;
}

/**
 * Run `body`, then close the journey and clean the plugin by label with `closePluginRun`.
 * SIGINT or SIGTERM kills every running helper's process group at once, so a launch or
 * poll in progress fails now and label cleanup starts without waiting for it to finish.
 */
export async function withPluginRun(plugin, run, body, close) {
  const interrupt = () => {
    run.interrupted = true;
    for (const child of run.pluginHelpers ?? []) killGroup(child);
  };
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, interrupt);
  try {
    let failure;
    try { await body(); } catch (error) { failure = error; }
    await closePluginRun(plugin, run, close, failure);
  } finally {
    for (const signal of ["SIGINT", "SIGTERM"]) process.off(signal, interrupt);
  }
}

/**
 * What is wrong with a withdrawal's order, as messages; none means it passed. `plugin` is the
 * plugin poll's answer runs and `editarr` the export states Editarr's polls saw after the
 * policy change was sent at `enforceSentAt`. The plugin must serve the personal file, then
 * only the default, and Editarr must record the withdrawal. A default may only arrive after
 * the policy change was sent and after every Editarr poll that still saw the personal file
 * serving was sent, so a withdrawal that fails before Editarr's first poll still passes.
 */
export function withdrawalOrder({ enforceSentAt, editarr, plugin, personalSha256, defaultSha256 }) {
  const runs = JSON.stringify(plugin.map(span => [span.status, span.bodySha256]));
  if (runs !== JSON.stringify([[200, personalSha256], [200, defaultSha256]])) return [`the plugin did not serve the personal file, then only the default: ${runs}`];
  const recorded = editarr.findIndex(span => span.state === "failed" && span.serving === false);
  if (recorded < 0) return [`Editarr never recorded the withdrawal: ${JSON.stringify(editarr)}`];
  const before = editarr.slice(0, recorded);
  const servingUntil = Math.max(enforceSentAt, ...before.map(span => span.lastSentAt));
  const fallback = plugin[1].firstReceivedAt;
  if (fallback > servingUntil) return [];
  return [`the plugin served the default at ${fallback}, before ${servingUntil === enforceSentAt ? "the policy change was sent" : "an Editarr poll that still saw the personal file serving was sent"} at ${servingUntil}`];
}

/** Build the shared media tree: Movies/{Interop Movie, Versions (two files), Policy, Transfer} and Restricted/Hidden. */
export function mediaTree(run, source) {
  const tree = path.join(fs.realpathSync(run.scratch), "jellyfin-media");
  const files = {
    movie: "Movies/Interop Movie/Interop Movie.mkv",
    v1080: "Movies/Versions/Versions - 1080p.mkv",
    v2160: "Movies/Versions/Versions - 2160p.mkv",
    policy: "Movies/Policy/Policy.mkv",
    transfer: "Movies/Transfer/Transfer.mkv",
    hidden: "Restricted/Hidden/Hidden.mkv",
  };
  for (const [key, relative] of Object.entries(files)) {
    files[key] = path.join(tree, relative);
    fs.mkdirSync(path.dirname(files[key]), { recursive: true });
    fs.copyFileSync(source, files[key]);
  }
  return { tree, files };
}

/** Item-default and personal paths of the editarr-export-layout-1 contract. */
export function layout(media, format, exportId) {
  const directory = path.dirname(media);
  const stem = path.basename(media, path.extname(media));
  return {
    legacy: path.join(directory, `${stem}.${format}`),
    default: path.join(directory, format, `${stem}.${format}`),
    personal: exportId && path.join(directory, format, exportId, `${stem}.user-${exportId}.${format}`),
  };
}

/** One synthetic MCF whose title and low-severity violence cue, plus an optional drugs cue, make each review's bytes distinct. */
export function mcf(title, startSecond, drugsSecond) {
  const cue = (second, subcategory) => `00:00:0${second}.000 --> 00:00:0${second + 1}.000\n${subcategory}=low=video\n`;
  const cues = [cue(startSecond, "violence"), ...(drugsSecond === undefined ? [] : ["\n", cue(drugsSecond, "drugs")])].join("");
  return `WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE ${title}\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n${cues}`;
}

/**
 * The cues an exported file selects, in order: EDL contributor subcategories from its
 * `## subcategory: label` comments, and MCF `subcategory=severity` payloads.
 */
export function exportedCues(format, content) {
  const lines = content.toString("utf8").split("\n");
  if (format === "edl") return lines.flatMap(line => /^## ([A-Za-z]+): /.exec(line)?.[1] ?? []);
  return lines.flatMap(line => /^([A-Za-z]+)=([a-z]+)=[a-z]+/.exec(line)?.slice(1, 3).join("=") ?? []);
}

/** Signed-in API actors over Playwright request contexts; every token and cookie joins the run's secrets. */
export function actors(run) {
  const { request } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const origin = run.manifest.origin;
  const contexts = [];
  const statuses = [];
  async function actor() {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    contexts.push(context);
    let csrf;
    return async (method, url, data, status = 200, multipart) => {
      // Playwright's error carries a call log with the CSRF header and session cookie; keep only its first line.
      const response = await context.fetch(url, { method, headers: csrf ? { "X-CSRF-Token": csrf } : {}, ...(multipart ? { multipart } : data ? { data } : {}) })
        .catch(error => { throw new Error(`${method} ${url}: ${error.message.split("\n")[0]}`); });
      const body = response.status() === 204 ? {} : await response.json();
      if (body.csrf_token) { csrf = body.csrf_token; run.secrets.add(csrf); }
      for (const cookie of (await context.storageState()).cookies) run.secrets.add(cookie.value);
      statuses.push({ method, path: url.split("?")[0], status: response.status(), code: body?.detail?.code ?? body?.code ?? null });
      if (status !== null) assert.equal(response.status(), status, `${method} ${url}: ${JSON.stringify(body)}`);
      return status === null ? { status: response.status(), body } : body;
    };
  }
  async function signIn(username, password) {
    const call = await actor();
    await call("POST", SIGN_IN_PATH, { username, password });
    return call;
  }
  /** Sign the Jellyfin user `username` in, which admits it on its first sign-in. */
  async function member(username, password) {
    const call = await signIn(username, password);
    return { id: (await call("GET", "/api/auth/status")).access.principal.user_id, username, password, call };
  }
  const close = async () => { for (const context of contexts) await context.dispose(); };
  return { actor, signIn, member, statuses, close };
}
