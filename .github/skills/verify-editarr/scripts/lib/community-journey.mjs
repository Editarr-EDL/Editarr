import assert from "node:assert/strict";
import { accountMenuButton } from "./account-menu.mjs";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { verifyCommunityWrites } from "./community-write-probes.mjs";
import { synthesizeMedia } from "./fixtures.mjs";
import { FILM, itemRoute, jellyfinTarget, probedDuration, startSyntheticJellyfin } from "./synthetic-jellyfin.mjs";
import { SIGN_IN_PATH, admitJellyfinUser, fillSignIn } from "./run-jellyfin.mjs";

export function prepareCommunity(run) {
  const bin = path.join(run.scratch, "bin");
  fs.mkdirSync(bin);
  const provisioned = process.env.EDITARR_VERIFY_PNPM_CJS;
  if (!provisioned || !fs.existsSync(provisioned)) throw new Error("Set EDITARR_VERIFY_PNPM_CJS to the provisioned pnpm .cjs; no install is performed");
  fs.writeFileSync(path.join(bin, "pnpm"), `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(provisioned)} "$@"\n`, { mode: 0o700 });
  run.environment = { PATH: `${bin}${path.delimiter}${process.env.PATH}`,
    EDITARR_COMMUNITY_SERVICE_URL: "https://community.test", EDITARR_COMMUNITY_SERVICE_AUDIENCE: "community.test",
    EDITARR_VERIFY_COMMUNITY_EVIDENCE: path.join(run.evidence, "community-double.json"), EDITARR_VERIFY_NODE: process.execPath };
}

