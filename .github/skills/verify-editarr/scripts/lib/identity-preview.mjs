import assert from "node:assert/strict";
import { signOut } from "./account-menu.mjs";

/**
 * Preview bytes in one browser context. Dana (linked, Movies) imports an EDL on a Movies
 * item and her page loads its mute preview. The same page then requests the same URL
 * after switching to alice, who can browse Movies but does not own the review, and after
 * a Jellyfin policy change and sync remove dana's Movies. Both refusals must be
 * `not_found` with no audio bytes, including when the browser is told to prefer its cache.
 * Alice, a local user scoped to Movies, then imports onto the same item and plays her own
 * preview, which dana's page cannot read.
 */
export async function checkPreviewIsolation(ui, ctx) {
  const { run, expect, signIn } = ui;
  const { open, allow, ownerContext, adminContext, alice, dana, libraries, item, media, jellyfin, folders } = ctx;
  const origin = run.manifest.origin;
  const mapping = await ui.api(ownerContext, "POST", "/api/settings/path-mappings", { remote_path: "/media", local_path: media });
  const viewer = await open(1440, "dana");
  await signIn(viewer, "dana", dana.password, "profile");
  await expect(viewer.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
  const imported = await importPreview(viewer.context, "0.2 0.8 1\n");
  assert.equal(imported.status, 201, JSON.stringify(imported.body));
  const review = imported.body.review;
  const url = `/api/reviews/${encodeURIComponent(review.id)}/filters/${encodeURIComponent(review.filters[0].id)}/mute-preview`;
  await viewer.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Movies)}/item/${encodeURIComponent(item.id)}`);
  await expect(viewer.page.getByText(item.name, { exact: true }).first()).toBeVisible();

  const allowed = await read(viewer.page, url);
  assert.equal(allowed.status, 200, "dana's page plays her own preview");
  assert.match(allowed.type, /^audio\//); assert.ok(allowed.bytes > 0, "the allowed preview has bytes");
  assert.equal(allowed.cacheControl, "no-store");

  const refused = async (label, reason) => {
    allow(label, url, 404, reason);
    const reads = { network: await read(viewer.page, url), preferCache: await read(viewer.page, url, "force-cache") };
    for (const [mode, value] of Object.entries(reads)) {
      assert.deepEqual([value.status, value.code], [404, "not_found"], `${reason} (${mode})`);
      assert.doesNotMatch(value.type, /^audio\//, `${reason} returns no audio (${mode})`);
      assert.notEqual(value.sha256, allowed.sha256, `${reason} returns none of the preview's bytes (${mode})`);
    }
    return reads;
  };

  await switchTo(viewer, "id-alice", alice.password);
  const afterSwitch = await refused("alice", "alice's identical preview URL after an account switch");
  const localImport = await importPreview(viewer.context, "0.5 0.9 1\n");
  assert.equal(localImport.status, 201, `alice imports onto a Movies item she can open: ${JSON.stringify(localImport.body)}`);
  const [upstream] = (await jellyfin("GET", `/Items?Ids=${encodeURIComponent(item.id)}&Fields=Path,ParentId`)).Items;
  assert.ok(upstream, "Jellyfin lists the preview item");
  assert.equal(upstream.LibraryId ?? null, null, "Jellyfin 12.0.0 omits LibraryId from /Items, so alice's import exercises placement by path. A provider image that returns it no longer covers that path; add a LibraryId-absent case before accepting it");
  const aliceReview = localImport.body.review;
  const aliceUrl = `/api/reviews/${encodeURIComponent(aliceReview.id)}/filters/${encodeURIComponent(aliceReview.filters[0].id)}/mute-preview`;
  const alicePreview = await read(viewer.page, aliceUrl);
  assert.equal(alicePreview.status, 200, "alice's page plays her own preview");
  assert.match(alicePreview.type, /^audio\//); assert.ok(alicePreview.bytes > 0, "alice's preview has bytes");
  assert.equal(alicePreview.cacheControl, "no-store");
  assert.notEqual(alicePreview.sha256, allowed.sha256, "alice's preview window (0.2 s on) differs from dana's (0 to 1 s), so the hash comparisons can tell the two previews apart");
  await switchTo(viewer, "dana", dana.password);
  const danaAgain = await read(viewer.page, url);
  assert.deepEqual([danaAgain.status, danaAgain.sha256], [200, allowed.sha256], "dana's own preview bytes return after switching back");
  allow("dana", aliceUrl, 404, "alice's preview URL in dana's page");
  const aliceRefused = await read(viewer.page, aliceUrl);
  assert.deepEqual([aliceRefused.status, aliceRefused.code], [404, "not_found"], "dana's page cannot read alice's preview");
  assert.notEqual(aliceRefused.sha256, alicePreview.sha256, "dana's page gets none of alice's preview bytes");

  const policy = (await jellyfin("GET", `/Users/${dana.jellyfinId}`)).Policy;
  await jellyfin("POST", `/Users/${dana.jellyfinId}/Policy`, { ...policy, EnabledFolders: [folders.Family] });
  await ui.api(adminContext, "POST", "/api/jellyfin/sync");
  assert.deepEqual((await ui.api(viewer.context, "GET", "/api/libraries")).map(entry => entry.name), ["Family"], "dana's synced policy drops Movies");
  const afterLoss = await refused("dana", "dana's preview after a sync removes Movies");
  await jellyfin("POST", `/Users/${dana.jellyfinId}/Policy`, { ...policy, EnabledFolders: [folders.Movies] });
  await ui.api(adminContext, "POST", "/api/jellyfin/sync");
  assert.deepEqual((await ui.api(viewer.context, "GET", "/api/libraries")).map(entry => entry.name), ["Movies"], "dana's Movies policy is restored");
  await viewer.context.close();
  return { review: review.id, url, mapping: mapping.id, allowed, afterSwitch, afterLoss,
    alice: { review: aliceReview.id, url: aliceUrl, jellyfinLibraryId: upstream.LibraryId ?? null, preview: alicePreview, refusedToDana: aliceRefused } };

  async function importPreview(context, edl) {
    const detail = await ui.api(context, "GET", `/api/library-items/${encodeURIComponent(item.id)}`);
    const auth = await ui.status(context);
    const response = await context.request.post(`${origin}/api/edl-imports/upload`, {
      headers: { Origin: origin, "X-CSRF-Token": auth.csrf_token },
      multipart: {
        options: JSON.stringify({ target: { source: "jellyfin", item_id: item.id, media_source_id: detail.versions[0]?.id ?? null }, name: "Identity preview" }),
        file: { name: "identity.edl", mimeType: "text/plain", buffer: Buffer.from(edl) },
      },
    });
    return { status: response.status(), body: await response.json().catch(() => null) };
  }

  async function switchTo(actor, username, password) {
    await actor.page.goto(`${origin}/#/settings/profile`);
    await signOut(actor.page);
    await expect(actor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
    actor.label = username.replace(/^id-/, "");
    await signIn(actor, username, password, "profile");
    await expect(actor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
  }
}

/** Fetch `url` from the page and describe the response without keeping its bytes. */
function read(page, url, cache = "default") {
  return page.evaluate(async ({ url, cache }) => {
    const response = await fetch(url, { cache });
    const bytes = new Uint8Array(await response.arrayBuffer());
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    let code = null;
    try { code = JSON.parse(new TextDecoder().decode(bytes)).code ?? null; } catch { code = null; }
    return {
      status: response.status, type: response.headers.get("content-type") ?? "", cacheControl: response.headers.get("cache-control"),
      bytes: bytes.length, sha256: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join(""), code,
    };
  }, { url, cache });
}
