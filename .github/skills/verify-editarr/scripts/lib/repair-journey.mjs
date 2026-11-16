import assert from "node:assert/strict";
import { accountMenuButton } from "./account-menu.mjs";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { cleanup, createRun, inspect, launch, launchBrowser, ready, stopOnSignals } from "./instance.mjs";
import { childEnvironment, evidenceRoot, pythonPath } from "./prerequisites.mjs";
import { FILM, LIBRARY, itemRoute, startSyntheticJellyfin } from "./synthetic-jellyfin.mjs";
import { SIGN_IN_PATH, addJellyfinUser, fillSignIn, startRunJellyfin } from "./run-jellyfin.mjs";

const COMMUNITY = "review-imported-002";
const LOCAL = "review-legacy-001";
const MESSAGE = "This review uses a scan that no longer exists.";
const UNAVAILABLE = "The missing scan could not be retrieved from the community. Choose one of the remaining options.";

export function cacheListing() {
  const root = path.join(process.env.HOME, "Library/Caches/Editarr");
  const entries = [];
  function visit(directory) {
    if (!fs.existsSync(directory)) return;
    for (const name of fs.readdirSync(directory).sort()) {
      const file = path.join(directory, name);
      const stat = fs.lstatSync(file, { bigint: true });
      entries.push([path.relative(root, file), String(stat.mode), String(stat.size), String(stat.mtimeNs), stat.isSymbolicLink() ? fs.readlinkSync(file) : null]);
      if (stat.isDirectory()) visit(file);
    }
  }
  visit(root);
  return { root, exists: fs.existsSync(root), entries: entries.length, sha256: createHash("sha256").update(JSON.stringify(entries)).digest("hex") };
}

function databaseInventory(run, file) {
  const code = `import json, sqlite3, sys
db = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
counts = {table: db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] for table in tables}
print(json.dumps({'counts': counts, 'foreignKeyViolations': db.execute('PRAGMA foreign_key_check').fetchall()}))`;
  return JSON.parse(execFileSync(pythonPath(run.root), ["-B", "-P", "-c", code, file], {
    cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 30000,
  }));
}

