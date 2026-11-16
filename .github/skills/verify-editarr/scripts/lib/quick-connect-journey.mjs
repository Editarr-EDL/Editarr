import assert from "node:assert/strict";
import { accountMenuButton } from "./account-menu.mjs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture as captureMasked } from "./capture.mjs";
import { launchBrowser, sleep } from "./instance.mjs";

const CLIENT = 'MediaBrowser Client="editarr-verify", Device="verify", DeviceId="verify-quick-connect", Version="1"';
const QC = "/api/auth/jellyfin/quick-connect";
/** Jellyfin 12 forgets a Quick Connect request ten minutes after it starts. */
const JELLYFIN_EXPIRY_MS = 10 * 60 * 1000;

/**
 * Drive Jellyfin Quick Connect sign-in through the real sign-in page against the
 * run's Jellyfin 12: approval by the ordinary member through Jellyfin's API signs
 * in and creates its account, a replayed attempt is refused, Cancel stops polling
 * and leaves no session, a disabled Editarr account is refused with the password
 * sign-in message, turning Quick Connect off hides the button, and a code left
 * pending expires when Jellyfin expires it. The expiry wait overlaps the other
 * checks, so the journey takes about eleven minutes.
 */
export async function verifyQuickConnect(run, owner) {
  const { root, manifest } = run;
  const { expect, request } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
  const origin = manifest.origin;
  const jellyfin = run.jellyfin;
  const member = jellyfin.member;
  const steps = [];
  const contexts = [];
  const step = (name, detail = {}) => { steps.push({ at: new Date().toISOString(), name, ...detail }); run.write("quick-connect-steps.json", steps); };
  await launchBrowser(run);

  async function jellyfinAs(account) {
    const signIn = await fetch(`${jellyfin.base}/Users/AuthenticateByName`, { method: "POST", headers: { Authorization: CLIENT, "Content-Type": "application/json" }, body: JSON.stringify({ Username: account.name, Pw: account.password }) });
    assert.ok(signIn.ok, `Jellyfin sign-in for ${account.name}`);
    const token = (await signIn.json()).AccessToken;
    run.secrets.add(token);
    return async (method, pathname) => {
      const response = await fetch(`${jellyfin.base}${pathname}`, { method, headers: { Authorization: `${CLIENT}, Token="${token}"` } });
      assert.ok(response.ok, `Jellyfin ${method} ${pathname} ${response.status}`);
      return response;
    };
  }
  const asMember = await jellyfinAs(member);
  const approve = code => asMember("POST", `/QuickConnect/Authorize?code=${code}`);
  async function setQuickConnect(enabled) {
    const configuration = await jellyfin.call("GET", "/System/Configuration");
    await jellyfin.call("POST", "/System/Configuration", { ...configuration, QuickConnectAvailable: enabled });
    const editarr = await api();
    await expect.poll(async () => (await (await editarr.get(QC)).json()).enabled, { message: "Editarr's ten-second availability answer caught up", timeout: 15000, intervals: [1000] }).toBe(enabled);
  }

  async function browser(width) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push(context);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const polls = [];
    page.on("request", sent => { if (new URL(sent.url()).pathname === `${QC}/status`) polls.push(Date.now()); });
    await page.goto(`${origin}/#/settings/general`);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    const handle = async () => (await context.cookies()).find(cookie => cookie.name.endsWith("_quick_connect"));
    const status = async () => (await (await context.request.get(`${origin}/api/auth/status`)).json()).state;
    return { context, page, polls, handle, status, capture: name => captureMasked(page, run, name) };
  }
  async function showCode(page) {
    await page.getByRole("button", { name: "Use Quick Connect", exact: true }).click();
    const code = page.getByLabel("Quick Connect code", { exact: true });
    await expect(code).toHaveText(/^\d{6}$/);
    return (await code.textContent()).trim();
  }
  async function api() {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    contexts.push(context);
    return context;
  }

  try {
    await setQuickConnect(false);
    const hidden = await browser(1440);
    await expect(hidden.page.getByLabel("Jellyfin password", { exact: true })).toBeVisible();
    await sleep(500);
    await expect(hidden.page.getByRole("button", { name: "Use Quick Connect", exact: true })).toHaveCount(0);
    assert.deepEqual(await (await hidden.context.request.get(`${origin}${QC}`)).json(), { enabled: false });
    await hidden.capture("qc-disabled-hidden-1440");
    await setQuickConnect(true);
    await hidden.page.reload();
    await expect(hidden.page.getByRole("button", { name: "Use Quick Connect", exact: true })).toBeVisible();
    step("Quick Connect off in Jellyfin hides Use Quick Connect; back on shows it");

    const expiring = await browser(1440);
    const expiringCode = await showCode(expiring.page);
    const expiryStarted = Date.now();
    step("Left a code pending to expire", { code: expiringCode });

    const signIn = await browser(1440);
    const code = await showCode(signIn.page);
    await signIn.capture("qc-code-1440");
    const spentHandle = await signIn.handle();
    assert.ok(spentHandle?.httpOnly, "the Quick Connect handle cookie is HttpOnly");
    run.secrets.add(spentHandle.value);
    const approvedAt = Date.now();
    await approve(code);
    await expect(accountMenuButton(signIn.page)).toBeVisible();
    const approvalToSignedIn = Date.now() - approvedAt;
    const signedIn = await (await signIn.context.request.get(`${origin}/api/auth/status`)).json();
    if (signedIn.csrf_token) run.secrets.add(signedIn.csrf_token);
    assert.equal(signedIn.state, "authenticated");
    assert.equal(signedIn.jellyfin_user_id.replaceAll("-", ""), member.id.replaceAll("-", "").toLowerCase(), "Quick Connect signed in the approving member");
    assert.equal(signedIn.access.principal.role, "user", "first sign-in admitted the member as an ordinary user");
    assert.equal(await signIn.handle(), undefined, "sign-in cleared the Quick Connect handle");
    await signIn.capture("qc-signed-in-1440");
    step("Member approved through Jellyfin's API and was signed in and admitted", { approvalToSignedInMs: approvalToSignedIn });

    const replay = await api();
    const replayed = await replay.fetch(`${QC}/complete`, { method: "POST", data: {}, headers: { Cookie: `${spentHandle.name}=${spentHandle.value}` } });
    assert.equal(replayed.status(), 409);
    assert.equal((await replayed.json()).code, "quick_connect_consumed");
    assert.equal((await (await replay.get("/api/auth/status")).json()).state, "unauthenticated");
    step("Replaying the spent attempt is refused", { status: replayed.status(), code: "quick_connect_consumed" });

    const cancel = await browser(320);
    const cancelledCode = await showCode(cancel.page);
    await cancel.capture("qc-code-320");
    const cancelledHandle = await cancel.handle();
    run.secrets.add(cancelledHandle.value);
    await cancel.page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(cancel.page.getByLabel("Jellyfin password", { exact: true })).toBeVisible();
    const pollsAtCancel = cancel.polls.length;
    await approve(cancelledCode);
    await sleep(3000);
    assert.equal(cancel.polls.length, pollsAtCancel, "polling stopped at Cancel");
    assert.equal(await cancel.status(), "unauthenticated", "a cancelled code leaves no session");
    const afterCancel = await replay.fetch(`${QC}/status`, { headers: { Cookie: `${cancelledHandle.name}=${cancelledHandle.value}` } });
    assert.equal((await afterCancel.json()).state, "cancelled");
    await cancel.capture("qc-cancelled-320");
    step("Cancel at 320px stopped polling; a later approval signed nobody in");

    const admin = await api();
    const ownerSignIn = await admin.post("/api/auth/jellyfin/login", { data: { username: owner.username, password: owner.password } });
    assert.equal(ownerSignIn.status(), 200);
    const csrf = (await ownerSignIn.json()).csrf_token;
    run.secrets.add(csrf);
    const memberId = signedIn.access.principal.user_id;
    const setStatus = async value => assert.equal((await admin.put(`/api/users/${memberId}/status`, { data: { status: value }, headers: { "X-CSRF-Token": csrf } })).status(), 200);
    await setStatus("disabled");
    const disabled = await browser(1440);
    await approve(await showCode(disabled.page));
    await expect(disabled.page.getByRole("alert")).toHaveText("This account is disabled. Ask an administrator for help.");
    assert.equal(await disabled.status(), "unauthenticated");
    await disabled.capture("qc-disabled-account-1440");
    const password = await (await api()).post("/api/auth/jellyfin/login", { data: { username: member.name, password: member.password } });
    assert.equal((await password.json()).code, "account_disabled", "password sign-in refuses the same account the same way");
    await setStatus("active");
    step("A disabled Editarr account is refused with the password sign-in message");

    await expect(expiring.page.getByRole("alert")).toHaveText("This code expired. Get a new code to try again.", { timeout: JELLYFIN_EXPIRY_MS + 120000 - (Date.now() - expiryStarted) });
    const expiredAfter = Date.now() - expiryStarted;
    await expiring.capture("qc-expired-1440");
    const pollsAtExpiry = expiring.polls.length;
    await sleep(2500);
    assert.equal(expiring.polls.length, pollsAtExpiry, "polling stopped at expiry");
    await expiring.page.getByRole("button", { name: "Get a new code", exact: true }).click();
    const renewed = (await expiring.page.getByLabel("Quick Connect code", { exact: true }).textContent()).trim();
    assert.match(renewed, /^\d{6}$/);
    await expiring.page.getByRole("button", { name: "Cancel", exact: true }).click();
    step("A pending code expired when Jellyfin expired it and a new code was offered", { expiredAfterMs: expiredAfter });

    run.write("quick-connect-result.json", { passed: true, approvalToSignedInMs: approvalToSignedIn, expiredAfterMs: expiredAfter, steps: steps.map(entry => entry.name) });
    console.log(`PASS: Quick Connect sign-in by the ordinary member (${approvalToSignedIn} ms from approval), replay refused, cancel at 320px, disabled account refused, hidden when off, expiry after ${Math.round(expiredAfter / 1000)} s with a new code.`);
  } finally {
    await setQuickConnect(true).catch(() => {});
    for (const context of contexts) {
      for (const cookie of await context.storageState().then(state => state.cookies).catch(() => [])) run.secrets.add(cookie.value);
      await (context.dispose?.() ?? context.close()).catch(() => {});
    }
  }
}
