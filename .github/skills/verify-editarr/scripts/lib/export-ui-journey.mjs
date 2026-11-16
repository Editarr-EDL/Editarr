import assert from "node:assert/strict";
import { accountMenuButton, signOut } from "./account-menu.mjs";
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { query } from "./database.mjs";
import { capture } from "./capture.mjs";
import { FILM, itemRoute, jellyfinTarget } from "./synthetic-jellyfin.mjs";
import { SIGN_IN_PATH, addJellyfinUser, fillSignIn } from "./run-jellyfin.mjs";

const digest = content => createHash("sha256").update(content).digest("hex");
const mcf = (title, start, end) => `WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE ${title}\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n${start} --> ${end}\nviolence=high\n`;
const CUES = { a: [1, 2], b: [2.5, 3.5] };
const MEMBER = "export-member";

/** Parse EDL rows (skipping `#` comments) or MCF cue timings into [start, end] seconds, independent of the app's parsers. */
export function cueTimes(file) {
  const text = fs.readFileSync(file, "utf8");
  if (file.endsWith(".mcf")) {
    assert.match(text, /^WEBVTT MovieContentFilter /, `${file} is an MCF document`);
    const seconds = value => value.split(":").reduce((total, part) => total * 60 + Number(part), 0);
    return [...text.matchAll(/^(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})/gm)].map(match => [seconds(match[1]), seconds(match[2])]);
  }
  return text.split("\n").filter(line => line.trim() && !line.startsWith("#")).map(line => line.trim().split(/\s+/).map(Number)).map(([start, end, action]) => {
    assert.ok(Number.isFinite(start) && Number.isFinite(end) && [0, 1, 3].includes(action), `${file} row is a valid EDL line`);
    return [start, end];
  });
}

function assertCues(file, expected) {
  const cues = cueTimes(file);
  assert.equal(cues.length, 1, `${file} has one cue`);
  assert.ok(Math.abs(cues[0][0] - expected[0]) < 0.05 && Math.abs(cues[0][1] - expected[1]) < 0.05, `${file} cue ${cues[0]} is ${expected}`);
}

const exportRows = run => query(run, "SELECT id, review_id, scope, format, target_user_id, actor_id, policy_owner_id, policy_revision, publication_revision, destination_path FROM media_item_exports WHERE scope != 'legacy' ORDER BY id");

/**
 * Drive the MU-37 export and review-deletion UI as a member and the owner on
 * one synthetic media file, checking every outcome against SQL rows and the
 * bytes on disk rather than badges.
 */
