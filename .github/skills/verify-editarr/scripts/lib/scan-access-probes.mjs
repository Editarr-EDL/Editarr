import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { openWorkflowStep } from "./new-scan-controls.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";

export function captureScanState(run) {
  const capturedAt = new Date().toISOString();
  try {
    const data = fs.realpathSync(run.manifest.data);
    assert.equal(data, path.join(fs.realpathSync(run.scratch), "data"), "Scan state must use this run's scratch data");
    const code = `
import json, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], uri=True)
connection.row_factory = sqlite3.Row
rows = connection.execute("""
SELECT s.id, s.name, s.status, s.progress_percent, s.progress_stage,
       s.progress_message, s.error_message, s.worker_pid, s.updated_at,
       q.id AS queue_id
FROM scans s LEFT JOIN scan_queue q ON q.scan_id = s.id ORDER BY s.id
""").fetchall()
connection.close()
print(json.dumps([dict(row) for row in rows]))
`;
    const result = spawnSync(pythonPath(run.root), ["-I", "-B", "-c", code, `${pathToFileURL(path.join(data, "scanner.db")).href}?mode=ro`], {
      cwd: run.root, env: childEnvironment(run.root, data), encoding: "utf8", timeout: 5000,
    });
    assert.equal(result.status, 0, result.error?.message || result.stderr);
    run.write("scan-ui-state.json", { capturedAt, scans: JSON.parse(result.stdout) });
  } catch (error) {
    run.write("scan-ui-state.json", { capturedAt, error: error.message });
  }
}