export async function verifyCommunity(run, owner) {
  const require = createRequire(path.join(run.root, "web/package.json"));
  const { expect, request } = require("@playwright/test");
  const { default: AxeBuilder } = require("@axe-core/playwright");
  const origin = run.manifest.origin, contexts = [], clients = [], steps = [], requests = [], failures = [], axe = [], authorizations = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  const step = (name, detail = {}) => { steps.push({ name, ...detail }); run.write("community-steps.json", steps); };
  async function client(account) {
    const api = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } }); clients.push(api);
    const login = await api.post(SIGN_IN_PATH, { data: { username: account.username, password: account.password } });
    assert.equal(login.status(), 200);
    const csrf = (await login.json()).csrf_token; run.secrets.add(csrf);
    return async (method, url, data, multipart) => {
      const response = await api.fetch(url, { method, ...(multipart ? { multipart } : { data }), headers: method === "GET" ? {} : { "X-CSRF-Token": csrf } });
      requests.push({ actor: account.username, method, path: url, status: response.status() });
      return { status: response.status(), body: await response.json().catch(() => null) };
    };
  }
  const ownerApi = await client(owner), fresh = { reauthentication: { password: owner.password } };
  async function create(username, role) {
    const account = await admitJellyfinUser(run, username);
    if (role === "admin") assert.equal((await ownerApi("PUT", `/api/users/${account.id}/role`, { role, ...fresh })).status, 200);
    return account;
  }
  async function open(account, width, section = "settings/profile") {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } }); contexts.push(context);
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort("blockedbyclient"));
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on("pageerror", error => failures.push(error.message));
    page.on("response", response => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/")) requests.push({ actor: account.username, width, method: response.request().method(), path: url.pathname, status: response.status() });
    });
    await page.goto(`${origin}/#/${section}`);
    await fillSignIn(page, account.username, account.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page, account.username)).toBeVisible();
    for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
    return page;
  }
  async function proof(page, name) {
    await capture(page, run, name);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    const violations = result.violations.map(item => ({ id: item.id, nodes: item.nodes.length }));
    axe.push({ name, violations }); run.write("community-axe.json", axe);
    assert.deepEqual(violations, [], name);
  }
  async function enroll(page, stateId) {
    await page.getByRole("button", { name: "Connect with Discord", exact: true }).click();
    const link = page.getByRole("link", { name: "Continue with Discord", exact: true }); await expect(link).toBeVisible();
    const authorization = await link.getAttribute("href"); run.secrets.add(authorization);
    authorizations.push(authorization);
    fs.writeFileSync(path.join(run.scratch, "community-authorizations.json"), JSON.stringify(authorizations), { mode: 0o600 });
    await page.getByLabel("Discord confirmation URL or enrollment reference").fill(stateId);
    await page.getByRole("button", { name: "Review connection", exact: true }).click();
    await page.getByRole("button", { name: "Confirm connection", exact: true }).click();
    await expect(page.getByRole("button", { name: "Disconnect community", exact: true })).toBeEnabled();
  }
  let provider;
  try {
    const member = await create("mu32-member", "user"), admin = await create("mu32-admin", "admin");
    const memberApi = await client(member), adminApi = await client(admin);
    for (const [actor, target, expected] of [[memberApi, admin.id, 404], [adminApi, 1, 403]]) {
      const refused = await actor("GET", `/api/users/${target}/community/connection/status`);
      assert.equal(refused.status, expected, `named identity target ${target}`);
    }
    step("Independent concealed other-account and protected-owner refusals", { statuses: [404, 403] });
    const memberPage = await open(member, 1440);
    await enroll(memberPage, "a".repeat(64));
    const memberState = await memberApi("GET", "/api/community/connection/status"); assert.equal(memberState.status, 200);
    await memberPage.getByRole("button", { name: "Recover import receipts", exact: true }).click();
    await expect(memberPage.getByText("No eligible import receipts.", { exact: true })).toBeVisible();
    await proof(memberPage, "community-own-1440");
    const adminPage = await open(admin, 1440); await enroll(adminPage, "b".repeat(64));
    const adminState = await adminApi("GET", "/api/community/connection/status"); assert.equal(adminState.status, 200);
    assert.notEqual(memberState.body.identity.installationId, adminState.body.identity.installationId);
    step("Own enrollment persists distinct per-account identities", { member: memberState.body.profile.contributorId, admin: adminState.body.profile.contributorId });

    const media = synthesizeMedia(run.root, run.scratch, run.data); run.write("media.json", media);
    provider = await startSyntheticJellyfin(run, { ...media, durationSeconds: probedDuration(run, media.file) }, { port: await run.ports.next() });
    assert.equal((await ownerApi("PUT", "/api/settings/jellyfin", provider.settings)).status, 200);
    assert.equal((await ownerApi("POST", "/api/jellyfin/sync")).status, 200, "members admitted before the synthetic catalog read its libraries at the next sync");
    const existing = await ownerApi("POST", "/api/edl-imports/upload", null, {
      options: JSON.stringify({ target: jellyfinTarget(FILM), name: "Synthetic owner media" }),
      file: { name: "owner.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") } });
    assert.equal(existing.status, 201, JSON.stringify(existing.body));
    const status = await (await memberPage.context().request.get(`${origin}/api/auth/status`)).json(); run.secrets.add(status.csrf_token);
    const imported = await memberPage.context().request.post(`${origin}/api/edl-imports/upload`, {
      headers: { Origin: origin, "X-CSRF-Token": status.csrf_token }, multipart: {
        options: JSON.stringify({ target: existing.body.review.target, name: "Member community review" }),
        file: { name: "community.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") } } });
    assert.equal(imported.status(), 201, await imported.text());
    const review = (await imported.json()).review;
    const source = `/api/community/sharing/sources/review/${review.id}`;
    const preview = await memberApi("POST", `${source}/preview`, { name: "Original member contribution", identity_confirmed: true,
      media: { provider: "tmdb", entity_type: "movie", reference: "123" } }); assert.equal(preview.status, 200);
    const queued = await memberApi("POST", `/api/community/sharing/previews/${preview.body.previewId}/approve`, { confirmed: true }); assert.equal(queued.status, 200);

    for (const width of [1440, 320]) {
      const page = width === 1440 ? adminPage : await open(admin, width);
      await page.goto(`${origin}/#/settings/users`);
      await page.getByRole("button", { name: "View mu32-member", exact: true }).click();
      const account = page.getByRole("dialog", { name: "Account details", exact: true });
      await account.getByText("Community connection", { exact: true }).click();
      await expect(account.getByRole("heading", { name: "Community scans for mu32-member", exact: true })).toBeVisible();
      await expect(account.getByRole("button", { name: "Disconnect community", exact: true })).toBeEnabled();
      await expect(account.getByRole("button", { name: "Recover import receipts", exact: true })).toBeEnabled();
      await expect(account.getByRole("region", { name: "Community scans for mu32-member", exact: true }).getByText(memberState.body.identity.installationId, { exact: true })).toBeVisible();
      await expect(account.getByText(adminState.body.identity.installationId, { exact: true })).toHaveCount(0);
      await account.getByRole("heading", { name: "Community scans for mu32-member", exact: true }).scrollIntoViewIfNeeded();
      await proof(page, `community-named-${width}`);
      await account.getByRole("button", { name: "Close", exact: true }).click();

      const deniedCapabilities = ["community.identity.disconnect.other", "community.identity.keys.other", "community.identity.profile.other", "community.receipts.recover.other"];
      assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: Object.fromEntries(deniedCapabilities.map(capability => [capability, "deny"])), ...fresh })).status, 200);
      await page.reload();
      await page.getByRole("button", { name: "View mu32-member", exact: true }).click();
      await account.getByText("Community connection", { exact: true }).click();
      await expect(account.getByRole("heading", { name: "Community scans for mu32-member", exact: true })).toBeVisible();
      for (const name of ["Disconnect community", "Recover with Discord", "Recover import receipts", "Save name"]) await expect(account.getByRole("button", { name, exact: true })).toHaveCount(0);
      assert.equal((await adminApi("POST", `/api/users/${member.id}/community/keys/rotate`, { confirmed: true })).status, 403);
      await account.getByRole("heading", { name: "Community scans for mu32-member", exact: true }).scrollIntoViewIfNeeded();
      await proof(page, `community-permission-refresh-${width}`);
      await account.getByRole("button", { name: "Close", exact: true }).click();
      assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: Object.fromEntries(deniedCapabilities.map(capability => [capability, "inherit"])), ...fresh })).status, 200);
      await page.reload();

      await page.goto(`${origin}/${itemRoute(FILM)}`);
      const panel = page.locator(".saved-review-panel");
      await panel.getByRole("button", { name: "Open review Member community review", exact: true }).click();
      await panel.getByRole("button", { name: "Share Member community review", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Share review", exact: true });
      await dialog.getByLabel("TMDB/TVDB ID or URL").fill("123");
      await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
      await dialog.getByRole("button", { name: "Generate preview", exact: true }).click();
      await expect(dialog.getByText(adminState.body.profile.contributorId, { exact: true })).toBeVisible();
      await dialog.getByText(adminState.body.profile.contributorId, { exact: true }).scrollIntoViewIfNeeded();
      await proof(page, `community-acting-preview-${width}`);
      const currentReview = await memberApi("GET", `/api/reviews/${review.id}`);
      const changed = await memberApi("PATCH", `/api/reviews/${review.id}`, { name: `Changed source ${width}`, revision: currentReview.body.revision });
      assert.equal(changed.status, 200);
      await dialog.getByLabel("I may share this data and its notes, and I approve this exact preview.").check();
      await dialog.getByRole("button", { name: "Share contribution", exact: true }).click();
      await expect(dialog.getByRole("alert")).toHaveText(/The source changed/);
      await expect(dialog.getByLabel("I confirm this TMDB/TVDB association.")).not.toBeChecked();
      await expect(dialog.getByRole("region", { name: "Outgoing contribution" })).toHaveCount(0);
      await proof(page, `community-stale-refresh-${width}`);
      await dialog.getByRole("region", { name: "Saved publications" }).getByRole("button").click();
      await expect(dialog.getByText(memberState.body.profile.contributorId, { exact: true })).toBeVisible();
      for (const name of ["Check outcome", "Retry saved contribution", "Cancel publication"]) await expect(dialog.getByRole("button", { name, exact: true })).toBeVisible();
      await proof(page, `community-original-contributor-${width}`);
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      assert.equal((await memberApi("PATCH", `/api/reviews/${review.id}`, { name: "Member community review", revision: changed.body.revision })).status, 200);
      step("Named identity, acting preview, stale refresh and original job contributor", { width });
    }
    const mobile = await open(member, 320);
    await expect(mobile.getByRole("button", { name: "Disconnect community", exact: true })).toBeEnabled();
    await proof(mobile, "community-own-320");
    await mobile.getByRole("button", { name: "Disconnect community", exact: true }).click();
    await expect(mobile.getByRole("button", { name: "Connect with Discord", exact: true })).toBeEnabled();
    assert.equal((await memberApi("GET", "/api/community/connection/status")).body.connected, false);
    assert.equal((await adminApi("GET", "/api/community/connection/status")).body.connected, true);
    await mobile.getByRole("button", { name: "Connect with Discord", exact: true }).click();
    await expect(mobile.getByRole("button", { name: "Disconnect community", exact: true })).toBeEnabled();
    const reconnected = await memberApi("GET", "/api/community/connection/status");
    assert.equal(reconnected.body.identity.installationId, memberState.body.identity.installationId);
    const saved = await adminApi("GET", source); assert.equal(saved.status, 200);
    assert.equal(saved.body.jobs.length, 1);
    assert.equal(saved.body.jobs[0].contributorId, memberState.body.profile.contributorId);
    const transport = JSON.parse(fs.readFileSync(path.join(run.evidence, "community-double.json"), "utf8"));
    assert.deepEqual(transport.posts, []);
    step("Disconnect/reconnect retained own identity and left the admin connected; stale approvals queued nothing");
    await verifyCommunityWrites({ run, step, ownerApi, memberApi, adminApi, member, admin, fresh, job: saved.body.jobs[0], source, target: existing.body.review.target });
    assert.deepEqual(failures, []);
    run.write("community-result.json", { status: "PASS", fixtureVersion: "MU32-community-ui-1", steps: steps.length, widths: [1440, 320], applicationErrors: failures, liveCommunityService: false });
  } catch (error) {
    run.write("community-result.json", { status: "FAIL", message: error.message, steps: steps.length, applicationErrors: failures });
    for (const context of contexts) for (const page of context.pages()) await capture(page, run, `community-failure-${contexts.indexOf(context)}`).catch(() => {});
    throw error;
  } finally {
    if (provider && run.jellyfin) {
      const restored = await ownerApi("PUT", "/api/settings/jellyfin", { server_url: run.jellyfin.base, api_key: (await run.jellyfin.editarrKeys())[0] });
      assert.equal(restored.status, 200, "the run's Jellyfin is connected again for later journeys");
    }
    if (provider) await provider.close();
    run.write("community-http-statuses.json", requests);
    for (const context of contexts) await context.close();
    for (const api of clients) await api.dispose();
  }
}
