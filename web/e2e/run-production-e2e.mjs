/** Launch disposable installed-artifact TLS fixtures around production Playwright. */
import { spawn, spawnSync } from "node:child_process";
import { constants } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

const required = ["EDITARR_PRODUCTION_EDITARR"];
const missing = required.filter((name) => !process.env[name]);
if (missing.length) {
  throw new Error(`Production browser checks require ${missing.join(", ")}. It must point to a disposable installed artifact.`);
}

const directory = path.dirname(fileURLToPath(import.meta.url));
const script = path.resolve(directory, "../../scripts/production_browser_fixture.py");
const python = process.env.EDITARR_PRODUCTION_PYTHON ?? "python3";
const proxy = process.env.EDITARR_PRODUCTION_PROXY ?? "nginx";
const prefix = process.env.EDITARR_PRODUCTION_PREFIX ?? "/";
// Playwright empties its output directory first; keep it away from other evidence.
const outputDirectory = path.resolve(process.env.EDITARR_PRODUCTION_OUTPUT ?? path.join(directory, "../test-results/production-e2e"));
// The fixture writes its logs at teardown, after Playwright has emptied this directory.
const fixtureArgs = [script, "--editarr", process.env.EDITARR_PRODUCTION_EDITARR,
  "--proxy", proxy, "--prefix", prefix, "--log-dir", path.join(outputDirectory, "fixture-logs")];
if (process.env.EDITARR_PRODUCTION_DEPENDENCY_SITE_PACKAGES) {
  fixtureArgs.push("--dependency-site-packages", process.env.EDITARR_PRODUCTION_DEPENDENCY_SITE_PACKAGES);
}
const fixture = spawn(python, fixtureArgs, { stdio: ["pipe", "pipe", "inherit"] });
const fixtureExited = new Promise((resolve) => { fixture.once("exit", resolve); fixture.once("error", resolve); });

let output = "";
const ready = new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error("Timed out starting the production TLS fixture.")), 30_000);
  fixture.stdout.on("data", (chunk) => {
    output += chunk;
    const newline = output.indexOf("\n");
    if (newline < 0) return;
    clearTimeout(timeout);
    try { resolve(JSON.parse(output.slice(0, newline))); } catch (error) { reject(error); }
  });
  fixture.once("error", reject);
  fixture.once("exit", (code) => reject(new Error(`Production TLS fixture exited before ready (${code}).`)));
});

/** Run production Playwright against the ready fixture; resolves with its exit status. */
function runPlaywright(details) {
  console.log(`Production fixture: ${JSON.stringify({ proxy: details.proxy, base_url: details.base_url, prefix: details.prefix, upstream_port: details.upstream_port, jellyfin_url: details.jellyfin_url })}`);
  // pnpm exits before Playwright finishes writing traces, so Playwright runs in its own group that is signalled and awaited whole.
  child = spawn("pnpm", ["exec", "playwright", "test", "-c", "playwright.production.config.ts", "--output", outputDirectory], {
    detached: true,
    stdio: "inherit",
    env: {
      ...process.env,
      EDITARR_PRODUCTION_BASE_URL: details.base_url,
      EDITARR_PRODUCTION_PREFIX: details.prefix,
      EDITARR_PRODUCTION_SECONDARY_BASE_URL: details.secondary_base_url,
      EDITARR_PRODUCTION_CONTROL_PORT: String(details.control_port),
      EDITARR_PRODUCTION_CERTIFICATE_ARG: details.certificate_arg,
      EDITARR_PRODUCTION_UPSTREAM_PORT: String(details.upstream_port),
      EDITARR_PRODUCTION_PROXY: details.proxy,
      EDITARR_PRODUCTION_JELLYFIN_URL: details.jellyfin_url,
      EDITARR_PRODUCTION_JELLYFIN_USERNAME: details.jellyfin_username,
      EDITARR_PRODUCTION_JELLYFIN_PASSWORD: details.jellyfin_password,
    },
  });
  return new Promise((resolve) => {
    child.once("exit", (value) => resolve(value ?? 1));
    child.once("error", (error) => { console.error(`Playwright did not start: ${error.message}`); resolve(1); });
  }).then(async (status) => { await groupExited(child.pid); return status; });
}

function signalGroup(pid, signal) {
  try { process.kill(-pid, signal); return true; } catch { return false; }
}

/** Resolve once every process in the Playwright group has exited, killing stragglers after 60 s. */
async function groupExited(pid) {
  if (pid === undefined) return;
  for (const deadline = Date.now() + 60_000; signalGroup(pid, 0);) {
    if (Date.now() > deadline) { signalGroup(pid, "SIGKILL"); return; }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

let child;
let code = 1;
// The first interrupt stops the workload, then the finally block tears down the fixture and scrubs evidence.
// Later interrupts are ignored: Node's default action would exit before the scrub.
let interrupted;
const interruption = new Promise((resolve) => {
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => {
      if (interrupted) return;
      interrupted = signal;
      if (child) signalGroup(child.pid, signal);
      resolve();
    });
  }
});
try {
  const details = await Promise.race([ready, interruption]);
  if (!interrupted) code = await runPlaywright(details);
} finally {
  fixture.stdin.end();
  await fixtureExited;
  // Traces and fixture logs can hold request bodies, cookies, and CSRF headers; scrub them before anyone keeps the output.
  const scrub = spawnSync(python, [path.resolve(directory, "../../scripts/evidence_secrets.py"), "--redact", outputDirectory], { stdio: "inherit" });
  if (scrub.status !== 0) console.error(`Credentials remain in ${outputDirectory} after redaction.`);
  process.exitCode = interrupted ? 128 + constants.signals[interrupted] : code || (scrub.status === 0 ? 0 : 1);
}