export async function verifyExportUI(run, owner, media, target, provider) {
  const { request, expect } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const { default: AxeBuilder } = createRequire(path.join(run.root, "web/package.json"))("@axe-core/playwright");
  const origin = run.manifest.origin;
  const failures = [];
  const requests = [];
  const evidence = { confirmations: [], observations: [] };
  const apiContexts = [];
  const browserContexts = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  async function actor(username, password) {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    apiContexts.push(context);
    let csrf;
    const send = async (method, url, data, multipart) => {
      const response = await context.fetch(url, { method, headers: csrf ? { "X-CSRF-Token": csrf } : {}, ...(multipart ? { multipart } : data ? { data } : {}) });
      const body = response.status() === 204 ? {} : await response.json();
      if (body.csrf_token) { csrf = body.csrf_token; run.secrets.add(csrf); }
      for (const cookie of (await context.storageState()).cookies) run.secrets.add(cookie.value);
      return { status: response.status(), body };
    };
    if (username) assert.equal((await send("POST", SIGN_IN_PATH, { username, password })).status, 200);
    return send;
  }
  const api = async (send, method, url, data, status = 200, multipart) => {
    const result = await send(method, url, data, multipart);
    assert.equal(result.status, status, `${method} ${url}: ${JSON.stringify(result.body)}`);
    return result.body;
  };
  async function browser(width) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    browserContexts.push(context);
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) { failures.push(`External browser request ${url.origin}`); return route.abort("blockedbyclient"); }
      return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on("pageerror", error => failures.push(error.message));
    page.on("response", response => {
      const url = new URL(response.url());
      if (url.pathname.includes("/exports") || url.pathname.endsWith("/deletion")) requests.push({ method: response.request().method(), path: url.pathname, status: response.status() });
    });
    return page;
  }
  async function signIn(page, username, password, { navigate = true } = {}) {
    active = page;
    const route = itemRoute({ id: target.item_id });
    if (navigate) await page.goto(`${origin}/${route}`);
    await fillSignIn(page, username, password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page)).toBeVisible();
    await page.evaluate(value => { window.location.hash = value; }, route);
    await expect(page.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
  }
  async function switchAccount(page, username, password) {
    await signOut(page);
    await expect(page.getByLabel("Jellyfin username", { exact: true })).toBeVisible();
    await signIn(page, username, password, { navigate: false });
  }
  async function openReview(page, name) {
    const panel = page.locator(".saved-review-panel");
    await panel.getByRole("button", { name: `Open review ${name}`, exact: true }).click();
    await expect(panel.getByRole("heading", { name, exact: true })).toBeVisible();
    return panel;
  }
  async function axe(page, name) {
    const result = await new AxeBuilder({ page }).include(".modal-backdrop").analyze();
    assert.deepEqual(result.violations.map(violation => violation.id), [], `${name} axe`);
  }
  async function exportDialog(page) {
    await page.locator(".saved-review-panel").getByRole("button", { name: "Export", exact: true }).click();
    return page.getByRole("dialog", { name: "Export", exact: true });
  }
  /** Publish one format; confirm a reported replacement when `replace` allows it. */
  async function publish(page, dialog, format, { replace = false } = {}) {
    const upper = format.toUpperCase();
    const post = () => page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/exports/${format}`));
    let [response] = await Promise.all([post(), dialog.getByRole("button", { name: `Publish ${upper}`, exact: true }).click()]);
    if (response.status() === 409) {
      assert.ok(replace, `${upper} unexpectedly needed replacement confirmation`);
      const alert = dialog.getByRole("alert", { name: "Confirm replacement" });
      await expect(alert).toBeVisible();
      evidence.confirmations.push({ format, text: await alert.textContent() });
      [response] = await Promise.all([post(), alert.getByRole("button", { name: `Replace ${upper}`, exact: true }).click()]);
    }
    assert.equal(response.status(), 200, await response.text());
    await expect(dialog.getByRole("status")).toContainText(`Published ${upper} to`);
    return response.json();
  }
  const filesSection = page => page.getByRole("region", { name: "Published files", exact: true });
  const fileRow = (page, name) => filesSection(page).getByRole("listitem", { name, exact: true });
  let active;

  try {
    const admin = await actor(owner.username, owner.password);
    const { password: chosen } = await addJellyfinUser(run, MEMBER);
    const member = await actor(MEMBER, chosen);
    const created = { id: (await api(member, "GET", "/api/auth/status")).access.principal.user_id };
    const reviews = {};
    for (const [key, [start, end]] of Object.entries({ a: ["00:00:01.000", "00:00:02.000"], b: ["00:00:02.500", "00:00:03.500"] })) {
      const imported = await api(member, "POST", "/api/mcf-imports/upload", null, 201, {
        options: JSON.stringify({ target, name: `Export ${key.toUpperCase()}`, synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
        file: { name: `export-${key}.mcf`, mimeType: "text/vtt", buffer: Buffer.from(mcf(`Export ${key}`, start, end)) },
      });
      reviews[key] = imported.review;
    }

    const page = await browser(1440);
    await signIn(page, MEMBER, chosen);
    await openReview(page, "Export A");
    let dialog = await exportDialog(page);
    await expect(dialog.getByRole("radio")).toHaveCount(1);
    await expect(dialog.getByRole("radio", { name: "Your personal files", exact: true })).toBeChecked();
    await expect(dialog.getByText(/Item default/)).toHaveCount(0);
    await axe(page, "member-export-dialog-1440");
    const memberEdlA = await publish(page, dialog, "edl");
    const memberMcfA = await publish(page, dialog, "mcf");
    assert.deepEqual([memberEdlA.target_user_id, memberMcfA.target_user_id, memberEdlA.policy_owner_id], [created.id, created.id, created.id]);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    const rows = () => Object.fromEntries(exportRows(run).map(row => [row.id, row]));
    const memberEdlPath = rows()[memberEdlA.id].destination_path;
    const memberMcfPath = rows()[memberMcfA.id].destination_path;
    assert.equal(path.dirname(path.dirname(memberEdlPath)), path.join(fs.realpathSync(path.dirname(media.file)), "edl"));
    assertCues(memberEdlPath, CUES.a);
    assertCues(memberMcfPath, CUES.a);

    await openReview(page, "Export B");
    dialog = await exportDialog(page);
    const firstAttempt = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/api/reviews/${reviews.b.id}/exports/edl`));
    await dialog.getByRole("button", { name: "Publish EDL", exact: true }).click();
    assert.equal((await (await firstAttempt).json()).detail.code, "export_confirmation_required");
    const confirm = dialog.getByRole("alert", { name: "Confirm replacement" });
    await expect(confirm).toContainText("The current file comes from “Export A” (publication revision 1, published by You).");
    await capture(page, run, "mu37-member-confirm-1440");
    const concurrent = await api(member, "POST", `/api/reviews/${reviews.a.id}/exports/edl`, { revision: reviews.a.revision, policy_owner_id: reviews.a.policy_owner_id, policy_revision: reviews.a.policy_revision, target: "self" });
    assert.equal(concurrent.publication_revision, 2);
    const staleAttempt = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/api/reviews/${reviews.b.id}/exports/edl`));
    await confirm.getByRole("button", { name: "Replace EDL", exact: true }).click();
    const stale = await staleAttempt;
    assert.deepEqual([stale.status(), (await stale.json()).detail.code], [409, "stale_export_confirmation"]);
    const staleAlert = dialog.getByRole("alert", { name: "Replacement changed" });
    await expect(staleAlert).toContainText("nothing was replaced");
    assertCues(memberEdlPath, CUES.a);
    assert.equal(rows()[memberEdlA.id].review_id, reviews.a.id);
    await capture(page, run, "mu37-member-stale-1440");
    await staleAlert.getByRole("button", { name: "Retry EDL", exact: true }).click();
    await expect(confirm).toContainText("(publication revision 2, published by You)");
    const replaced = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/api/reviews/${reviews.b.id}/exports/edl`));
    await confirm.getByRole("button", { name: "Replace EDL", exact: true }).click();
    assert.equal((await replaced).status(), 200);
    await expect(dialog.getByRole("listitem", { name: "EDL at Your personal files" })).toContainText("From this review.");
    await expect(dialog.getByRole("listitem", { name: "MCF at Your personal files" })).toContainText("From “Export A”.");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    assertCues(memberEdlPath, CUES.b);
    assertCues(memberMcfPath, CUES.a);
    assert.deepEqual([rows()[memberEdlA.id].review_id, rows()[memberMcfA.id].review_id], [reviews.b.id, reviews.a.id]);
    for (const review of Object.values(reviews)) await api(member, "GET", `/api/reviews/${review.id}`);
    evidence.replacement = { exportId: memberEdlA.id, stale: 409, staleDiskCue: CUES.a, finalDiskCue: CUES.b, mcfUnchanged: CUES.a, reviewsKept: 2 };

    await page.reload();
    await openReview(page, "Export B");
    await expect(fileRow(page, "EDL · Personal · You")).toContainText("CurrentFrom this review.");
    await expect(fileRow(page, "MCF · Personal · You")).toContainText("From “Export A”.");
    await capture(page, run, "mu37-member-reloaded-1440");

    await switchAccount(page, owner.username, owner.password);
    await openReview(page, "Export B");
    dialog = await exportDialog(page);
    await expect(dialog.getByRole("radio")).toHaveCount(3);
    const ownerTarget = dialog.getByRole("radio", { name: /^Review owner's personal files \(/ });
    await ownerTarget.check();
    await expect(dialog).toContainText(`Filters follow the review owner's enforced policy (${MEMBER}, policy revision ${reviews.b.policy_revision}) for every destination.`);
    await expect(dialog).toContainText(`Jellyfin plays these files only for ${MEMBER}.`);
    await expect(dialog.getByRole("listitem", { name: `EDL at Review owner's personal files (${MEMBER})` })).toContainText("From this review.");
    await expect(dialog.getByRole("listitem", { name: `MCF at Review owner's personal files (${MEMBER})` })).toContainText("From “Export A”.");
    await axe(page, "owner-export-dialog-1440");
    await capture(page, run, "mu37-owner-review-owner-target-1440");
    await dialog.getByRole("radio", { name: "Item default (shared fallback)", exact: true }).check();
    const defaultMcf = await publish(page, dialog, "mcf", { replace: true });
    await dialog.getByRole("radio", { name: "Your personal files", exact: true }).check();
    const ownerEdl = await publish(page, dialog, "edl", { replace: true });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    const stem = path.basename(media.file, path.extname(media.file));
    const mediaDirectory = fs.realpathSync(path.dirname(media.file));
    const stored = rows();
    assert.equal(stored[defaultMcf.id].destination_path, path.join(mediaDirectory, "mcf", `${stem}.mcf`));
    assert.deepEqual([stored[defaultMcf.id].scope, stored[defaultMcf.id].target_user_id, stored[defaultMcf.id].policy_owner_id, stored[defaultMcf.id].actor_id], ["default", null, created.id, stored[ownerEdl.id].actor_id]);
    assert.deepEqual([stored[ownerEdl.id].target_user_id, stored[ownerEdl.id].policy_owner_id, stored[ownerEdl.id].review_id], [stored[ownerEdl.id].actor_id, created.id, reviews.b.id]);
    assert.notEqual(stored[ownerEdl.id].actor_id, created.id);
    assertCues(stored[defaultMcf.id].destination_path, CUES.b);
    assertCues(stored[ownerEdl.id].destination_path, CUES.b);
    for (const name of ["EDL · Personal · You", `EDL · Personal · ${MEMBER}`, `MCF · Personal · ${MEMBER}`, "MCF · Item default"]) {
      await expect(fileRow(page, name).getByRole("button", { name: `Delete ${name}`, exact: true })).toBeVisible();
    }
    const memberEdlBytes = digest(fs.readFileSync(memberEdlPath));
    await fileRow(page, `MCF · Personal · ${MEMBER}`).getByRole("button", { name: `Delete MCF · Personal · ${MEMBER}`, exact: true }).click();
    const [deleted] = await Promise.all([
      page.waitForResponse(response => response.request().method() === "DELETE" && new URL(response.url()).pathname === `/api/exports/${memberMcfA.id}`),
      fileRow(page, `MCF · Personal · ${MEMBER}`).getByRole("button", { name: "Confirm delete MCF", exact: true }).click(),
    ]);
    assert.equal(deleted.status(), 204);
    await expect(fileRow(page, `MCF · Personal · ${MEMBER}`)).toHaveCount(0);
    assert.equal(fs.existsSync(memberMcfPath), false);
    assert.equal(rows()[memberMcfA.id], undefined);
    assert.equal(digest(fs.readFileSync(memberEdlPath)), memberEdlBytes);
    await capture(page, run, "mu37-owner-files-1440");

    await switchAccount(page, MEMBER, chosen);
    await openReview(page, "Export B");
    await expect(filesSection(page).getByRole("listitem")).toHaveCount(1);
    await expect(fileRow(page, "EDL · Personal · You")).toContainText("CurrentFrom this review.");
    await expect(filesSection(page).getByRole("button", { name: /^Delete / })).toHaveCount(1);
    dialog = await exportDialog(page);
    await expect(dialog.getByRole("listitem", { name: "MCF at Your personal files" })).toContainText("No file published here yet.");
    const memberMcfB = await publish(page, dialog, "mcf");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    assertCues(rows()[memberMcfB.id].destination_path, CUES.b);
    const beforeDeletion = rows();

    await page.locator(".saved-review-panel").getByRole("button", { name: "Delete review", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "Delete review", exact: true });
    await expect(deletion.getByRole("group", { name: / · / })).toHaveCount(2);
    await expect(deletion.getByRole("group", { name: "EDL · Personal · You", exact: true }).getByRole("radio", { name: "Keep file" })).toBeChecked();
    await deletion.getByRole("group", { name: "MCF · Personal · You", exact: true }).getByRole("radio", { name: "Remove file" }).check();
    await axe(page, "member-deletion-dialog-1440");
    await capture(page, run, "mu37-member-deletion-1440");
    const [deletionResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/api/reviews/${reviews.b.id}/deletion`)),
      deletion.getByRole("button", { name: "Delete review", exact: true }).click(),
    ]);
    const deletionResult = await deletionResponse.json();
    assert.deepEqual(deletionResult, { review_id: reviews.b.id, retained: [memberEdlA.id], removed: [memberMcfB.id] });
    await expect(filesSection(page).getByRole("status")).toHaveText("Review deleted. Published files kept: 1. Removed: 1.");
    await expect(fileRow(page, "EDL · Personal · You")).toContainText("From a deleted or unavailable review.");
    await api(member, "GET", `/api/reviews/${reviews.b.id}`, null, 404);
    await api(member, "GET", `/api/reviews/${reviews.a.id}`);
    const afterDeletion = rows();
    assert.equal(fs.existsSync(beforeDeletion[memberMcfB.id].destination_path), false);
    assert.equal(afterDeletion[memberMcfB.id], undefined);
    for (const id of [memberEdlA.id, ownerEdl.id, defaultMcf.id]) {
      assert.equal(afterDeletion[id].review_id, null);
      assert.equal(afterDeletion[id].policy_owner_id, created.id);
      assertCues(afterDeletion[id].destination_path, CUES.b);
    }
    assert.equal(digest(fs.readFileSync(memberEdlPath)), memberEdlBytes);
    await capture(page, run, "mu37-member-after-deletion-1440");

    const small = await browser(320);
    await signIn(small, MEMBER, chosen);
    await expect(fileRow(small, "EDL · Personal · You")).toContainText("From a deleted or unavailable review.");
    await capture(small, run, "mu37-member-files-320");
    await openReview(small, "Export A");
    const smallDialog = await exportDialog(small);
    await expect(smallDialog.getByRole("listitem", { name: "EDL at Your personal files" })).toContainText("From a deleted or unavailable review.");
    await axe(small, "member-export-dialog-320");
    await capture(small, run, "mu37-member-export-dialog-320");
    await smallDialog.getByRole("button", { name: "Close", exact: true }).click();

    const policyUrl = `/api/settings/filters/users/${created.id}`;
    const policy = await api(admin, "GET", policyUrl);
    const changed = await api(admin, "PUT", `${policyUrl}/policy`, { policy_revision: policy.policy_revision, rules: [], severities: ["violence"], reauthentication: { password: owner.password } });
    await expect.poll(async () => {
      await small.reload();
      const text = await fileRow(small, "EDL · Personal · You").textContent();
      const status = await member("GET", `/api/exports/${memberEdlA.id}`);
      evidence.observations.push({ ui: text, api: status.body.regeneration_state ?? status.body.detail?.code, policyRevision: status.body.policy_revision });
      if (text.includes("Regenerating")) assert.ok(text.includes("The previous file stays available until regeneration finishes."));
      return text.includes("Current") && text.includes(`Policy: You, revision ${changed.policy_revision}.`);
    }, { timeout: 30000, intervals: [100, 250, 500] }).toBe(true);

    await page.reload();
    await openReview(page, "Export A");
    dialog = await exportDialog(page);
    const memberMcfLast = await publish(page, dialog, "mcf");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page.locator(".saved-review-panel").getByRole("button", { name: "Delete review", exact: true }).click();
    const lastDeletion = page.getByRole("dialog", { name: "Delete review", exact: true });
    await expect(lastDeletion.getByRole("group", { name: / · / })).toHaveCount(1);
    await expect(lastDeletion.getByRole("group", { name: "MCF · Personal · You", exact: true }).getByRole("radio", { name: "Keep file" })).toBeChecked();
    const [lastResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/api/reviews/${reviews.a.id}/deletion`)),
      lastDeletion.getByRole("button", { name: "Delete review", exact: true }).click(),
    ]);
    assert.deepEqual(await lastResponse.json(), { review_id: reviews.a.id, retained: [memberMcfLast.id], removed: [] });
    const retainedIds = [memberEdlA.id, memberMcfLast.id];
    const retainedBytes = Object.fromEntries(retainedIds.map(id => [id, digest(fs.readFileSync(rows()[id].destination_path))]));
    const discovery = `/api/exports?media_item_id=${encodeURIComponent(reviews.a.media_item_id)}&media_source_id=${target.media_source_id}`;
    const retainedNames = ["EDL · Personal · You", "MCF · Personal · You"];
    for (const view of [page, small]) {
      await view.reload();
      await expect(view.locator(".saved-review-panel").getByText("No saved reviews yet.", { exact: true })).toBeVisible();
      await expect(filesSection(view).getByRole("listitem")).toHaveCount(2);
      for (const name of retainedNames) {
        await expect(fileRow(view, name)).toContainText("From a deleted or unavailable review.");
        await expect(fileRow(view, name).getByRole("button", { name: `Delete ${name}`, exact: true })).toBeVisible();
      }
    }
    await capture(page, run, "mu37-member-last-review-deleted-reloaded-1440");
    await capture(small, run, "mu37-member-last-review-deleted-reloaded-320");
    const discovered = (await api(member, "GET", discovery)).publications;
    assert.deepEqual(discovered.map(row => row.id).sort((a, b) => a - b), [...retainedIds].sort((a, b) => a - b));
    const afterLastDeletion = rows();
    for (const row of discovered) {
      assert.deepEqual([row.review_id, row.serving, row.target_user_id], [null, true, created.id]);
      assert.equal(afterLastDeletion[row.id].review_id, null);
      const bytes = digest(fs.readFileSync(afterLastDeletion[row.id].destination_path));
      assert.equal(bytes, retainedBytes[row.id]);
      assert.equal(row.content_sha256, bytes);
    }
    evidence.lastReviewDeletion = { review: reviews.a.id, retained: retainedIds, reloadedViewports: [1440, 320], discovered: discovered.map(row => ({ id: row.id, format: row.format, review_id: row.review_id, serving: row.serving, content_sha256: row.content_sha256 })) };

    const secondFile = path.join(path.dirname(media.file), `zzz-export-second${path.extname(media.file)}`);
    fs.copyFileSync(media.file, secondFile);
    const secondItem = provider.add({ id: "e".repeat(32), version: "f".repeat(32), title: "Synthetic second film" }, secondFile);
    const second = await api(admin, "POST", "/api/edl-imports/upload", null, 201, {
      options: JSON.stringify({ target: jellyfinTarget(secondItem), name: "Owner only second review" }),
      file: { name: "second.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") },
    });
    await page.goto(`${origin}/${itemRoute(secondItem)}`);
    await expect(page.getByRole("heading", { name: secondItem.title, exact: true })).toBeVisible();
    await expect(page.locator(".saved-review-panel").getByText("No saved reviews yet.", { exact: true })).toBeVisible();
    await expect(filesSection(page)).toHaveCount(0);
    await expect(page.locator(".saved-review-panel").getByRole("button", { name: /^Delete (EDL|MCF) · / })).toHaveCount(0);
    assert.deepEqual((await api(member, "GET", `/api/exports?media_item_id=${encodeURIComponent(second.review.media_item_id)}&media_source_id=${secondItem.version}`)).publications, []);
    await api(member, "GET", `/api/reviews/${second.review.id}`, null, 404);
    await capture(page, run, "mu37-member-second-file-1440");
    await page.goto(`${origin}/${itemRoute({ id: target.item_id })}`);
    await expect(page.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
    await expect(filesSection(page).getByRole("listitem")).toHaveCount(2);
    for (const name of retainedNames) await expect(fileRow(page, name)).toContainText("From a deleted or unavailable review.");
    evidence.mediaSwitch = { second: second.review.media_item_id, secondPublications: 0, secondReviewHidden: true, firstRestored: retainedNames };

    assert.equal(digest(fs.readFileSync(media.file)), media.sha256);
    assert.deepEqual(failures, []);
    run.write("export-ui-result.json", {
      passed: true, feature: "MU-37", member: created.id, reviews: { a: reviews.a.id, b: reviews.b.id },
      publications: { memberEdl: memberEdlA.id, memberMcfA: memberMcfA.id, memberMcfB: memberMcfB.id, ownerEdl: ownerEdl.id, defaultMcf: defaultMcf.id },
      deletionResult, beforeDeletion, afterDeletion, policyRevision: changed.policy_revision, viewports: [1440, 320], mediaUnchanged: true, ...evidence,
      scope: "Real source app at 1440 and 320: member and owner export dialogs, replacement confirmation with a concurrent stale change, owner self/review-owner/item-default targets, cross-user delete, account switch and reload, review deletion keep/remove choices, the regeneration state of a retained file, retained files after deleting the last visible review and reloading at both widths, and a switch to a second Jellyfin item with no visible review. Every outcome is checked against SQL rows and parsed disk bytes.",
    });
  } catch (error) {
    if (active && !active.isClosed()) await capture(active, run, "mu37-failure").catch(() => {});
    throw error;
  } finally {
    run.write("export-ui-http-statuses.json", requests);
    run.write("export-ui-browser-errors.json", failures);
    for (const context of browserContexts) {
      for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
      await context.close();
    }
    for (const context of apiContexts) await context.dispose();
  }
}
