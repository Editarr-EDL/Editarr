#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { authenticate } from "./lib/auth-journey.mjs";
import { capture } from "./lib/capture.mjs";
import { synthesizeMedia } from "./lib/fixtures.mjs";
import { FILM, LIBRARY, startSyntheticJellyfin } from "./lib/synthetic-jellyfin.mjs";
import { cleanup, createRun, inspect, launch, ready, stopOnSignals } from "./lib/instance.mjs";
import { childEnvironment, exitBlocked, missingPrerequisites, pythonPath } from "./lib/prerequisites.mjs";
import { captureScanState, deletedSharedScanDraft, mediaRevocation, privateDiagnostics } from "./lib/scan-access-probes.mjs";
import { foreignJobs } from "./lib/scan-foreign-jobs.mjs";
import { verifyConnectionWritesAndRemovedRoutes } from "./lib/scan-connection-probes.mjs";
import { prepareReviewMedia, verifySourceReviews } from "./lib/review-source-journey.mjs";
import { verifyModelChoices } from "./lib/scan-model-choices.mjs";
import { chooseModel, chooseScanType, openAdvancedVideoSettings, openWorkflowStep, refuseUnavailableModel } from "./lib/new-scan-controls.mjs";
import { SIGN_IN_PATH, addJellyfinUser, fillSignIn } from "./lib/run-jellyfin.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const require = createRequire(path.join(root, "web/package.json"));
const { expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const reviewsOnly = process.argv.length === 3 && process.argv[2] === "--reviews";
assert.ok(process.argv.length === 2 || reviewsOnly, "Only --reviews is supported");
const missing = [...missingPrerequisites("auth", root), ...missingPrerequisites("media", root)];
if (missing.length) exitBlocked(root, "scan-ui", missing);
const firstPort = Number(process.env.VERIFY_PORT_MIN || 8980);
const lastPort = Number(process.env.VERIFY_PORT_MAX || 8999);
let nextPort = firstPort;
const originalListen = net.Server.prototype.listen;
net.Server.prototype.listen = function (...args) {
  if (args[0] === 0) {
    while (nextPort <= lastPort && spawnSync("lsof", ["-nP", "-t", `-iTCP:${nextPort}`, "-sTCP:LISTEN"]).status === 0) nextPort++;
    assert.ok(nextPort <= lastPort, "No free port in the assigned proof range");
    args[0] = nextPort++;
  }
  return originalListen.apply(this, args);
};

const run = createRun(root);
stopOnSignals(run);
const boundary = path.join(run.scratch, "model-boundary");
fs.mkdirSync(boundary);
fs.writeFileSync(path.join(boundary, "sitecustomize.py"), [
  "from pathlib import Path",
  "from scanner.testing import test_fake_models",
  "from scanner.settings import capabilities",
  "test_fake_models.enabled = lambda: True",
  'capabilities.current_environment = lambda: capabilities.CapabilityEnvironment("Linux", "x86_64", lambda name: True, frozenset({"CPUExecutionProvider"}))',
  "from scanner.pipeline import runner",
  "original = runner._run_test_fake_models",
  "def synthetic_model(*args, **kwargs):",
  `    if Path(${JSON.stringify(path.join(run.scratch, "interrupt-model"))}).exists():`,
  `        raise PermissionError(13, "Synthetic model interruption", ${JSON.stringify(path.join(run.scratch, "unrelated-model-cache", "weights.bin"))})`,
  "    return original(*args, **kwargs)",
  "runner._run_test_fake_models = synthetic_model",
  "",
].join("\n"));
run.environment = { PYTHONPATH: `${boundary}${path.delimiter}${root}` };
const library = LIBRARY;
const item = FILM.id;
const version = FILM.version;
let media;
let provider;
const contexts = [];
const steps = [];
const requests = [];
const accessibility = [];
const errors = [];
const step = (name, details = {}) => { steps.push({ name, recordedAt: new Date().toISOString(), ...details }); run.write("scan-ui-steps.json", steps); };
let origin;

const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
async function send(context, method, route, data, multipart) {
  const status = await (await context.request.get(`${origin}/api/auth/status`)).json();
  if (status.csrf_token) run.secrets.add(status.csrf_token);
  const response = await context.request.fetch(`${origin}${route}`, { method, ...(multipart ? { multipart } : { data }),
    headers: { Origin: origin, ...(status.csrf_token ? { "X-CSRF-Token": status.csrf_token } : {}) } });
  requests.push({ method, path: route.split("?")[0], status: response.status() });
  run.write("scan-ui-http.json", requests);
  for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
  return response;
}

async function actor(width, username, password) {
  const context = await run.browser.newContext({ viewport: { width, height: 900 } });
  contexts.push(context);
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() }));
  const paths = [];
  page.on("request", request => paths.push(new URL(request.url()).pathname));
  await page.goto(`${origin}/#/library/${library}/item/${item}`);
  await fillSignIn(page, username, password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Synthetic catalog film", exact: true })).toBeVisible();
  await openWorkflowStep(page, "Scans", expect);
  return { context, page, paths };
}

