import assert from "node:assert/strict";
import { accountMenuButton } from "./account-menu.mjs";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { query } from "./database.mjs";
import { capture } from "./capture.mjs";
import { synthesizeMedia } from "./fixtures.mjs";
import { FILM, itemRoute, jellyfinTarget, probedDuration, startSyntheticJellyfin } from "./synthetic-jellyfin.mjs";
import { verifyAccountDeletion } from "./account-deletion-journey.mjs";
import { verifyExportUI } from "./export-ui-journey.mjs";
import { verifyReviewDeletion } from "./review-deletion-journey.mjs";
import { verifyReviewPolicy } from "./review-policy-journey.mjs";
import { fillSignIn } from "./run-jellyfin.mjs";

export async function verifyReviews(run, owner) {
  const { expect } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const origin = run.manifest.origin;
  const media = run.reviewMedia ?? synthesizeMedia(run.root, run.scratch, run.data);
  run.write("media.json", media);
  const provider = run.provider ?? await startSyntheticJellyfin(run, { ...media, durationSeconds: probedDuration(run, media.file) }, { port: await run.ports.next() });
  let connected = false;
  const digest = file => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  const requests = [];
  const failures = [];
  const contexts = [];
  let context;
  let page;
  let csrf;
  async function remember() {
    for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
  }
  async function open(width) {
    context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push({ width, initialCookies: (await context.cookies()).length });
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) {
        failures.push(`External browser request ${url.origin}`);
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on("pageerror", error => failures.push(error.message));
    page.on("response", response => requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() }));
    await page.goto(`${origin}/${itemRoute(FILM)}`);
    await fillSignIn(page, owner.username, owner.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page)).toBeVisible();
    const status = await (await context.request.get(`${origin}/api/auth/status`)).json();
    csrf = status.csrf_token;
    if (csrf) run.secrets.add(csrf);
    await remember();
    if (!connected) {
      const saved = await context.request.put(`${origin}/api/settings/jellyfin`, { headers: headers(), data: provider.settings });
      assert.equal(saved.status(), 200, await saved.text());
      connected = true;
      await page.reload();
    }
    await expect(page.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
  }
  async function read(reviewId) {
    const response = await context.request.get(`${origin}/api/reviews/${reviewId}`);
    assert.equal(response.status(), 200);
    return response.json();
  }
  const headers = () => ({ Origin: origin, "X-CSRF-Token": csrf });
  try {
    await open(1440);
    await capture(page, run, "reviews-before-1440");
    const imported = await context.request.post(`${origin}/api/edl-imports/upload`, {
      headers: headers(),
      multipart: {
        options: JSON.stringify({ target: jellyfinTarget(FILM), name: "Verification EDL" }),
        file: { name: "verification.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") },
      },
    });
    assert.equal(imported.status(), 201, await imported.text());
    const edl = await imported.json();
    assert.equal((await read(edl.review.id)).filters.length, 1);
    await page.reload();
    const panel = page.locator(".saved-review-panel");
    await panel.getByRole("button", { name: "Open review Verification EDL", exact: true }).click();
    await expect(panel.getByRole("heading", { name: "Verification EDL", exact: true })).toBeVisible();
    await capture(page, run, "reviews-edl-1440");
    await panel.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Import", exact: true });
    await dialog.getByRole("button", { name: "MCF", exact: true }).click();
    const mcf = "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Verification Movie\nYEAR 2026\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n00:00:01.000 --> 00:00:02.000\nviolence=high\n";
    await dialog.getByLabel("MCF file", { exact: true }).setInputFiles({ name: "verification.mcf", mimeType: "text/vtt", buffer: Buffer.from(mcf) });
    await dialog.getByLabel("Import review name").fill("Verification MCF");
    await dialog.getByRole("button", { name: "File end", exact: true }).click();
    assert.ok(Number.isFinite(edl.scan.duration_seconds) && edl.scan.duration_seconds >= 4);
    const fileEnd = new Date(edl.scan.duration_seconds * 1000).toISOString().slice(11, 23);
    await dialog.getByLabel("File END", { exact: true }).fill(fileEnd);
    await dialog.getByLabel("Canonical before-credits END", { exact: true }).fill("00:00:04.000");
    await capture(page, run, "reviews-mcf-before-1440");
    const [mcfResponse] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith("/api/mcf-imports/upload") && response.request().method() === "POST"),
      dialog.getByRole("button", { name: "Import MCF", exact: true }).click(),
    ]);
    assert.equal(mcfResponse.status(), 201, await mcfResponse.text());
    const importedMcf = await mcfResponse.json();
    const reviewId = importedMcf.review.id;
    await expect(panel.getByRole("heading", { name: "Verification MCF", exact: true })).toBeVisible();
    assert.equal((await read(reviewId)).mcf_document.title, "Verification Movie");
    await panel.getByRole("button", { name: "Rename review", exact: true }).click();
    await panel.getByLabel("Review name", { exact: true }).fill("Verification renamed");
    await panel.locator(".saved-review-name-editor").getByRole("button", { name: "Save", exact: true }).click();
    await expect(panel.getByRole("heading", { name: "Verification renamed", exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Duplicate", exact: true }).click();
    const duplicate = page.getByRole("dialog", { name: "Duplicate Review", exact: true });
    await duplicate.getByLabel("Duplicate review name").fill("Verification copy");
    const [copiedResponse] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith(`/api/reviews/${reviewId}/duplicate`)),
      duplicate.getByRole("button", { name: "Duplicate", exact: true }).click(),
    ]);
    assert.equal(copiedResponse.status(), 201);
    const copied = await copiedResponse.json();
    assert.deepEqual((await read(copied.id)).memberships, (await read(reviewId)).memberships);
    await panel.getByRole("button", { name: "Open review Verification renamed", exact: true }).click();
    const sidecars = [];
    for (const format of ["edl", "mcf"]) {
      const legacy = media.file.replace(/\.mkv$/, `.${format}`);
      fs.writeFileSync(legacy, "Preserved legacy sidecar\n", { flag: "wx" });
      await panel.getByRole("button", { name: "Export", exact: true }).click();
      const exportDialog = page.getByRole("dialog", { name: "Export", exact: true });
      const [exported] = await Promise.all([
        page.waitForResponse(response => response.url().endsWith(`/api/reviews/${reviewId}/exports/${format}`) && response.request().method() === "POST"),
        exportDialog.getByRole("button", { name: `Publish ${format.toUpperCase()}`, exact: true }).click(),
      ]);
      assert.equal(exported.status(), 200, await exported.text());
      await expect(exportDialog.getByRole("status")).toHaveText(`Published ${format.toUpperCase()} to Your personal files.`);
      await exportDialog.getByRole("button", { name: "Close", exact: true }).click();
      const [stored] = query(run, "SELECT id, destination_path, content_bytes, snapshot_json, target_export_id, publication_revision FROM media_item_exports WHERE review_id = ? AND format = ? AND scope = 'personal'", reviewId, format);
      const file = path.join(fs.realpathSync(path.dirname(media.file)), format, stored.target_export_id, `${path.basename(media.file, ".mkv")}.user-${stored.target_export_id}.${format}`);
      assert.equal(stored.destination_path, file);
      assert.equal(fs.readFileSync(legacy, "utf8"), "Preserved legacy sidecar\n");
      const detail = await read(reviewId);
      const downloaded = await context.request.post(`${origin}/api/reviews/${reviewId}/exports/${format}/download`, {
        headers: headers(), data: { revision: detail.revision, policy_owner_id: detail.policy_owner_id, policy_revision: detail.policy_revision },
      });
      assert.equal(downloaded.status(), 200, await downloaded.text());
      assert.deepEqual(await downloaded.body(), fs.readFileSync(file));
      assert.deepEqual(Buffer.from(stored.content_bytes), fs.readFileSync(file));
      assert.ok(fs.statSync(file).size > 0);
      sidecars.push({ id: stored.id, publicationId: JSON.parse(stored.snapshot_json).publication_id, revision: stored.publication_revision, format, sha256: digest(file), bytes: fs.statSync(file).size });
      fs.copyFileSync(file, path.join(run.evidence, `verification.${format}`));
    }
    await page.reload();
    await expect(page.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Open review Verification renamed", exact: true }).click();
    await expect(panel.getByRole("heading", { name: "Verification renamed", exact: true })).toBeVisible();
    for (const format of ["EDL", "MCF"]) {
      await expect(panel.getByText(new RegExp(`${format} . Current`), { exact: true }).first()).toBeVisible();
    }
    const current = await read(reviewId);
    const discovery = await context.request.get(`${origin}/api/reviews/${reviewId}/exports`);
    assert.equal(discovery.status(), 200);
    const listed = (await discovery.json()).publications;
    for (const sidecar of sidecars) {
      const publication = listed.find(row => row.id === sidecar.id);
      assert.equal(publication.publication_id, sidecar.publicationId);
      assert.equal(publication.publication_revision, sidecar.revision);
      assert.equal(publication.current, true);
      assert.equal(current.exports.exports[sidecar.format].current, true);
      assert.equal(current.exports.publications.find(row => row.id === sidecar.id).publication_id, sidecar.publicationId);
    }
    run.write("reviews-publications-after-reload.json", { passed: true, publications: listed });
    const blocked = await context.request.delete(`${origin}/api/reviews/${reviewId}?revision=${current.revision}`, { headers: headers() });
    assert.equal(blocked.status(), 409);
    assert.equal((await blocked.json()).detail.code, "published_review_retention_required");
    assert.equal((await read(reviewId)).name, "Verification renamed");
    assert.equal(digest(media.file), media.sha256);
    await capture(page, run, "reviews-after-1440");
    await remember();
    await context.close();
    await open(320);
    await page.locator(".saved-review-panel").getByRole("button", { name: "Open review Verification renamed", exact: true }).click();
    await expect(page.locator(".saved-review-panel").getByRole("heading", { name: "Verification renamed", exact: true })).toBeVisible();
    assert.equal((await read(reviewId)).filters.length, 1);
    for (const format of ["EDL", "MCF"]) {
      await expect(page.locator(".saved-review-panel").getByText(new RegExp(`${format} . Current`), { exact: true }).first()).toBeVisible();
    }
    await capture(page, run, "reviews-reopened-320");
    for (const sidecar of sidecars) {
      const removed = await context.request.delete(`${origin}/api/exports/${sidecar.id}?revision=${sidecar.revision}&publication_id=${sidecar.publicationId}`, { headers: headers() });
      assert.equal(removed.status(), 204, await removed.text());
    }
    await verifyReviewPolicy(run, owner, media, edl.review.target);
    await verifyReviewDeletion(run, owner, media, edl.review.target);
    await verifyExportUI(run, owner, media, edl.review.target, provider);
    if (run.accountDeletion) await verifyAccountDeletion(run, owner, media, edl.review.target);
    assert.deepEqual(failures, []);
    run.write("reviews-result.json", { passed: true, features: ["import-edl", "import-mcf", "import-persistence", "review-isolation", "export-edl", "export-mcf", "export-reload-discovery"], viewports: [1440, 320], contexts, sidecars, mediaUnchanged: true, publishedReviewRetained: true, scope: "A Jellyfin item from the labelled synthetic Jellyfin transport through the real source app. Two-user and live-provider adapters require separate evidence." });
  } catch (error) {
    if (page && !page.isClosed()) await capture(page, run, "reviews-failure").catch(() => {});
    throw error;
  } finally {
    run.write("reviews-http-statuses.json", requests);
    run.write("reviews-browser-errors.json", failures);
    if (context) {
      if (run.jellyfin) {
        const restored = await context.request.put(`${origin}/api/settings/jellyfin`, { headers: headers(), data: { server_url: run.jellyfin.base, api_key: (await run.jellyfin.editarrKeys())[0] } });
        assert.equal(restored.status(), 200, "the run's Jellyfin is connected again for later journeys");
      }
      await remember().catch(() => {});
      await context.close();
    }
    await provider.close();
  }
}