import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { childEnvironment, missingPrerequisites, pythonPath } from "./prerequisites.mjs";
import { usersUI } from "./users-ui-support.mjs";
import { addJellyfinUser } from "./run-jellyfin.mjs";
import { checkIdentityUI } from "./identity-ui.mjs";
import { checkPreviewIsolation } from "./identity-preview.mjs";
import { browserFailures } from "./identity-browser-failures.mjs";

/**
 * What Editarr must list for the owner from media_access_live_drive's LAYOUT: five
 * libraries, one entry per movie, series, or home video. Songs holds only audio, which
 * the catalog does not list.
 */
const FIXTURE = {
  Family: ["Film C (2003) [tmdbid-9003]"],
  Home: ["Clip"],
  Movies: ["Film A (2001) [tmdbid-9001]", "Film B (2002) [tmdbid-9002]", "Film E (2005) [tmdbid-9005]", "Film F (2006) [tmdbid-9006]", "Film G (2007) [tmdbid-9007]"],
  Shows: ["Show X"],
  Songs: [],
};
const NOT_FOUND = [404, "not_found"];

/** List the identity journey's gaps (FFmpeg for previews, Docker, and the pinned image) before anything launches. */
export function identityPrerequisites(root) {
  const media = missingPrerequisites("media", root);
  if (media.length) return media;
  const scripts = path.join(root, ".github/skills/verify-editarr/scripts");
  try {
    const image = execFileSync(pythonPath(root), ["-B", "-c", "from media_access_live_docker import IMAGE; print(IMAGE)"], { cwd: scripts, env: { ...childEnvironment(root), PYTHONPATH: `${root}${path.delimiter}${scripts}` }, encoding: "utf8" }).trim();
    try { execFileSync("docker", ["image", "inspect", image], { stdio: "ignore", timeout: 30000 }); return []; }
    catch { return [{ requirement: "jellyfinImage", detail: `docker cannot inspect ${image}`, remedy: "Start Docker and provide the digest-pinned Jellyfin 12.0.0 image." }]; }
  } catch (error) { return [{ requirement: "jellyfinImage", detail: `cannot read the pinned image: ${String(error.message).split("\n")[0]}`, remedy: "Provision this checkout's .venv." }]; }
}

/**
 * Six actors against one real Editarr run and a disposable pinned Jellyfin 12.0.0
 * (external boundary): owner and five Jellyfin users whose Jellyfin library policies
 * decide their media: the admin and bob (Family), alice and dana (Movies), and casey
 * (Family and Home). Proves per-actor counts, hidden versus
 * missing errors, artwork, deep links, API keys, and in-session scope and sync
 * changes through HTTP, then hands the browser checks to identity-ui.mjs.
 */