async function axe(page, name) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name} overflows the viewport`);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  accessibility.push({ name, violations: result.violations.map(value => ({ id: value.id, nodes: value.nodes.length })) });
  run.write("scan-ui-axe.json", accessibility);
  assert.deepEqual(result.violations.map(value => value.id), [], `${name} has accessibility violations`);
}

async function history(context) {
  const response = await send(context, "GET", "/api/scans/history");
  assert.equal(response.status(), 200);
  return response.json();
}

async function activeJobs(context) {
  const response = await send(context, "GET", "/api/scans/queue");
  assert.equal(response.status(), 200);
  return response.json();
}

async function storedScan(context, name) {
  const response = await send(context, "POST", "/api/edl-imports/upload", undefined, {
    options: JSON.stringify({ target: { source: "jellyfin", item_id: item, media_source_id: version }, name }),
    file: { name: "synthetic.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") },
  });
  assert.equal(response.status(), 201, await response.text());
  const imported = await response.json();
  assert.equal((await send(context, "DELETE", `/api/reviews/${imported.review.id}?revision=${imported.review.revision}`)).status(), 204);
  return imported.scan;
}

const videoModels = { video: "nudenet", combined: "d1-3b" };
const SEVERITY_CHOICE = "qwen3-vl-4b-instruct-4bit";
const changedModelSelects = new WeakMap();

async function submit(actor, mode, name, width) {
  const { page, context } = actor;
  await openWorkflowStep(page, "Scans", expect);
  await page.getByRole("button", { name: "New Scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New Scan", exact: true });
  await chooseScanType(dialog, mode, expect);
  const capabilities = await (await send(context, "GET", "/api/capabilities")).json();
  const changed = changedModelSelects.get(page) ?? new Set();
  changedModelSelects.set(page, changed);
  const fresh = new Set(["qwen", ...(mode === "audio" ? [] : ["video"])].filter(select => !changed.has(select)));
  const choices = await verifyModelChoices({ dialog, capabilities, mode, fresh, expect });
  for (const select of fresh) changed.add(select);
  if (mode !== "video") {
    await expect(dialog.getByRole("button", { name: "Add to Queue", exact: true })).toBeEnabled();
    await dialog.getByLabel("Audio track", { exact: true }).selectOption("2");
    await chooseModel(dialog, "Transcriber", "whisper", expect);
    await chooseModel(dialog, "Filter", "better_profanity", expect);
  }
  if (mode !== "audio") {
    await chooseModel(dialog, "Video model", videoModels[mode], expect);
    await openAdvancedVideoSettings(dialog, expect);
    await dialog.getByLabel("Representative frames per flagged segment", { exact: true }).fill("6");
    await dialog.getByLabel("Join flagged segments within (seconds)", { exact: true }).fill("2");
  }
  // Where Qwen3-VL is unavailable (this run simulates Linux) its row is aria-disabled and the default selection stays.
  const severityChoice = choices.severity.find(option => option.id === SEVERITY_CHOICE);
  assert.ok(severityChoice, `${SEVERITY_CHOICE} is offered`);
  if (severityChoice.available) await chooseModel(dialog, "Severity and Description model", SEVERITY_CHOICE, expect);
  else await refuseUnavailableModel(dialog, "Severity and Description model", SEVERITY_CHOICE, expect);
  const expectedQwen = severityChoice.available ? SEVERITY_CHOICE : (capabilities.qwen_models.find(model => model.recommended) ?? capabilities.qwen_models[0]).id;
  await dialog.getByLabel("Scan name (optional)", { exact: true }).fill(name);
  const submitButton = dialog.getByRole("button", { name: "Add to Queue", exact: true });
  await submitButton.scrollIntoViewIfNeeded();
  await capture(page, run, `scan-${mode}-action-${width}`);
  await axe(page, `scan-${mode}-dialog-${width}`);
  const [response] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === "/api/scans" && response.request().method() === "POST"),
    submitButton.click(),
  ]);
  assert.equal(response.status(), 201, await response.text());
  const scan = await response.json();
  assert.equal(scan.name, name);
  assert.equal(scan.is_own, true);
  assert.equal(scan.scan_audio, mode !== "video");
  assert.equal(scan.scan_video, mode !== "audio");
  if (mode !== "video") assert.equal(scan.requested_audio_selection.track.stream_index, 2);
  if (mode !== "audio") assert.deepEqual([scan.video_model, scan.qwen_summaries_per_segment, scan.qwen_segment_join_gap_seconds], [videoModels[mode], 6, 2]);
  if (mode !== "video") assert.deepEqual([scan.audio_model, scan.audio_filter], ["whisper", "better_profanity"]);
  assert.equal(scan.qwen_model, expectedQwen);
  step("catalog submission persisted its exact options", { width, mode, scan: scan.id });
  return { ...scan, name };
}

try {
  const adapter = spawnSync(pythonPath(root), ["-I", "-c", "import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())", path.join(boundary, "sitecustomize.py")], { env: childEnvironment(root, run.data), encoding: "utf8" });
  assert.equal(adapter.status, 0, adapter.stderr);
  await launch(run);
  console.log(`Evidence: ${run.evidence}`);
  await ready(run);
  run.write("doctor.json", await inspect(run.manifest, root));
  const owner = await authenticate(run);
  origin = run.manifest.origin;
  media = synthesizeMedia(root, run.scratch, run.data);
  if (reviewsOnly) media = prepareReviewMedia(run, media);
  run.write("media.json", media);
  provider = await startSyntheticJellyfin(run, media);
  const ownerContext = await run.browser.newContext();
  contexts.push(ownerContext);
  assert.equal((await send(ownerContext, "POST", SIGN_IN_PATH, { username: owner.username, password: owner.password })).status(), 200);
  assert.equal((await send(ownerContext, "PUT", "/api/settings/jellyfin", provider.settings)).status(), 200);
  const { password: memberPassword } = await addJellyfinUser(run, "scan-member");
  const setup = await run.browser.newContext();
  contexts.push(setup);
  const signedIn = await send(setup, "POST", SIGN_IN_PATH, { username: "scan-member", password: memberPassword });
  assert.equal(signedIn.status(), 200);
  const memberId = (await signedIn.json()).access.principal.user_id;
  await verifyConnectionWritesAndRemovedRoutes({ run, send, ownerContext, memberContext: setup, memberId, providerUrl: provider.url, providerCalls: provider.calls, media, secret });
  step("owner connection test, disconnect, and reconnect succeed and the member's same requests are refused; Other Files and path import routes are gone and an upload naming another source is refused");

  if (!reviewsOnly) {
    const ownerActor = await actor(1440, owner.username, owner.password);
    const member = await actor(1440, "scan-member", memberPassword);
    await foreignJobs({ run, owner: ownerActor, member, ownerContext, media, interrupt: path.join(run.scratch, "interrupt-model"), submit, send, expect, capture, axe });
    step("foreign active, orphaned, and queued jobs stay hidden from the member");
  }
  for (const width of [1440, 320]) {
    if (reviewsOnly) {
      const member = await actor(width, "scan-member", memberPassword);
      await verifySourceReviews({ run, member, ownerContext, memberId, width, send, expect, axe, media, library, item });
      step("personal review source adapters and completed credits", { width });
      continue;
    }
    const other = await storedScan(ownerContext, `Other actor ${width}`);
    const member = await actor(width, "scan-member", memberPassword);
    const { page, context } = member;
    await expect(page.getByRole("list", { name: "Scan history", exact: true }).getByText(`Other actor ${width}`, { exact: false })).toBeVisible();
    assert.equal(await page.getByRole("button", { name: new RegExp(`^(Rename|Delete) Other actor ${width}`) }).count(), 0);
    await capture(page, run, `scan-catalog-before-${width}`);
    await axe(page, `scan-catalog-${width}`);
    assert.equal(await page.getByLabel("Video file path", { exact: true }).count(), 0);
    assert.equal((await send(context, "GET", `/api/library-items/${item}`)).status(), 200);
    const safeItem = await (await send(context, "GET", `/api/library-items/${item}`)).json();
    assert.equal(safeItem.path, null);
    assert.ok(safeItem.versions.every(value => value.path === null));
    const audio = await submit(member, "audio", `Audio ${width}`, width);
    await expect.poll(async () => {
      const job = (await activeJobs(context)).find(value => value.id === audio.id);
      return job?.status === "scanning" && job.progress_percent >= 25;
    }, { timeout: 60_000 }).toBe(true);
    await page.getByRole("button", { name: "Open scans queue and history", exact: true }).click();
    const queue = page.getByRole("region", { name: "Scans queue and history", exact: true });
    await expect(queue.getByText(`Audio ${width}`, { exact: false }).first()).toBeVisible();
    assert.equal(await queue.getByRole("button", { name: /Move.*up|Move.*down/ }).count(), 0);
    await capture(page, run, `scan-queue-action-${width}`);
    const row = queue.locator("tr").filter({ hasText: `Audio ${width}` }).first();
    await row.getByRole("button", { name: /^(Cancel|Remove)$/ }).click();
    await page.getByRole("button", { name: "Confirm", exact: true }).click();
    await expect.poll(async () => (await history(context)).find(value => value.id === audio.id)?.status, { timeout: 30_000 }).toBe("cancelled");
    await page.getByRole("button", { name: "Collapse scans panel", exact: true }).click();
    step("own cancellation persisted without global controls", { width });
    const video = await submit(member, "video", `Video ${width}`, width);
    await expect.poll(async () => (await history(context)).find(value => value.id === video.id)?.status, { timeout: 60_000 }).toBe("complete");
    await expect(page.getByRole("button", { name: `Rename Video ${width} · Scan #${video.scan_id}`, exact: true })).toBeVisible();
    await page.getByRole("button", { name: `Rename Video ${width} · Scan #${video.scan_id}`, exact: true }).press("Enter");
    await page.getByLabel(`Name for Video ${width} · Scan #${video.scan_id}`, { exact: true }).fill(`Renamed ${width}`);
    await page.getByRole("button", { name: "Save", exact: true }).press("Enter");
    await expect.poll(async () => (await history(context)).find(value => value.id === video.id)?.name).toBe(`Renamed ${width}`);
    await page.getByRole("button", { name: `Delete Renamed ${width} · Scan #${video.scan_id}`, exact: true }).click();
    await page.getByRole("button", { name: "Delete scan", exact: true }).click();
    await expect.poll(async () => (await history(context)).some(value => value.id === video.id)).toBe(false);
    step("own rename and deletion persisted", { width });
    fs.writeFileSync(path.join(run.scratch, "interrupt-model"), "synthetic model interruption");
    const combined = await submit(member, "combined", `Combined ${width}`, width);
    await expect.poll(async () => (await activeJobs(context)).find(value => value.id === combined.id)?.status, { timeout: 60_000 }).toBe("orphaned");
    await privateDiagnostics({ run, member, ownerContext, scan: combined, width, send, expect, capture, axe });
    step("worker diagnostics are path-safe for member and detailed for owner and logs", { width });
    fs.unlinkSync(path.join(run.scratch, "interrupt-model"));
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect.poll(async () => (await history(context)).find(value => value.id === combined.id)?.status, { timeout: 60_000 }).toBe("complete");
    await page.getByRole("button", { name: "Collapse scans panel", exact: true }).click();
    const review = await send(ownerContext, "POST", `/api/library-items/${item}/reviews?media_source_id=${version}`, { name: `Owner private ${width}`, memberships: [{ scan_id: combined.id, role: "both" }] });
    assert.equal(review.status(), 201, await review.text());
    await page.getByRole("button", { name: `Delete Combined ${width} · Scan #${combined.scan_id}`, exact: true }).click();
    await expect(page.getByText("Saved reviews you cannot view", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete scan", exact: true })).toBeDisabled();
    assert.equal(await page.getByText(`Owner private ${width}`, { exact: false }).count(), 0);
    const references = await (await send(context, "GET", `/api/scans/${combined.id}/references`)).json();
    assert.equal(references.has_hidden_references, true);
    assert.deepEqual(references.reviews, []);
    await capture(page, run, `scan-blocker-after-${width}`);
    await axe(page, `scan-blocker-${width}`);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
    step("own resume and private-reference deletion blocker", { width });
    const admin = await actor(width, owner.username, owner.password);
    await expect(admin.page.getByRole("button", { name: `Rename Combined ${width} · Scan #${combined.scan_id}`, exact: true })).toBeVisible();
    await expect(admin.page.getByRole("button", { name: `Rename Other actor ${width} · Scan #${other.scan_id}`, exact: true })).toBeVisible();
    await capture(admin.page, run, `scan-admin-after-${width}`);
    await axe(admin.page, `scan-admin-${width}`);
    await deletedSharedScanDraft({ run, member, ownerContext, scan: other, item, width, send, expect, capture, axe });
    step("deleted shared scan refuses Save without losing the accessible workspace or dirty draft", { width });
    await openWorkflowStep(page, "Scans", expect);
    await page.getByRole("button", { name: "New Scan", exact: true }).click();
    assert.equal((await send(ownerContext, "PATCH", `/api/users/${memberId}/permissions`, { overrides: { "media.browse": "deny" }, reauthentication: { password: owner.password } })).status(), 200);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.getByRole("heading", { name: "Page unavailable", exact: true })).toBeVisible();
    assert.equal(await page.getByRole("dialog", { name: "New Scan", exact: true }).count(), 0);
    assert.equal(await page.getByText(`Combined ${width}`, { exact: false }).count(), 0);
    await capture(page, run, `scan-revoked-after-${width}`);
    await axe(page, `scan-revoked-${width}`);
    assert.equal((await send(ownerContext, "PATCH", `/api/users/${memberId}/permissions`, { overrides: { "media.browse": "inherit" }, reauthentication: { password: owner.password } })).status(), 200);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.getByRole("heading", { name: "Synthetic catalog film", exact: true })).toBeVisible();
    // Editarr keeps fetched Jellyfin items until the connection is saved again.
    const dropItemCache = async () => assert.equal((await send(ownerContext, "PUT", "/api/settings/jellyfin", provider.settings)).status(), 200);
    const resync = async () => assert.equal((await send(ownerContext, "POST", "/api/jellyfin/sync")).status(), 200);
    await mediaRevocation({ run, member, providerState: provider.state, dropItemCache, resync, title: "Synthetic catalog film", scanName: `Combined ${width}`, width, send, expect, capture, axe });
    step("media-only target loss clears workspace and requires fresh authorization", { width });
    assert.deepEqual(member.paths.filter(value => value.startsWith("/api/") && /\/settings\/|\/models(?:\/|$)|\/path-mappings/.test(value)), []);
    step("permission revision clears inaccessible catalog and pending selection", { width });
  }
  assert.deepEqual(errors, []);
  run.write("scan-ui-result.json", { passed: true, viewports: [1440, 320], steps: steps.length, providerRequests: provider.calls.length,
    scope: reviewsOnly ? "Real member review imports, timing saves, completed chapter-based credits and clips, copy, rename, delete, and private-route refusals for Jellyfin. Synthetic provider transport. No mocked browser APIs or credits detector, model downloads, live-provider, inference or installed-runtime certification." : "Real authenticated source UI, API, persisted jobs, and FFmpeg media. Synthetic Jellyfin transport and existing fake-model adapter. No inference, model-download, or live-provider certification." });
} catch (error) {
  run.write("scan-ui-failure.json", { message: error.message, stack: error.stack });
  if (run.manifest) captureScanState(run);
  for (const [index, context] of contexts.entries()) {
    const page = context.pages()[0];
    if (page && !page.isClosed()) await capture(page, run, `scan-failure-${index}`).catch(() => {});
  }
  console.error(error.message);
  process.exitCode = 1;
} finally {
  run.write("scan-ui-http.json", requests);
  if (run.manifest) run.write("scan-worker-lifecycle.json", {
    capturedAt: new Date().toISOString(),
    logs: Object.fromEntries(["application.log", "worker.log"].map(name => {
      const file = path.join(run.manifest.logs, name);
      return [name, fs.existsSync(file) ? fs.readFileSync(file, "utf8") : null];
    })),
  });
  for (const context of contexts) {
    for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
    await context.close();
  }
  if (provider) await provider.close();
  await cleanup(run);
  try { await run.stopJellyfin?.(); }
  catch (error) { console.error(`Jellyfin cleanup failed: ${error.message}`); process.exitCode = 1; }
  if (run.manifest) await assert.rejects(() => inspect(run.manifest, root), /Recorded process group .* is not running/);
  run.write("doctor-after-cleanup.json", { stopped: true, evidenceRetained: fs.existsSync(path.join(run.evidence, "scan-ui-http.json")) });
  console.log(`Retained evidence: ${run.evidence}`);
}