export async function deletedSharedScanDraft({ run, member, ownerContext, scan, item, width, send, expect, capture, axe }) {
  const { page, context } = member;
  const name = `Unsaved shared scan draft ${width}`;
  const itemRoute = `/api/library-items/${item}`;
  await openWorkflowStep(page, "Review", expect);
  await page.getByRole("button", { name: "New Review", exact: true }).click();
  await page.getByLabel("New review name", { exact: true }).fill(name);
  await page.getByRole("button", { name: "Start new review", exact: true }).click();
  await page.getByRole("button", { name: /^Sources · / }).click();
  const sources = page.getByRole("dialog", { name: "Included scans", exact: true });
  await sources.getByRole("checkbox", { name: new RegExp(`^Other actor ${width}`) }).check();
  await sources.getByRole("button", { name: "Done", exact: true }).click();
  const save = page.getByRole("button", { name: "Save Review", exact: true });
  await expect(save).toBeEnabled();
  await capture(page, run, `scan-draft-before-deletion-${width}`);
  await axe(page, `scan-draft-before-deletion-${width}`);
  const deletion = await send(ownerContext, "DELETE", `/api/scans/${scan.id}`);
  assert.equal(deletion.status(), 200, await deletion.text());
  assert.equal((await deletion.json()).deleted, true);
  const [failure, revalidation] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `${itemRoute}/reviews`
      && response.request().method() === "POST" && response.status() === 404),
    page.waitForResponse(response => new URL(response.url()).pathname === itemRoute
      && response.request().method() === "GET" && response.status() === 200),
    save.click(),
  ]);
  assert.equal((await failure.json()).detail.code, "scan_not_found");
  await expect(page.getByText("A selected scan is no longer available", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Synthetic catalog film", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(save).toBeEnabled();
  assert.equal(await page.getByText("Media unavailable", { exact: true }).count(), 0);
  const reviews = await send(context, "GET", `${itemRoute}/reviews`);
  assert.equal(reviews.status(), 200);
  assert.ok(!(await reviews.json()).some(review => review.name === name), "Failed create persisted no review");
  await capture(page, run, `scan-draft-failed-save-${width}`);
  await axe(page, `scan-draft-failed-save-${width}`);
  let prompt = null;
  page.once("dialog", async dialog => {
    prompt = { type: dialog.type(), message: dialog.message() };
    await dialog.dismiss();
  });
  // The breadcrumb's library link is visible at every width; narrow windows collapse the primary navigation.
  await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("button", { name: "Synthetic Movies", exact: true }).click();
  assert.deepEqual(prompt, { type: "confirm", message: "Discard unsaved review changes and leave this page?" });
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(save).toBeEnabled();
  await capture(page, run, `scan-draft-retained-${width}`);
  await axe(page, `scan-draft-retained-${width}`);
  run.write(`scan-deleted-shared-draft-${width}.json`, {
    scan: scan.id, deletionStatus: deletion.status(), saveStatus: failure.status(),
    saveCode: "scan_not_found", revalidationStatus: revalidation.status(),
    draftRetained: true, noReviewPersisted: true, navigationPrompt: prompt,
  });
  await page.getByRole("button", { name: "Discard", exact: true }).click();
  await expect(page.getByRole("button", { name: "Discard", exact: true })).toBeDisabled();
}

export async function privateDiagnostics({ run, member, ownerContext, scan, width, send, expect, capture, axe }) {
  const route = "/api/scans/queue";
  const owner = (await (await send(ownerContext, "GET", route)).json()).find(value => value.id === scan.id);
  const ordinary = (await (await send(member.context, "GET", route)).json()).find(value => value.id === scan.id);
  const privatePath = path.join(run.scratch, "unrelated-model-cache", "weights.bin");
  assert.ok(owner.error_message.includes(privatePath), "Owner retains unrelated model-cache diagnostics");
  assert.equal(ordinary.error_message, "Scan interrupted");
  assert.ok(!JSON.stringify(ordinary).includes(run.scratch), "Restricted queue contains no host paths");
  const logFiles = fs.readdirSync(path.join(run.scratch, "logs"), { recursive: true })
    .map(name => path.join(run.scratch, "logs", name)).filter(name => fs.statSync(name).isFile());
  assert.ok(logFiles.some(name => fs.readFileSync(name, "utf8").includes(privatePath)), "Protected logs retain the original exception");
  await member.page.getByRole("button", { name: "Open scans queue and history", exact: true }).click();
  await expect(member.page.getByText("Scan interrupted", { exact: true }).first()).toBeVisible();
  assert.ok(!(await member.page.locator("body").innerText()).includes(run.scratch));
  await capture(member.page, run, `scan-private-diagnostics-${width}`);
  await axe(member.page, `scan-private-diagnostics-${width}`);
  run.write(`scan-private-diagnostics-${width}.json`, {
    scan: scan.id, ordinaryMessage: ordinary.error_message,
    ownerRetainsOriginal: true, protectedLogsRetainOriginal: true, restrictedPathsAbsent: true,
  });
}

export async function mediaRevocation({ run, member, providerState, dropItemCache, resync, title, scanName, width, send, expect, capture, axe }) {
  const { page, context } = member;
  const before = (await (await send(context, "GET", "/api/auth/status")).json()).access;
  assert.ok(Number.isInteger(before.permission_revision));
  const refusal = page.waitForResponse(response => /^\/api\/(media\/audio-tracks|library-items\/[^/]+)$/.test(new URL(response.url()).pathname)
    && response.request().method() === "GET" && response.status() === 404);
  providerState.unavailable = true;
  await dropItemCache();
  // A background review refresh may reread the item first and remove New Scan.
  await page.getByRole("button", { name: "New Scan", exact: true }).click({ timeout: 5000 }).catch(() => {});
  const denied = await refusal;
  assert.equal((await denied.json()).code, "not_found");
  await expect(page.getByText("Media unavailable", { exact: true })).toBeVisible();
  assert.equal(await page.getByRole("heading", { name: title, exact: true }).count(), 0);
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.ok(!(await page.locator("main").innerText()).includes(scanName));
  const after = (await (await send(context, "GET", "/api/auth/status")).json()).access;
  assert.deepEqual(after, before, "Media-only denial does not change principal capabilities or revision");
  await capture(page, run, `scan-media-revoked-${width}`);
  await axe(page, `scan-media-revoked-${width}`);
  const reads = providerState.itemReads;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(() => providerState.itemReads).toBeGreaterThan(reads);
  await expect(page.getByText("Media unavailable", { exact: true })).toBeVisible();
  providerState.unavailable = false;
  const restoredReads = providerState.itemReads;
  // Editarr reuses a linked user's item decisions until that user's next sync.
  await resync();
  // Retry rereads the item's protected routes, which answered 404 while the item was hidden.
  const [reopened] = await Promise.all([
    page.waitForResponse(response => new RegExp(`^/api/library-items/[^/]+(/(scans|reviews))?$`).test(new URL(response.url()).pathname)
      && response.request().method() === "GET" && response.status() === 200),
    page.getByRole("button", { name: "Retry", exact: true }).click(),
  ]);
  await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  const providerReads = providerState.itemReads - restoredReads;
  assert.ok(providerReads > 0, "Restoration rereads the provider item");
  assert.equal(await page.getByRole("dialog").count(), 0);
  await capture(page, run, `scan-media-restored-${width}`);
  await axe(page, `scan-media-restored-${width}`);
  run.write(`scan-media-revocation-${width}.json`, {
    permissionRevision: before.permission_revision, principalUnchanged: true,
    denialStatus: denied.status(), workspaceCleared: true, reopeningRevalidated: true,
    reopeningRead: new URL(reopened.url()).pathname.replace(/^\/api\/library-items\/[^/]+/, "/api/library-items/{id}"), providerItemReadsAfterRestore: providerReads,
  });
}