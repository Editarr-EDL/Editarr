import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { capture } from "./capture.mjs";
import { cleanup, createRun, inspect, launch, launchBrowser, ready, sleep, stopOnSignals } from "./instance.mjs";
import { childEnvironment, evidenceRoot, pythonPath } from "./prerequisites.mjs";
import { linkUI } from "./account-link-ui.mjs";
import { SIGN_IN_PATH, addJellyfinUser, fillSignIn, startRunJellyfin } from "./run-jellyfin.mjs";

const FIXTURE = ".github/skills/verify-editarr/scripts/restore_journey_fixture.py";
const MEMBERS = ["restore-admin", "restore-user", "restore-linked", "restore-unlinked"];
const COMPARED = ["users", "providerIdentities", "dispositions", "apiKeys", "communityIdentities", "communityConnections", "communityOperations", "exports", "transferMoves"];

function python(run, args, options = {}) {
  return spawnSync(pythonPath(run.root), ["-B", "-P", ...args], {
    cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 120000, ...options,
  });
}

function fixture(run, ...args) {
  const input = typeof args.at(-1) === "object" ? JSON.stringify(args.pop()) : "";
  const result = python(run, [path.join(run.root, FIXTURE), ...args], { input });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout.trim().split("\n").at(-1));
}

function cli(run, ...args) {
  return python(run, ["-m", "scanner.cli", "--data-dir", run.data, "backup", ...args], { input: "" });
}

const sha256 = file => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const digests = directory => Object.fromEntries(fs.readdirSync(directory, { recursive: true }).sort()
  .filter(name => fs.statSync(path.join(directory, name)).isFile()).map(name => [name, sha256(path.join(directory, name))]));

// Saved ports only: a saved Base URL must match the development origin, which differs per run.
// The launcher's --port overrides both, so these test the restore without moving any listener.
const BACKUP_PORT = 9443;
const TARGET_PORT = 9444;
const BINDING_NOTICE = new RegExp(`keeps its own base URL and listening port\\. The backup used base URL default and port ${BACKUP_PORT}\\.`);

/** Save a deployment binding in a run's data directory before it starts. */
function bind(run, port) {
  fs.mkdirSync(run.data, { mode: 0o700, recursive: true });
  const file = path.join(run.data, "deployment.json");
  fs.writeFileSync(file, `${JSON.stringify({ version: 1, port })}\n`, { mode: 0o600, flag: "wx" });
  return sha256(file);
}

