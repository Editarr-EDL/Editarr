import assert from "node:assert/strict";
import { signOut } from "./account-menu.mjs";

/**
 * Browser half of the identity journey: per-actor library views, denied deep links,
 * keyboard sign-in and navigation, account switching in one context, a fresh sign-in
 * after bob's media scope was removed, and delayed and failed catalog loads, at 1440
 * and 320 pixels. `ctx` comes from identity-journey.mjs after its HTTP checks pass;
 * `ctx.allow` registers each refusal these pages are expected to receive.
 */
export async function checkIdentityUI(ui, ctx) {
  const { run, expect, open, signIn, save, checkAxe, record } = ui;
  const origin = run.manifest.origin;
  const { locals, linked, libraries, pages, allow } = ctx;
  const { probe } = ctx;
  const moviesNames = pages.Movies.items.map(item => item.name);
  const familyName = pages.Family.items[0].name;
  const library = name => `/api/libraries/${encodeURIComponent(libraries[name])}`;
  allow("casey", library("Movies"), 404, "casey's Movies deep link");
  allow("casey", `/api/library-items/${encodeURIComponent(probe.id)}`, 404, "casey's Movies item deep link");
  allow("alice", library("Family"), 404, "alice's Family deep link");
  allow("bob", library("Family"), 404, "bob's Family deep link after scope removal");
  allow("bob", `/api/library-items/${encodeURIComponent(pages.Family.items[0].id)}`, 404, "bob's former Family item, reloaded after scope removal");
  allow("alice", `/api/library-items/${encodeURIComponent(ctx.artwork.id)}`, 404, "the owner's Family artwork URL fetched after switching to alice");
  const evidence = { reloads: {}, widths: {}, keyboard: null, switch: null, states: null };
  const aria = page => page.locator("body").ariaSnapshot();
  const absent = async (page, names, label) => {
    const text = await aria(page);
    for (const name of names) assert.ok(!text.includes(name), `${label} never renders ${name}`);
  };

  for (const [name, actor] of Object.entries(ctx.sessions)) evidence.reloads[name] = await reloadPersists(name, actor, ctx.actors[name].expected);
  record("identity UI: owner, admin, alice, bob, casey, and dana each reload into the same account, scope, stored route, and libraries");

  for (const width of [1440, 320]) {
    const row = {};
    const casey = await open(width, "casey");
    await keyboardSignIn(casey.page, "casey", linked.casey.password);
    await casey.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Family)}`);
    await expect(casey.page.getByText(familyName, { exact: true }).first()).toBeVisible();
    await absent(casey.page, moviesNames, `casey Family ${width}`);
    assert.equal(await casey.page.getByRole("button", { name: "Movies", exact: true }).count(), 0, "casey has no Movies navigation");
    await save(casey.page, `identity-casey-family-${width}`); await checkAxe(casey.page, `identity-casey-family-${width}`);
    await casey.page.reload();
    await expect(casey.page.getByText(familyName, { exact: true }).first()).toBeVisible();
    await absent(casey.page, moviesNames, `casey Family after reload ${width}`);
    if (width === 1440) {
      await tabTo(casey.page, casey.page.getByRole("button", { name: "Home", exact: true }).first(), 80);
      await casey.page.keyboard.press("Enter");
      await expect.poll(() => casey.page.evaluate(() => window.location.hash)).toContain(encodeURIComponent(libraries.Home));
      evidence.keyboard = { signIn: "Tab, type, Enter", navigation: "Tab to Home, Enter", hash: await casey.page.evaluate(() => window.location.hash) };
    }
    await casey.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Movies)}/item/${encodeURIComponent(probe.id)}`);
    await casey.page.waitForLoadState("networkidle");
    await expect(casey.page.getByRole("heading", { name: "Library unavailable", exact: true })).toBeVisible();
    await absent(casey.page, [probe.name, ...moviesNames], `casey Movies deep link ${width}`);
    await save(casey.page, `identity-casey-denied-${width}`); await checkAxe(casey.page, `identity-casey-denied-${width}`);
    row.caseyDenied = (await aria(casey.page)).split("\n").filter(line => /heading|alert|status/.test(line)).slice(0, 6);

    const alice = await open(width, "alice");
    await signIn(alice, "id-alice", locals["id-alice"].password);
    await alice.page.goto(`${origin}/#/settings/users`);
    await expect(alice.page.getByRole("heading", { name: "Page unavailable" })).toBeVisible();
    assert.equal(await alice.page.getByRole("button", { name: "Users", exact: true }).count(), 0, "alice has no Users control");
    await alice.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Family)}`);
    await alice.page.waitForLoadState("networkidle");
    await expect(alice.page.getByRole("heading", { name: "Library unavailable", exact: true })).toBeVisible();
    await absent(alice.page, [familyName], `alice Family deep link ${width}`);
    await save(alice.page, `identity-alice-denied-${width}`); await checkAxe(alice.page, `identity-alice-denied-${width}`);

    const bob = await open(width, "bob");
    await signIn(bob, "id-bob", locals["id-bob"].password);
    await bob.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Family)}`);
    await bob.page.waitForLoadState("networkidle");
    await expect(bob.page.getByRole("heading", { name: "Library unavailable", exact: true })).toBeVisible();
    await absent(bob.page, [familyName, ...moviesNames], `bob after scope removal ${width}`);
    assert.equal(await bob.page.getByRole("button", { name: "Family", exact: true }).count(), 0, "bob's revoked library leaves navigation");
    await save(bob.page, `identity-bob-revoked-${width}`);
    evidence.widths[width] = row;
    for (const actor of [casey, alice, bob]) await actor.context.close();
  }
  record("identity UI: linked, local, and revoked actors see only their libraries and denied deep links render nothing hidden at 1440 and 320");

  const shared = await open(1440, "owner");
  await signIn(shared, ctx.owner.username, ctx.owner.password, "profile");
  await shared.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Movies)}`);
  await shared.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Movies)}/item/${encodeURIComponent(probe.id)}`);
  await expect(shared.page.getByText(probe.name, { exact: true }).first()).toBeVisible();
  const pageFetch = url => shared.page.evaluate(async target => {
    const response = await fetch(target);
    const text = await response.text();
    let code = null;
    try { code = JSON.parse(text).code ?? null; } catch { code = null; }
    return { status: response.status, code };
  }, url);
  const artworkAsOwner = await pageFetch(ctx.artwork.image_url);
  assert.equal(artworkAsOwner.status, 200, "the owner's page loads Family artwork");
  const ownerKeys = await storage(shared.page);
  const ownerValues = Object.entries(ownerKeys).filter(([key]) => key.startsWith("editarr.user."));
  assert.ok(ownerValues.some(([key]) => key.includes("library-last-route")), "the owner's last library route is stored");
  await shared.page.goto(`${origin}/#/settings/profile`);
  await signOut(shared.page);
  await expect(shared.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  shared.label = "alice";
  await signIn(shared, "id-alice", locals["id-alice"].password, "profile");
  await expect.poll(async () => (await storage(shared.page))["editarr.user-scope"]?.split(":")[0], { message: "storage is bound to alice" }).toBe(String(locals["id-alice"].id));
  const aliceKeys = await storage(shared.page);
  for (const [key, value] of ownerValues) assert.notEqual(aliceKeys[key], value, `alice inherits no owner value ${key}`);
  const artworkAsAlice = await pageFetch(ctx.artwork.image_url);
  assert.deepEqual([artworkAsAlice.status, artworkAsAlice.code], [404, "not_found"], "the same browser cache serves alice no Family artwork");
  await save(shared.page, "identity-switch-alice-1440");
  await shared.page.reload();
  await expect(shared.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
  assert.equal((await ui.status(shared.context)).access.principal.user_id, locals["id-alice"].id, "the switched page reloads as alice");
  const reloadedKeys = await storage(shared.page);
  assert.equal(reloadedKeys["editarr.user-scope"], aliceKeys["editarr.user-scope"], "alice's storage scope survives a reload");
  for (const [key, value] of ownerValues) assert.notEqual(reloadedKeys[key], value, `the owner's ${key} stays gone after a reload`);
  const artworkAfterReload = await pageFetch(ctx.artwork.image_url);
  assert.deepEqual([artworkAfterReload.status, artworkAfterReload.code], [404, "not_found"], "the reloaded page still serves alice no Family artwork");
  evidence.switch = { ownerKeys: Object.keys(ownerKeys).sort(), aliceKeys: Object.keys(aliceKeys).sort(), reloadedKeys: Object.keys(reloadedKeys).sort(), artwork: { owner: artworkAsOwner, alice: artworkAsAlice, aliceAfterReload: artworkAfterReload } };
  await shared.context.close();
  record("identity UI: switching from owner to alice in one context clears the owner's stored routes, binds storage to alice, and refuses the owner's cached artwork URL, before and after a reload");

  const states = await open(1440, "casey");
  await signIn(states, "casey", linked.casey.password, "profile");
  const listing = `**/api/libraries/${encodeURIComponent(libraries.Family)}/items**`;
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await states.context.route(listing, async route => { await held; await route.continue(); });
  await states.page.goto(`${origin}/#/library/${encodeURIComponent(libraries.Family)}`);
  await expect(states.page.getByRole("button", { name: "Refresh Library", exact: true })).toBeDisabled();
  await save(states.page, "identity-loading-1440");
  const loading = await aria(states.page);
  assert.ok(!loading.includes(familyName), "loading state shows no items yet");
  release();
  await expect(states.page.getByText(familyName, { exact: true }).first()).toBeVisible();
  await states.context.unroute(listing);
  await states.context.route(listing, route => route.abort("failed"));
  await states.page.reload();
  await expect(states.page.getByRole("alert").getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  await save(states.page, "identity-error-1440");
  const failed = await aria(states.page);
  await absent(states.page, moviesNames, "casey catalog error state");
  const main = text => text.slice(text.indexOf("- main:")).split("\n").slice(0, 6);
  evidence.states = { loading: main(loading), error: main(failed) };
  await states.context.close();
  record("identity UI: a held catalog request renders a loading state and a failed one renders an error state without other users' items");
  run.write("identity-ui.json", evidence);
  return evidence;

  /**
   * Reload the actor's sign-in page (it loaded before Jellyfin was configured, and the
   * sidebar does not learn that without a reload), open its first library item (bob,
   * with no media, his former Family), reload again, and require the same account,
   * storage scope, stored library route, and view, with no title from a library the
   * actor cannot reach.
   */
  async function reloadPersists(name, actor, expected) {
    const id = (await ui.status(actor.context)).access.principal.user_id;
    const hidden = Object.keys(libraries).filter(library => !expected.includes(library)).flatMap(library => pages[library].items.map(item => item.name));
    const target = expected.slice().sort().find(library => pages[library].items.length) ?? "Family";
    const item = pages[target].items[0];
    await actor.page.reload();
    if (expected.length) await actor.page.goto(`${origin}/#/library/${encodeURIComponent(libraries[target])}`);
    await actor.page.goto(`${origin}/#/library/${encodeURIComponent(libraries[target])}/item/${encodeURIComponent(item.id)}`);
    const view = expected.length ? actor.page.getByText(item.name, { exact: true }).first() : actor.page.getByRole("heading", { name: "Library unavailable", exact: true });
    await expect(view).toBeVisible();
    const routeKey = `editarr.user.${id}.library-last-route.${libraries[target]}`;
    if (expected.length) await expect.poll(async () => (await storage(actor.page))[routeKey] ?? null, { message: `${name} stores its last ${target} route` }).not.toBeNull();
    const before = await storage(actor.page);
    assert.equal(before["editarr.user-scope"]?.split(":")[0], String(id), `${name}'s storage is bound to its own account`);
    await actor.page.reload();
    await expect(view).toBeVisible();
    await expect(actor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
    assert.equal((await ui.status(actor.context)).access.principal.user_id, id, `${name} reloads into the same account`);
    const after = await storage(actor.page);
    assert.equal(after["editarr.user-scope"], before["editarr.user-scope"], `${name}'s storage scope survives a reload`);
    assert.equal(after[routeKey], before[routeKey], `${name}'s stored ${target} route survives a reload`);
    await absent(actor.page, hidden, `${name} after reload`);
    return { target, item: item.id, scope: after["editarr.user-scope"], routeKey: after[routeKey] === undefined ? null : routeKey, view: expected.length ? item.name : "Library unavailable" };
  }

  async function keyboardSignIn(page, username, password) {
    await page.goto(`${origin}/#/settings/profile`);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
    await tabTo(page, page.getByLabel("Jellyfin username", { exact: true }), 20); await page.keyboard.type(username);
    await tabTo(page, page.getByLabel("Jellyfin password", { exact: true }), 20); await page.keyboard.type(password);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toHaveCount(0);
    await ui.status(page.context());
  }
}

/** Press Tab until `target` (or, with `anyRadio`, any radio) holds focus. */
async function tabTo(page, target, limit, anyRadio = false) {
  if (!anyRadio) assert.equal(await target.count(), 1, "keyboard target exists");
  for (let step = 0; step < limit; step++) {
    const focused = anyRadio
      ? await page.evaluate(() => document.activeElement?.getAttribute("type") === "radio" || document.activeElement?.getAttribute("role") === "radio")
      : await target.evaluate(element => element === document.activeElement, null, { timeout: 1000 });
    if (focused) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Keyboard focus did not reach the target within ${limit} Tab presses`);
}

const storage = page => page.evaluate(() => Object.fromEntries(Object.keys(window.localStorage).map(key => [key, window.localStorage.getItem(key)])));