async function prove(run, width) {
  const load = createRequire(path.join(run.root, "web/package.json"));
  const { expect } = load("@playwright/test");
  const { default: AxeBuilder } = load("@axe-core/playwright");
  const origin = run.manifest.origin;
  const journal = JSON.parse(fs.readFileSync(path.join(run.data, "migration-upgrade.json"), "utf8"));
  assert.equal(journal.status, "complete");
  assert.equal(journal.from_revision, run.fixture.revision);
  assert.equal(journal.to_revision, "0030_remove_passwords");
  const snapshot = path.join(journal.generation, "snapshot.sqlite3");
  const before = databaseInventory(run, snapshot);
  assert.deepEqual(before.counts, run.fixture.counts);
  assert.deepEqual(before.foreignKeyViolations, run.fixture.foreignKeyViolations);
  const after = databaseInventory(run, path.join(run.data, "scanner.db"));
  assert.deepEqual(after.foreignKeyViolations, []);
  assert.equal(after.counts.review_scan_repairs, 2);
  run.write("migration.json", { journal, snapshotVerified: true, before, after });
  await launchBrowser(run);
  const context = await run.browser.newContext({ viewport: { width, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  const statuses = [];
  const axe = [];
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const checkpoint = async name => {
    await capture(page, run, name);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    axe.push({ name, violations: result.violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) })) });
    run.write("axe.json", axe);
    assert.deepEqual(axe.at(-1).violations, [], name);
  };
  let csrf;
  const send = async (method, url, data, api = context.request, token = csrf) => {
    const response = await api.fetch(`${origin}${url}`, {
      method, ...(data === undefined ? {} : { data }),
      headers: { Origin: origin, ...(token && method !== "GET" ? { "X-CSRF-Token": token } : {}) },
    });
    const body = response.status() === 204 ? null : await response.json();
    statuses.push({ method, path: url, status: response.status(), code: body?.detail?.code ?? body?.code ?? null });
    run.write("http-statuses.json", statuses);
    return { status: response.status(), body };
  };
  try {
    await page.goto(`${origin}/${itemRoute(FILM)}`);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    await fillSignIn(page, run.jellyfin.admin.name, run.jellyfin.admin.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page)).toBeVisible();
    const auth = await (await context.request.get(`${origin}/api/auth/status`)).json();
    csrf = auth.csrf_token;
    if (csrf) run.secrets.add(csrf);
    for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
    assert.equal((await send("PUT", "/api/settings/jellyfin", run.provider.settings)).status, 200);
    await page.reload();
    await expect(page.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
    const table = page.getByRole("table", { name: "Saved reviews", exact: true });
    await expect(table).toBeVisible();
    const healthy = await send("GET", "/api/reviews/healthy");
    assert.equal(healthy.status, 200);
    assert.equal(healthy.body.repair, null);
    const geometry = [];
    for (const name of ["Community repair", "Local repair"]) {
      const open = table.getByRole("button", { name: `Open review ${name}`, exact: true });
      await expect(open).toBeDisabled();
      const row = open.locator("xpath=ancestor::tr");
      const alert = row.locator("xpath=following-sibling::tr[1]");
      await expect(alert.getByText(MESSAGE, { exact: true })).toBeVisible();
      await expect(alert.getByRole("img", { name: "Review needs repair" })).toHaveText("!");
      const top = await row.boundingBox();
      const below = await alert.boundingBox();
      assert.ok(below.y >= top.y + top.height - 1, "repair message occupies a separate row");
      const color = await alert.getByRole("img").evaluate(element => getComputedStyle(element).color);
      assert.equal(color, "rgb(255, 180, 174)");
      geometry.push({ name, row: top, error: below, color, openDisabled: true });
    }
    await expect(table.getByRole("button", { name: "Open review Healthy review", exact: true })).toBeEnabled();
    assert.equal(await table.getByRole("img", { name: "Review needs repair" }).count(), 2);
    run.write("row-geometry.json", geometry);
    await checkpoint("flagged-list");
    const conflict = await send("GET", `/api/reviews/${COMMUNITY}`);
    assert.equal(conflict.status, 409);
    assert.equal(conflict.body.detail.code, "review_needs_repair");
    assert.equal(conflict.body.detail.repair.recover.kept_cues, 1);
    run.write("flagged-api.json", conflict);

    const readerUser = await addJellyfinUser(run, "repair-reader");
    const reader = await load("@playwright/test").request.newContext();
    let permissionSpecimen;
    try {
      const signedIn = await send("POST", SIGN_IN_PATH, { username: readerUser.name, password: readerUser.password }, reader, null);
      assert.equal(signedIn.status, 200);
      const created = { body: { id: signedIn.body.access.principal.user_id } };
      permissionSpecimen = JSON.parse(execFileSync(pythonPath(run.root), ["-B", "-P", path.join(run.root, ".github/skills/verify-editarr/scripts/review_repairs_fixture.py"), run.data, String(created.body.id)], {
        cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 30000,
      }));
      const permissions = await send("PATCH", `/api/users/${created.body.id}/permissions`, {
        overrides: { "reviews.view.other": "allow", "reviews.save.own": "deny", "reviews.edit.other": "deny" },
        reauthentication: { password: run.jellyfin.admin.password },
      });
      assert.equal(permissions.status, 200);
      const readerStatus = await send("GET", "/api/auth/status", undefined, reader, null);
      if (readerStatus.body.csrf_token) run.secrets.add(readerStatus.body.csrf_token);
      const visibleFlag = await send("GET", "/api/reviews/permission-probe", undefined, reader, readerStatus.body.csrf_token);
      assert.equal(visibleFlag.status, 409);
      const denied = await send("POST", "/api/reviews/permission-probe/repair/recover", {}, reader, readerStatus.body.csrf_token);
      assert.equal(denied.status, 403);
      run.write("recover-denied.json", denied);
      for (const cookie of (await reader.storageState()).cookies) run.secrets.add(cookie.value);
    } finally { await reader.dispose(); }
    assert.equal((await send("DELETE", `/api/reviews/permission-probe?revision=${permissionSpecimen.revision}`)).status, 204);

    const dialog = page.getByRole("dialog", { name: "Resolve review", exact: true });
    await table.getByRole("button", { name: "Resolve Local repair", exact: true }).click();
    await expect(dialog.getByRole("radio", { name: "Re-download from the community", exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("radio")).toHaveCount(2);
    await checkpoint("local-options");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await table.getByRole("button", { name: "Resolve Community repair", exact: true }).click();
    await expect(dialog.getByRole("radio")).toHaveCount(3);
    await dialog.getByRole("radio", { name: "Re-download from the community", exact: true }).check();
    await checkpoint("community-options");
    const redownload = page.waitForResponse(response => response.url().endsWith(`/api/reviews/${COMMUNITY}/repair/community`));
    await dialog.getByRole("button", { name: "Re-download", exact: true }).click();
    const failure = await redownload;
    assert.equal(failure.status(), 409);
    const failureBody = await failure.json();
    assert.equal(failureBody.detail.reason, "not_connected");
    run.write("community-unavailable.json", failureBody);
    await expect(dialog.getByRole("radio", { name: "Re-download from the community", exact: true })).toBeDisabled();
    await expect(dialog.getByText(`${UNAVAILABLE} Your account is not connected to the community service.`, { exact: true })).toBeVisible();
    await checkpoint("community-unavailable");
    await dialog.getByRole("radio", { name: "Recover the review", exact: true }).check();
    await dialog.getByRole("button", { name: "Recover review", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(table.getByRole("button", { name: "Open review Community repair", exact: true })).toBeEnabled();
    await table.getByRole("button", { name: "Open review Community repair", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Community repair", exact: true })).toBeVisible();
    const recovered = await send("GET", `/api/reviews/${COMMUNITY}`);
    assert.equal(recovered.body.repair, null);
    assert.equal(recovered.body.filters.length, 1);
    assert.equal(recovered.body.filters[0].label, "Preserved community cue");
    assert.equal(recovered.body.filters[0].source_finding_id, null);
    assert.notEqual(recovered.body.filters[0].source_scan_id, "imported-scan-003");
    const scanHistory = await send("GET", "/api/scans/history");
    assert.equal(scanHistory.status, 200);
    const manual = scanHistory.body.find(scan => scan.id === recovered.body.filters[0].source_scan_id);
    assert.equal(manual?.manual, true);
    await page.getByRole("button", { name: "Rename review", exact: true }).click();
    await page.getByRole("textbox", { name: "Review name", exact: true }).fill("Recovered community");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Recovered community", exact: true })).toBeVisible();
    const saved = await send("GET", `/api/reviews/${COMMUNITY}`);
    assert.equal(saved.body.name, "Recovered community");
    assert.deepEqual(saved.body.filters, recovered.body.filters);
    run.write("recovered.json", { recovered: recovered.body, saved: saved.body, manualScan: manual });
    await checkpoint("recovered-editable");

    await table.getByRole("button", { name: "Resolve Local repair", exact: true }).click();
    await dialog.getByRole("radio", { name: "Delete the review", exact: true }).check();
    await dialog.getByRole("button", { name: "Continue", exact: true }).click();
    const reviewDeletion = page.getByRole("dialog", { name: "Delete review", exact: true });
    await expect(reviewDeletion.getByText("No published files you can see come from this review.")).toBeVisible();
    await reviewDeletion.getByRole("button", { name: "Delete review", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(table.getByRole("button", { name: "Open review Local repair", exact: true })).toHaveCount(0);
    assert.equal((await send("GET", `/api/reviews/${LOCAL}`)).status, 404);
    const healthyAfter = await send("GET", "/api/reviews/healthy");
    assert.deepEqual(healthyAfter.body, healthy.body);
    await checkpoint("local-deleted");

    const history = page.getByRole("list", { name: "Scan history", exact: true });
    await history.getByRole("button", { name: "Delete Unused community · Scan #92", exact: true }).click();
    const deletion = page.getByRole("dialog");
    await expect(deletion.getByRole("button", { name: "Delete scan", exact: true })).toBeEnabled();
    await checkpoint("unused-delete-precheck");
    await deletion.getByRole("button", { name: "Delete scan", exact: true }).click();
    await expect(deletion).toHaveCount(0);
    const remaining = await send("GET", "/api/scans/history");
    assert.equal(remaining.status, 200);
    assert.ok(!remaining.body.some(scan => scan.id === "unused-community"));
    await history.getByRole("button", { name: "Delete Pending community · Scan #93", exact: true }).click();
    await expect(deletion.getByText("A community operation for this scan is still in progress", { exact: true })).toBeVisible();
    await expect(deletion.getByRole("button", { name: "Delete scan", exact: true })).toBeDisabled();
    await checkpoint("pending-delete-blocked");
    await deletion.getByRole("button", { name: "Cancel", exact: true }).click();
    const final = databaseInventory(run, path.join(run.data, "scanner.db"));
    assert.deepEqual(final.foreignKeyViolations, []);
    assert.equal(final.counts.review_scan_repairs, 0);
    assert.deepEqual(errors, []);
    run.write("result.json", { passed: true, width, P1: true, P2: true, P3: true, P4: true, P5: true, P6: true, final, pageErrors: errors, axeChecks: axe.length });
  } catch (error) {
    await capture(page, run, "failure").catch(() => {});
    throw error;
  } finally { await context.close(); }
}

export async function runReviewRepairs(root, portRange) {
  const parent = evidenceRoot(root);
  fs.mkdirSync(parent, { recursive: true });
  const evidenceDirectory = fs.mkdtempSync(path.join(parent, "repairs-"));
  const before = cacheListing();
  fs.writeFileSync(path.join(evidenceDirectory, "cache-before.json"), JSON.stringify(before, null, 2));
  const runs = [];
  let exitCode = 0;
  for (const width of [1440, 320]) {
    const run = createRun(root, { portRange, evidenceDirectory });
    stopOnSignals(run);
    runs.push({ width, evidence: run.evidence });
    try {
      const media = { file: path.join(fs.realpathSync(run.scratch), "media", "Movie.mkv"), durationSeconds: 120 };
      const jellyfin = await startRunJellyfin(run);
      await jellyfin.call("POST", "/Auth/Keys?app=Editarr");
      const [key] = await jellyfin.editarrKeys();
      run.provider = await startSyntheticJellyfin(run, media, { port: await run.ports.next() });
      const output = execFileSync(pythonPath(root), ["-B", "-P", path.join(root, ".github/skills/verify-editarr/scripts/review_repairs_fixture.py"), run.data,
        JSON.stringify({ server: run.provider.url, library: LIBRARY, item: FILM.id, version: FILM.version, title: FILM.title })], {
        cwd: root, env: childEnvironment(root, run.data), encoding: "utf8", timeout: 120000,
        input: JSON.stringify({ base: jellyfin.base, key, serverId: jellyfin.serverId, adminId: jellyfin.admin.id }),
      });
      run.fixture = JSON.parse(output.trim().split("\n").at(-1));
      run.secrets.add("d".repeat(64));
      run.write("fixture.json", run.fixture);
      run.environment = {
        EDITARR_COMMUNITY_SERVICE_URL: "https://community.test", EDITARR_COMMUNITY_SERVICE_AUDIENCE: "community.test",
        EDITARR_COMMUNITY_DOWNLOAD_BOUNDS: JSON.stringify({ compressed_bytes: 100000, expanded_bytes: 500000, events: 1000, text: 10000, depth: 20 }),
        EDITARR_COMMUNITY_DOWNLOAD_RESPONSE_BYTES: "100000",
      };
      await launch(run);
      console.log(`Evidence: ${run.evidence}`);
      await ready(run, "unauthenticated");
      run.write("doctor.json", await inspect(run.manifest, root));
      await prove(run, width);
    } catch (error) {
      run.write("failure.json", { message: error.message, stack: error.stack });
      console.error(error.stack);
      exitCode = 1;
    } finally {
      try { await cleanup(run); } catch (error) { console.error(error.message); exitCode = 1; }
      if (run.provider) await run.provider.close();
      try { await run.stopJellyfin?.(); } catch (error) { console.error(`Jellyfin cleanup failed: ${error.message}`); exitCode = 1; }
    }
    if (exitCode) break;
  }
  const after = cacheListing();
  fs.writeFileSync(path.join(evidenceDirectory, "cache-after.json"), JSON.stringify(after, null, 2));
  const cacheUnchanged = JSON.stringify(before) === JSON.stringify(after);
  fs.writeFileSync(path.join(evidenceDirectory, "repairs-runs.json"), JSON.stringify({ runs, cacheUnchanged, passed: exitCode === 0 && cacheUnchanged }, null, 2));
  if (!cacheUnchanged) { console.error("Host Editarr cache listing changed"); exitCode = 1; }
  return exitCode;
}