/** Private identity files keyed by home, with the incoming directory read as the owner's home. */
function identityFiles(inventory) {
  const owner = inventory.communityIdentities.find(([, user]) => user === 1)?.[0];
  return Object.fromEntries(Object.entries(inventory.files).map(([name, digest]) => [name.replace(/^incoming\//, `homes/${owner}/`), digest]));
}

/** Whether any process still belongs to `group`; zombies answer EPERM on macOS. */
function groupAlive(group) {
  try { process.kill(-group, 0); return true; }
  catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code !== "EPERM") throw error;
    const listing = spawnSync("ps", ["-axo", "pgid=,stat="], { encoding: "utf8", timeout: 5000 }).stdout;
    return listing.split("\n").some(line => Number(line.trim().split(/\s+/)[0]) === group && !line.includes("Z"));
  }
}

async function stopInstance(run) {
  if (groupAlive(run.child.pid)) process.kill(-run.child.pid, "SIGTERM");
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (!groupAlive(run.child.pid)) return;
    await sleep(100);
  }
  throw new Error("Instance did not stop");
}

async function relaunch(run, state) {
  await stopInstance(run);
  await launch(run);
  await ready(run, state);
}

async function browserPage(run, width = 1440) {
  const load = createRequire(path.join(run.root, "web/package.json"));
  const { expect, request } = load("@playwright/test");
  const { default: AxeBuilder } = load("@axe-core/playwright");
  await launchBrowser(run);
  const context = await run.browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const checkpoint = async name => {
    await capture(page, run, name);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    assert.deepEqual(result.violations.map(({ id }) => id), [], name);
  };
  return { context, page, expect, request, checkpoint };
}

async function signIn(page, expect, origin, username, password) {
  await page.goto(origin);
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
  await fillSignIn(page, username, password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toHaveCount(0);
}

/** Back up the seeded source through Settings, and prove it offers no restore. */
async function backUpSource(run, shared, accounts) {
  const { context, page, expect, checkpoint } = await browserPage(run);
  const origin = run.manifest.origin;
  await signIn(page, expect, origin, accounts.owner.name, accounts.owner.password);
  await page.goto(`${origin}/#/settings/general`);
  const backup = page.locator(".backup-settings").first();
  await expect(backup.getByRole("heading", { name: "Backup and restore" })).toBeVisible();
  for (const control of ["Backup file", "Backup contents"]) await expect(backup.getByLabel(control, { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Scheduled backup contents")).toHaveCount(0);
  await backup.getByRole("button", { name: "Create backup", exact: true }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), backup.getByRole("button", { name: "Download backup", exact: true }).click()]);
  const archive = path.join(shared, "source.backup");
  await download.saveAs(archive);
  assert.equal(await download.failure(), null);
  await checkpoint("source-settings");
  const oldSession = (await context.cookies()).map(cookie => ({ name: cookie.name, value: cookie.value }));
  oldSession.forEach(cookie => run.secrets.add(cookie.value));
  const refused = await context.request.post(`${origin}/api/setup-restore/flow`, { headers: { Origin: origin } });
  assert.equal(refused.status(), 409);
  assert.equal((await refused.json()).code, "setup_already_complete");
  await context.close();
  return { archive, oldSession };
}

/** Restore through onboarding on an empty instance, then sign in again after a restart. */
async function restoreByOnboarding(run, archive, seeded, accounts, oldSession, source, ownBinding) {
  const origin = run.manifest.origin;
  const { context, page, expect, request, checkpoint } = await browserPage(run);
  await page.goto(origin);
  await page.getByRole("button", { name: "Restore from backup" }).click();
  const panel = page.locator(".auth-panel");
  await panel.getByLabel("Backup file").setInputFiles(archive);
  await panel.getByRole("button", { name: "Inspect backup" }).click();
  await expect(panel.getByRole("heading", { name: "Restore preview" })).toBeVisible();
  await expect(panel.getByText("Other accounts' community identities: 1.", { exact: false })).toBeVisible();
  await expect(panel.getByLabel(/Replace/)).toHaveCount(0);
  await panel.getByLabel(/Restore this backup into this new installation/).check();
  await checkpoint("restore-preview");
  await panel.getByRole("button", { name: "Restore backup" }).click();
  await expect(panel.getByRole("heading", { name: "Restore complete" })).toBeVisible({ timeout: 60000 });
  await expect(panel.getByText(/Review media paths and source connections/)).toBeVisible();
  await expect(panel.getByText(BINDING_NOTICE)).toBeVisible();
  await checkpoint("restore-complete");
  await panel.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
  await context.close();

  await relaunch(run, "unauthenticated");
  const restarted = run.manifest.origin;
  const signedIn = await browserPage(run, 320);
  await signIn(signedIn.page, signedIn.expect, restarted, accounts.owner.name, accounts.owner.password);
  await signedIn.page.goto(`${restarted}/#/settings/general`);
  await signedIn.expect(signedIn.page.locator(".backup-settings").first().getByLabel("Backup file")).toHaveCount(0);
  await signedIn.checkpoint("restored-owner-320");
  await signedIn.context.close();

  const access = {};
  for (const name of ["restore-admin", "restore-user"]) {
    const api = await request.newContext();
    const login = await api.post(`${restarted}${SIGN_IN_PATH}`, { headers: { Origin: restarted }, data: { username: name, password: accounts[name].password } });
    access[name] = { login: login.status(), state: (await login.json()).state };
    await api.dispose();
  }
  for (const [name, key] of Object.entries(seeded.keys)) {
    const api = await request.newContext({ extraHTTPHeaders: { "X-Api-Key": key } });
    access[`${name}-key`] = (await api.get(`${restarted}/api/auth/api-key`)).status();
    await api.dispose();
  }
  const stale = await request.newContext({ extraHTTPHeaders: { Cookie: oldSession.map(({ name, value }) => `${name}=${value}`).join("; ") } });
  access.oldSession = (await stale.get(`${restarted}/api/users`)).status();
  await stale.dispose();
  run.write("access.json", access);
  assert.deepEqual(access, {
    "restore-admin": { login: 200, state: "authenticated" }, "restore-user": { login: 200, state: "authenticated" },
    "restore-owner-key": 200, "restore-admin-key": 200, "restore-user-key": 200, oldSession: 401,
  });

  const restored = fixture(run, "inventory", run.data);
  for (const key of COMPARED) assert.deepEqual(restored[key], source[key], key);
  const unlinked = restored.users.find(row => row[3] === "restore-unlinked");
  assert.deepEqual([unlinked[2], unlinked[5]], ["disabled", "unlinked"], "the unlinked account is restored disabled with cause unlinked");
  assert.deepEqual(identityFiles(restored), identityFiles(source));
  assert.deepEqual(restored.foreignKeyViolations, []);
  assert.deepEqual(restored.restoreEvents, [["system", "succeeded", '{"move_disagreements":2,"stage":"restored"}']]);
  const report = JSON.parse(fs.readFileSync(path.join(run.data, "restore-report.json"), "utf8"));
  assert.deepEqual(report.move_disagreements.map(({ finding, export_id: id }) => [finding, id]), [["unrecorded_copy", 3], ["recorded_path_missing", 3]]);
  const hold = JSON.parse(fs.readFileSync(path.join(run.data, "restore-reconciliation.json"), "utf8"));
  assert.equal(hold.connection_review_required, true);
  assert.deepEqual(report.backup_deployment, { base_url: null, port: BACKUP_PORT });
  assert.equal(sha256(path.join(run.data, "deployment.json")), ownBinding, "the empty installation keeps its own binding");
  run.write("inventory-compared.json", { compared: COMPARED, identityFiles: Object.keys(identityFiles(restored)), report, hold });
  run.write("deployment-binding.json", { kept: JSON.parse(fs.readFileSync(path.join(run.data, "deployment.json"), "utf8")), backup: report.backup_deployment, noticeListed: true });
  await linkRestoredUnlinked(run, accounts, unlinked[0]);
}

/**
 * The owner links the restored `restore-unlinked` account, disabled for having no
 * Jellyfin link, to its Jellyfin user from Users. The preview says it becomes
 * active, the link enables it, and its Jellyfin sign-in then reaches that account.
 */
async function linkRestoredUnlinked(run, accounts, userId) {
  const member = accounts["restore-unlinked"];
  const ui = linkUI(run, "restore-link");
  const browser = await ui.open(1440);
  try {
    await ui.signIn(browser, accounts.owner.name, accounts.owner.password, "users");
    const { page } = browser;
    await page.getByRole("button", { name: "View restore-unlinked", exact: true }).click();
    const details = page.getByRole("dialog", { name: "Account details", exact: true });
    const section = details.getByRole("region", { name: "Jellyfin link", exact: true });
    await ui.expect(section).toContainText("Not linked to Jellyfin. The account cannot sign in until it is linked.");
    assert.equal(await details.getByRole("button", { name: "Enable account", exact: true }).count(), 0, "an unlinked account offers no Enable");
    await ui.evidence(page, "unlinked-details-1440");
    await section.getByRole("button", { name: "Link to Jellyfin", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Link to Jellyfin", exact: true });
    const proven = await ui.prove(page, dialog, { username: member.name, password: member.password, actorPassword: accounts.owner.password });
    assert.equal(proven.status, 200, JSON.stringify(proven.body));
    assert.deepEqual([proven.body.preview.proof_basis, proven.body.preview.blockers, proven.body.preview.status], ["admin_attested", [], { current: "disabled", after: "active", cause: "unlinked" }]);
    await ui.expect(dialog.getByRole("region", { name: "Link impact", exact: true })).toContainText("disabled (no Jellyfin link) becomes active.");
    await ui.evidence(page, "unlinked-preview-1440");
    const linked = await ui.confirm(page, dialog, accounts.owner.password);
    assert.deepEqual([linked.status, linked.body.state], [200, "completed"]);
    await ui.evidence(page, "unlinked-linked-1440");
    const read = await ui.api(browser.context, "GET", `/api/users/${userId}`, undefined, 200);
    assert.deepEqual([read.status, read.disable_cause, read.source], ["active", null, "jellyfin"]);
  } finally { await ui.close(); }
  const signedIn = await fetch(`${run.manifest.origin}${SIGN_IN_PATH}`, { method: "POST", headers: { "Content-Type": "application/json", Origin: run.manifest.origin }, body: JSON.stringify({ username: member.name, password: member.password }) });
  const body = await signedIn.json();
  if (body.csrf_token) run.secrets.add(body.csrf_token);
  assert.deepEqual([signedIn.status, body.access?.principal?.user_id], [200, userId], "the linked account's Jellyfin sign-in reaches the restored account");
  run.write("restore-link.json", { user: userId, preview: { current: "disabled", after: "active", cause: "unlinked" }, linked: true, signIn: signedIn.status });
}

/**
 * An interrupted activation rolls back with `backup recover` to an empty installation,
 * which then starts at setup; the CLI restores it. The source development launcher
 * refuses a pending journal instead of recovering, unlike `editarr serve`.
 */
async function interruptThenRestoreByCli(run, archive, accounts) {
  fs.mkdirSync(run.data, { mode: 0o700, recursive: true });
  const crash = python(run, [path.join(run.root, FIXTURE), "interrupt", run.data, archive]);
  assert.equal(crash.status, 47, crash.stderr);
  assert.ok(fs.existsSync(path.join(run.data, "restore-activation.json")));
  const recovered = cli(run, "recover");
  assert.equal(recovered.status, 0, recovered.stderr);
  assert.match(recovered.stdout, /rolled back/);
  await launch(run);
  await ready(run, "setup_required");
  run.write("doctor.json", await inspect(run.manifest, run.root));
  assert.ok(!fs.existsSync(path.join(run.data, "restore-activation.json")));
  assert.deepEqual(fixture(run, "inventory", run.data).users, []);
  await stopInstance(run);
  const restored = cli(run, "restore", archive, "--confirm");
  assert.equal(restored.status, 0, restored.stderr);
  assert.match(restored.stdout, /Review media paths and source connections\. 2 transfer or link move records disagree/);
  assert.match(restored.stdout, new RegExp(`The backup used base URL default and port ${BACKUP_PORT}\\.`));
  assert.ok(!fs.existsSync(path.join(run.data, "deployment.json")), "an unbound empty installation stays unbound");
  await launch(run);
  await ready(run, "unauthenticated");
  const response = await fetch(`${run.manifest.origin}${SIGN_IN_PATH}`, {
    method: "POST", headers: { "Content-Type": "application/json", Origin: run.manifest.origin },
    body: JSON.stringify({ username: accounts.owner.name, password: accounts.owner.password }),
  });
  assert.equal(response.status, 200);
  run.write("cli-restore.json", { interruptedExit: crash.status, recoverExit: recovered.status, emptyAfterRecovery: true, restoreExit: restored.status, ownerLogin: response.status });
}

/** An installation with work but no account offers owner setup and names why it cannot restore. */
async function refuseOccupied(run) {
  fixture(run, "seed-occupied", run.data);
  const before = sha256(path.join(run.data, "scanner.db"));
  await launch(run);
  await ready(run);
  const { context, page, expect, checkpoint } = await browserPage(run, 320);
  await page.goto(run.manifest.origin);
  await page.getByRole("button", { name: "Restore from backup" }).click();
  await expect(page.getByRole("alert")).toContainText("already has scans, reviews, or settings");
  await checkpoint("occupied-refusal-320");
  const flow = await context.request.post(`${run.manifest.origin}/api/setup-restore/flow`, { headers: { Origin: run.manifest.origin } });
  assert.equal(flow.status(), 409);
  assert.equal((await flow.json()).detail.code, "restore_target_has_work");
  await context.close();
  await stopInstance(run);
  assert.equal(sha256(path.join(run.data, "scanner.db")), before);
  run.write("occupied-refusal.json", { onboarding: "refused", http: 409, code: "restore_target_has_work", databaseUnchanged: true });
}

/** Run the backup-restore journey across four disposable instances and keep its evidence. */
export async function runRestore(root, portRange) {
  const parent = evidenceRoot(root);
  fs.mkdirSync(parent, { recursive: true });
  const evidenceDirectory = fs.mkdtempSync(path.join(parent, "restore-"));
  const shared = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-restore-shared-"));
  const runs = {};
  const make = name => {
    const run = createRun(root, { portRange, evidenceDirectory });
    stopOnSignals(run);
    runs[name] = run;
    return run;
  };
  let exitCode = 0;
  try {
    const source = make("source");
    const jellyfin = await startRunJellyfin(source);
    const accounts = { owner: jellyfin.admin };
    for (const name of MEMBERS) accounts[name] = await addJellyfinUser(source, name);
    const seeded = fixture(source, "seed-source", source.data, path.join(source.scratch, "media"), {
      base: jellyfin.base, admin: { name: jellyfin.admin.name, password: jellyfin.admin.password },
      members: Object.fromEntries(MEMBERS.filter(name => name !== "restore-unlinked").map(name => [name, accounts[name].jellyfinId])),
    });
    bind(source, BACKUP_PORT);
    Object.values(seeded.keys).forEach(value => source.secrets.add(value));
    const media = digests(path.join(source.scratch, "media"));
    await launch(source);
    console.log(`Evidence: ${evidenceDirectory}`);
    await ready(source, "unauthenticated");
    source.write("doctor.json", await inspect(source.manifest, root));
    const { archive, oldSession } = await backUpSource(source, shared, accounts);
    const sourceInventory = fixture(source, "inventory", source.data);
    assert.equal(sourceInventory.transferMoves.length, 1, "the settled move survives the source's recovery");
    await stopInstance(source);
    const refused = cli(source, "restore", archive, "--confirm");
    assert.equal(refused.status, 1);
    assert.match(refused.stderr, /restore_target_has_accounts/);
    source.write("source.json", { archiveSha256: sha256(archive), sizeBytes: fs.statSync(archive).size, cliRefusal: "restore_target_has_accounts", inventory: { ...sourceInventory, users: sourceInventory.users.map(row => row.slice(0, 4)), apiKeys: sourceInventory.apiKeys.length } });

    const target = make("target");
    for (const run of [target, make("cli"), make("occupied")]) source.secrets.forEach(value => run.secrets.add(value));
    const ownBinding = bind(target, TARGET_PORT);
    await launch(target);
    await ready(target);
    target.write("doctor.json", await inspect(target.manifest, root));
    await restoreByOnboarding(target, archive, seeded, accounts, oldSession, sourceInventory, ownBinding);
    assert.deepEqual(digests(path.join(source.scratch, "media")), media, "media files unchanged");
    await interruptThenRestoreByCli(runs.cli, archive, accounts);
    await refuseOccupied(runs.occupied);
    console.log("PASS: onboarding restore into an empty instance, restart Jellyfin sign-in, keys, inventories and identity hashes, linking the restored unlinked account, interrupted activation, CLI restore, and non-empty refusals; the empty installation kept its own binding and listed the backup's.");
  } catch (error) {
    fs.writeFileSync(path.join(evidenceDirectory, "failure.json"), JSON.stringify({ message: error.message, stack: error.stack }, null, 2));
    console.error(error.stack);
    exitCode = 1;
  } finally {
    for (const run of Object.values(runs)) {
      try { await cleanup(run); } catch (error) { console.error(error.message); exitCode = 1; }
    }
    try { await runs.source?.stopJellyfin?.(); } catch (error) { console.error(`Jellyfin cleanup failed: ${error.message}`); exitCode = 1; }
    fs.rmSync(shared, { recursive: true, force: true });
  }
  const summary = { runs: Object.fromEntries(Object.entries(runs).map(([name, run]) => [name, path.basename(run.evidence)])), sharedRemoved: !fs.existsSync(shared), passed: exitCode === 0 };
  fs.writeFileSync(path.join(evidenceDirectory, "restore-runs.json"), JSON.stringify(summary, null, 2));
  return exitCode;
}