export async function verifyIdentity(run, owner) {
  const ui = usersUI(run);
  const { record, api, signIn } = ui;
  const result = { actors: {}, libraries: {}, matrix: {}, refusals: {}, provider: null, browser: null };
  const browser = browserFailures(), allowances = [];
  const allow = (label, pathname, status, reason, detail = null, code = "not_found") => allowances.push({ label, path: String(pathname), status, reason, detail, code });
  const open = async (width, label) => {
    const actor = await ui.open(width);
    actor.label = label;
    const recorded = browser.watch(actor);
    const close = actor.context.close.bind(actor.context);
    actor.context.close = async () => { await recorded(); return close(); };
    return actor;
  };
  try {
    const ownerActor = await open(1440, "owner");
    await signIn(ownerActor, owner.username, owner.password, "users");
    await ui.expect(ownerActor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
    const jf = { ...run.jellyfin.libraries, base: run.jellyfin.base, image: run.jellyfin.image, run: run.jellyfin.run };
    const port = run.jellyfin.port;
    result.provider = { base: jf.base, image: jf.image, run: jf.run, port, folders: Object.keys(jf.folders).sort(), setUpThrough: "first-run Jellyfin setup" };
    assert.equal((await api(ownerActor.context, "GET", "/api/settings/jellyfin")).server_url, jf.base, "setup stored the run's Jellyfin");
    await api(ownerActor.context, "GET", "/api/settings/jellyfin-admission", undefined, 404);
    record("identity: labelled pinned Jellyfin configured and no admission mode setting exists");

    const fresh = { reauthentication: { password: owner.password } };
    const libraries = Object.fromEntries((await api(ownerActor.context, "GET", "/api/libraries")).map(entry => [entry.name, entry.id]));
    const all = Object.keys(FIXTURE).sort();
    assert.deepEqual(Object.keys(libraries).sort(), all, "owner lists exactly the five fixture libraries");
    const restrict = async (user, names) => {
      const { Policy } = await jellyfin(jf, "GET", `/Users/${user.jellyfinId}`);
      await jellyfin(jf, "POST", `/Users/${user.jellyfinId}/Policy`, { ...Policy, EnableAllFolders: false, EnabledFolders: names.map(name => jf.folders[name]) });
    };
    const locals = {};
    for (const [username, role, folders] of [["id-admin", "admin", ["Family"]], ["id-alice", "user", ["Movies"]], ["id-bob", "user", ["Family"]]]) {
      const user = await addJellyfinUser(run, username);
      await restrict(user, folders);
      const actor = await open(1440, username.replace(/^id-/, ""));
      await signIn(actor, username, user.password);
      await ui.expect(actor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
      const { user_id: id } = (await ui.status(actor.context)).access.principal;
      if (role === "admin") await api(ownerActor.context, "PUT", `/api/users/${id}/role`, { role, ...fresh });
      locals[username] = { id, password: user.password, jellyfinId: user.jellyfinId, actor };
    }
    const linked = {};
    for (const name of ["casey", "dana"]) {
      const actor = await open(1440, name);
      await signIn(actor, name, jf.users[name].password);
      await ui.expect(actor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
      const status = await ui.status(actor.context);
      assert.equal(status.access.principal.link_state, "verified", `${name} signs in linked and verified`);
      linked[name] = { id: status.access.principal.user_id, password: jf.users[name].password, actor };
    }
    record("identity: owner and five Jellyfin users with their own library policies, each admitted by first sign-in, are signed in separately");
    result.firstLibraryReads = {};
    for (const [name, { actor }] of Object.entries(linked)) {
      const read = await raw(actor.context, "GET", "/api/libraries");
      result.firstLibraryReads[name] = { status: read.status, body: read.status === 200 ? read.body.map(entry => entry.name).sort() : read.body };
    }
    run.write("identity-result.json", result);
    for (const [name, read] of Object.entries(result.firstLibraryReads)) assert.equal(read.status, 200, `${name}'s first library read after sign-in succeeds without a retry`);

    const actors = {
      owner: { context: ownerActor.context, expected: all },
      admin: { context: locals["id-admin"].actor.context, expected: ["Family"] },
      alice: { context: locals["id-alice"].actor.context, expected: ["Movies"] },
      bob: { context: locals["id-bob"].actor.context, expected: ["Family"] },
      casey: { context: linked.casey.actor.context, expected: ["Family", "Home"] },
      dana: { context: linked.dana.actor.context, expected: ["Movies"] },
    };
    const pages = {};
    for (const name of all) {
      pages[name] = await api(ownerActor.context, "GET", `/api/libraries/${encodeURIComponent(libraries[name])}/items`);
      assert.deepEqual([pages[name].total_count, pages[name].items.map(item => item.name).sort()], [FIXTURE[name].length, FIXTURE[name].slice().sort()], `owner's ${name} lists the fixture items`);
    }
    result.libraries = Object.fromEntries(all.map(name => [name, { id: libraries[name], total: pages[name].total_count, names: pages[name].items.map(item => item.name) }]));
    const missing = async context => ({
      library: await raw(context, "GET", "/api/libraries/00000000000000000000000000000000/items"),
      item: await raw(context, "GET", "/api/library-items/00000000000000000000000000000000"),
    });
    const probe = pages.Movies.items.find(item => item.name === FIXTURE.Movies[0]);
    const missingArtwork = await raw(ownerActor.context, "GET", probe.image_url);
    result.missingArtwork = { item: probe.id, status: missingArtwork.status, body: missingArtwork.body };
    assert.deepEqual([missingArtwork.status, missingArtwork.body], [404, { detail: "No primary image found" }], "Film A has no artwork, and its 404 carries that detail and no code");
    for (const label of ["owner", "alice", "dana"]) allow(label, `/api/library-items/${encodeURIComponent(probe.id)}/image`, 404, "an item page for a Movies item without artwork", "No primary image found", null);
    const missingLibraries = {};
    for (const [name, actor] of Object.entries(actors)) {
      const visible = (await api(actor.context, "GET", "/api/libraries")).map(entry => entry.name).sort();
      assert.deepEqual(visible, actor.expected.slice().sort(), `${name} lists only its libraries`);
      const row = { libraries: visible, totals: {}, hidden: {} };
      const { library: missingLibrary, item: missingItem } = await missing(actor.context);
      row.missing = [missingLibrary.status, missingItem.status];
      assert.deepEqual([missingItem.status, missingItem.body?.code], NOT_FOUND, `${name}'s missing item is not_found`);
      assert.deepEqual([missingLibrary.status, missingLibrary.body?.code], NOT_FOUND, `${name}'s missing library is not_found`);
      missingLibraries[name] = { status: missingLibrary.status, body: missingLibrary.body };
      for (const library of all) {
        const listing = await raw(actor.context, "GET", `/api/libraries/${encodeURIComponent(libraries[library])}/items`);
        if (actor.expected.includes(library)) {
          assert.equal(listing.status, 200, `${name} reads ${library}`);
          assert.equal(listing.body.total_count, FIXTURE[library].length, `${name} counts the fixture's ${library} total`);
          assert.deepEqual(listing.body.items.map(item => item.name).sort(), FIXTURE[library].slice().sort(), `${name} lists the fixture's ${library} items`);
          if (!["owner", "admin"].includes(name)) assert.ok(listing.body.items.every(item => !item.path), `${name} sees no server paths`);
          row.totals[library] = listing.body.total_count;
        } else {
          assert.deepEqual([listing.status, listing.body?.code], NOT_FOUND, `${name} ${library} is refused as not_found`);
          assert.equal(listing.status, missingLibrary.status, `${name} ${library} refusal matches a missing library`);
          assert.deepEqual(listing.body, missingLibrary.body, `${name} ${library} body matches a missing library`);
          row.hidden[library] = listing.status;
        }
      }
      const item = await raw(actor.context, "GET", `/api/library-items/${encodeURIComponent(probe.id)}`);
      if (actor.expected.includes("Movies")) assert.equal(item.status, 200, `${name} opens a Movies item`);
      else {
        assert.deepEqual([item.status, item.body?.code], NOT_FOUND, `${name} hidden Movies item is not_found`);
        assert.deepEqual([item.status, item.body], [missingItem.status, missingItem.body], `${name} hidden Movies item matches a missing item`);
      }
      row.moviesItem = item.status;
      result.matrix[name] = row;
      run.write("identity-result.json", result);
    }
    for (const [name, read] of Object.entries(missingLibraries)) assert.deepEqual(read, missingLibraries.owner, `${name}'s missing library, and so each hidden one, answers exactly like the owner's missing library`);
    result.missingLibrary = missingLibraries.owner;
    record("identity: six actors list exactly their fixture libraries and items, and hidden libraries and items answer not_found like missing ones, the owner's missing library included");

    const artwork = pages.Family.items[0];
    const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    await jellyfin(jf, "POST", `/Items/${artwork.id}/Images/Primary`, png, "image/png");
    result.artwork = { item: artwork.id, uploaded: "1x1 PNG through Jellyfin's image API", attempts: [] };
    await ui.expect.poll(async () => {
      const read = await raw(ownerActor.context, "GET", artwork.image_url);
      result.artwork.attempts.push(read.status);
      return read.status;
    }, { timeout: 60000, intervals: [2000] }).toBe(200);
    result.artwork = { ...result.artwork, ...await artworkChecks(actors, artwork) };
    record("identity: artwork is private, no-store, varies by credential, and a restricted actor's identical URL answers 404");

    const users = await raw(actors.alice.context, "GET", "/api/users");
    const adminUsers = await raw(actors.admin.context, "GET", "/api/users");
    const ownerOnly = await raw(actors.admin.context, "PATCH", `/api/users/${locals["id-bob"].id}/permissions`, { overrides: { "media.browse": "deny" }, reauthentication: { password: locals["id-admin"].password } });
    const keyIssue = await raw(actors.alice.context, "POST", "/api/auth/api-key", { reauthentication: { password: locals["id-alice"].password } });
    assert.equal(keyIssue.status, 200, "alice issues her own key with fresh authentication");
    run.secrets.add(keyIssue.body.api_key);
    const keyed = async (method, pathname, data) => { const response = await fetch(run.manifest.origin + pathname, { method, headers: { "X-Api-Key": keyIssue.body.api_key, ...(data ? { "Content-Type": "application/json" } : {}) }, ...(data ? { body: JSON.stringify(data) } : {}) }); return { status: response.status, body: await response.json().catch(() => null) }; };
    const keyRefusals = {
      users: await keyed("GET", "/api/users"),
      hiddenItem: await keyed("GET", `/api/library-items/${encodeURIComponent(pages.Family.items[0].id)}`),
      reveal: await keyed("POST", "/api/auth/api-key/reveal", { reauthentication: { password: locals["id-alice"].password } }),
      ownItem: await keyed("GET", `/api/library-items/${encodeURIComponent(probe.id)}`),
    };
    result.refusals = { aliceUsers: users.status, adminUsers: adminUsers.status, adminOwnerOnly: [ownerOnly.status, ownerOnly.body?.code], key: Object.fromEntries(Object.entries(keyRefusals).map(([name, value]) => [name, [value.status, value.body?.code ?? null]])) };
    assert.equal(users.status, 403); assert.equal(adminUsers.status, 200); assert.equal(ownerOnly.status, 403);
    assert.equal(keyRefusals.users.status, 403); assert.equal(keyRefusals.ownItem.status, 200);
    const aliceMissing = await missing(actors.alice.context);
    assert.deepEqual([keyRefusals.hiddenItem.status, keyRefusals.hiddenItem.body], [aliceMissing.item.status, aliceMissing.item.body]);
    assert.deepEqual([keyRefusals.reveal.status, keyRefusals.reveal.body?.code], [403, "interactive_session_required"]);
    record("identity: direct API refusals hold for user, admin owner-only, and a key's directory, hidden media, and interactive-only reveal");

    result.preview = await checkPreviewIsolation(ui, {
      open, allow, ownerContext: ownerActor.context, adminContext: locals["id-admin"].actor.context, alice: locals["id-alice"], dana: { ...linked.dana, jellyfinId: jf.users.dana.id },
      libraries, item: probe, media: jf.media, folders: jf.folders, jellyfin: (method, pathname, data) => jellyfin(jf, method, pathname, data),
    });
    run.write("identity-result.json", result);
    record("identity: dana's page loads her preview bytes, and the same URL in the same context is not_found with no bytes after switching to alice and after a sync removes dana's Movies");

    await restrict(locals["id-bob"], []);
    const danaUser = await jellyfin(jf, "GET", `/Users/${jf.users.dana.id}`);
    await jellyfin(jf, "POST", `/Users/${jf.users.dana.id}/Policy`, { ...danaUser.Policy, EnabledFolders: [jf.folders.Movies, jf.folders.Family] });
    assert.deepEqual((await api(actors.dana.context, "GET", "/api/libraries")).map(entry => entry.name).sort(), ["Movies"], "an upstream change waits for a sync");
    assert.deepEqual((await api(actors.bob.context, "GET", "/api/libraries")).map(entry => entry.name).sort(), ["Family"], "an upstream removal waits for a sync");
    const sync = await api(locals["id-admin"].actor.context, "POST", "/api/jellyfin/sync");
    assert.deepEqual(await api(actors.bob.context, "GET", "/api/libraries"), [], "bob's existing session loses media at its next request");
    assert.equal((await raw(actors.bob.context, "GET", `/api/libraries/${encodeURIComponent(libraries.Family)}/items`)).status, (await missing(actors.bob.context)).library.status);
    assert.deepEqual((await api(actors.dana.context, "GET", "/api/libraries")).map(entry => entry.name).sort(), ["Family", "Movies"], "dana's existing session sees the synced policy");
    assert.equal((await raw(actors.alice.context, "POST", "/api/jellyfin/sync")).status, 403, "an ordinary user cannot start a sync");
    result.inSession = { bobAfterScope: [], danaAfterSync: ["Family", "Movies"], syncCounts: sync.counts ?? sync };
    actors.bob.expected = []; actors.dana.expected = ["Family", "Movies"];
    record("identity: Jellyfin policy changes reach existing sessions at an admin-run Jellyfin sync, without sign-in");

    const sessions = { owner: ownerActor, admin: locals["id-admin"].actor, alice: locals["id-alice"].actor, bob: locals["id-bob"].actor, casey: linked.casey.actor, dana: linked.dana.actor };
    result.ui = await checkIdentityUI({ ...ui, open }, { owner, locals, linked, actors, sessions, libraries, pages, artwork, probe, allow });
    result.actors = Object.fromEntries([...Object.entries(locals), ...Object.entries(linked)].map(([name, value]) => [name, value.id]));
    record("identity: browser checks passed at 1440 and 320");
    await browser.settled();
    result.browser = browser.outcome(allowances, ui.errors);
    run.write("identity-result.json", result);
    assert.deepEqual(result.browser.pageErrors, [], "no browser page errors");
    assert.deepEqual(result.browser.unexpected, [], "every browser API failure was an expected refusal");
    record("identity: every browser API failure was a labelled expected refusal and no page raised an error");
  } finally {
    if (!result.browser) { await browser.settled(); result.browser = browser.outcome(allowances, ui.errors); }
    run.write("identity-result.json", result);
    await ui.close();
  }

  async function raw(context, method, pathname, data) {
    const auth = await ui.status(context);
    const response = await context.request.fetch(run.manifest.origin + pathname, { method, data, headers: { Origin: run.manifest.origin, ...(method === "GET" ? {} : { "X-CSRF-Token": auth.csrf_token }) } });
    ui.requests.push({ method, path: pathname, status: response.status() });
    return { status: response.status(), headers: response.headers(), body: await response.json().catch(() => null) };
  }

  async function artworkChecks(actors, item) {
    const owner = await raw(actors.owner.context, "GET", item.image_url);
    const dana = await raw(actors.dana.context, "GET", item.image_url);
    assert.equal(owner.status, 200, "owner loads the artwork");
    assert.match(owner.headers["cache-control"] ?? "", /private/); assert.match(owner.headers["cache-control"] ?? "", /no-store/);
    assert.match(owner.headers.vary ?? "", /cookie/i);
    assert.equal(dana.status, 404, "dana's identical artwork URL is 404");
    return { item: item.id, owner: owner.status, dana: dana.status, cacheControl: owner.headers["cache-control"], vary: owner.headers.vary };
  }
}

async function jellyfin(jf, method, pathname, data, type = "application/json") {
  const body = data === undefined ? undefined : type === "application/json" ? JSON.stringify(data) : data;
  const response = await fetch(jf.base + pathname, { method, body, signal: AbortSignal.timeout(60000), headers: { Authorization: `MediaBrowser Client="editarr-identity", Device="verify", DeviceId="identity", Version="1", Token="${jf.key}"`, ...(body ? { "Content-Type": type } : {}) } });
  assert.ok(response.ok, `Jellyfin ${method} ${pathname} ${response.status}`);
  const text = await response.text(); return text ? JSON.parse(text) : null;
}
