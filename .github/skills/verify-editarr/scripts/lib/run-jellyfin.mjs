import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";
import { scanEvidence } from "./instance.mjs";

const CLIENT = 'MediaBrowser Client="editarr-verify", Device="verify", DeviceId="verify-setup", Version="1"';

/**
 * Start the run's disposable Jellyfin 12 from the pinned image, finish its startup
 * wizard with an administrator and an ordinary user, and stop it with the run.
 * Every journey sets Editarr up against this server, so it stays the configured
 * server for the whole run unless a journey says otherwise.
 */
export async function startRunJellyfin(run, { libraries = false } = {}) {
  if (run.jellyfin) return run.jellyfin;
  const evidence = `${run.evidence}-setup-jellyfin`;
  const provider = (...args) => JSON.parse(execFileSync(pythonPath(run.root), ["-B", path.join(run.root, ".github/skills/verify-editarr/scripts/users_ui_provider.py"), ...args], {
    cwd: run.root, env: { ...childEnvironment(run.root, run.data), VERIFY_DOCKER_RUN_PREFIX: process.env.VERIFY_DOCKER_RUN_PREFIX ?? "verify-setup-jellyfin-" }, encoding: "utf8", timeout: 300000,
  }));
  const port = await run.ports.next();
  run.stopJellyfin = async () => {
    try { run.write("setup-jellyfin-cleanup.json", provider("cleanup", evidence)); }
    finally {
      const redaction = scanEvidence(evidence, run.secrets);
      run.write("setup-jellyfin-redaction.json", redaction);
      assert.deepEqual(redaction.findings, [], "Setup Jellyfin evidence contains no run secrets");
    }
  };
  const instance = provider("launch", evidence, String(port), ...(libraries ? ["--libraries"] : []));
  const jellyfin = jellyfinClient(instance.base);
  const secret = () => { const value = randomBytes(18).toString("base64url"); run.secrets.add(value); return value; };
  let admin, member;
  if (libraries) {
    for (const value of [instance.key, instance.admin.password, ...Object.values(instance.users).map(user => user.password)]) run.secrets.add(value);
    admin = { ...instance.admin };
    member = { name: "casey", ...instance.users.casey };
  } else {
    admin = { name: "jf-owner", password: secret() };
    member = { name: "jf-member", password: secret() };
    await jellyfin.call("POST", "/Startup/Configuration", { UICulture: "en-US", MetadataCountryCode: "US", PreferredMetadataLanguage: "en" });
    await jellyfin.call("GET", "/Startup/User");
    await jellyfin.call("POST", "/Startup/User", { Name: admin.name, Password: admin.password });
    await jellyfin.call("POST", "/Startup/RemoteAccess", { EnableRemoteAccess: true, EnableAutomaticPortMapping: false });
    await jellyfin.call("POST", "/Startup/Complete");
  }
  await adoptJellyfin(run, { base: instance.base, image: instance.image, port, admin, member, client: jellyfin, extra: { run: instance.run, libraries: libraries ? instance : null } });
  if (!libraries) member.id = (await jellyfin.call("POST", "/Users/New", { Name: member.name, Password: member.password })).Id;
  run.write("setup-jellyfin.json", { base: instance.base, image: instance.image, port, serverId: run.jellyfin.serverId, admin: admin.name, member: member.name });
  return run.jellyfin;
}

function jellyfinClient(base) {
  const client = { token: null };
  client.call = async (method, pathname, data) => {
    const authorization = client.token ? `${CLIENT}, Token="${client.token}"` : CLIENT;
    const response = await fetch(base + pathname, { method, signal: AbortSignal.timeout(60000), headers: { Authorization: authorization, ...(data ? { "Content-Type": "application/json" } : {}) }, ...(data ? { body: JSON.stringify(data) } : {}) });
    assert.ok(response.ok, `Jellyfin ${method} ${pathname} ${response.status}`);
    const text = await response.text(); return text ? JSON.parse(text) : null;
  };
  return client;
}

/**
 * Make the Jellyfin at `base`, whose startup wizard is complete, the run's Jellyfin:
 * sign in as `admin`, which Editarr setup then uses, and return the record journeys
 * read. `member` names an ordinary Jellyfin account that signs in with Jellyfin, or
 * is null. Another helper owns the server's lifecycle.
 */
export async function adoptJellyfin(run, { base, image, port, admin, member = null, client = jellyfinClient(base), extra = {} }) {
  const { call } = client;
  const signedIn = await call("POST", "/Users/AuthenticateByName", { Username: admin.name, Pw: admin.password });
  client.token = signedIn.AccessToken; run.secrets.add(client.token);
  admin.id = signedIn.User.Id;
  const info = await call("GET", "/System/Info/Public");
  assert.equal(info.StartupWizardCompleted, true, "Jellyfin finished its startup wizard");
  const editarrKeys = async () => (await call("GET", "/Auth/Keys")).Items.filter(entry => entry.AppName === "Editarr").map(entry => { run.secrets.add(entry.AccessToken); return entry.AccessToken; });
  run.jellyfin = { base, image, port, serverId: info.Id, admin, member, call, editarrKeys, run: null, libraries: null, ...extra };
  return run.jellyfin;
}

/** Fill and submit Editarr's first-run setup screen for `account` on `page`. */
export async function submitJellyfinSetup(page, jellyfin, account = jellyfin.admin) {
  await page.getByLabel("Jellyfin server URL", { exact: true }).fill(jellyfin.base);
  await page.getByLabel("Jellyfin username", { exact: true }).fill(account.name);
  await page.getByLabel("Jellyfin password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Connect and sign in", exact: true }).click();
}

/** The sign-in API path: every account signs in with its Jellyfin password. */
export const SIGN_IN_PATH = "/api/auth/jellyfin/login";

/** Fill the sign-in screen's Jellyfin username and password on `page`, without submitting. */
export async function fillSignIn(page, username, password) {
  await page.getByLabel("Jellyfin username", { exact: true }).fill(username);
  await page.getByLabel("Jellyfin password", { exact: true }).fill(password);
}

/**
 * Create a user named `name` on the run's Jellyfin with a random password, a Jellyfin
 * administrator when `administrator` is set. Editarr has no account for it until it
 * signs in or an administrator imports it. Returns `{ name, username, password, jellyfinId }`.
 */
export async function addJellyfinUser(run, name, { administrator = false } = {}) {
  const password = randomBytes(18).toString("base64url");
  run.secrets.add(password);
  const { Id: jellyfinId } = await run.jellyfin.call("POST", "/Users/New", { Name: name, Password: password });
  if (administrator) {
    const { Policy } = await run.jellyfin.call("GET", `/Users/${jellyfinId}`);
    await run.jellyfin.call("POST", `/Users/${jellyfinId}/Policy`, { ...Policy, IsAdministrator: true });
  }
  return { name, username: name, password, jellyfinId };
}

/** Rename the run's Jellyfin user `jellyfinId` to `name`; Editarr reads it at the next sync or sign-in. */
export async function renameJellyfinUser(run, jellyfinId, name) {
  const user = await run.jellyfin.call("GET", `/Users/${jellyfinId}`);
  await run.jellyfin.call("POST", `/Users?userId=${jellyfinId}`, { ...user, Name: name });
}

/**
 * Create `name` on the run's Jellyfin and admit it to Editarr by one Jellyfin sign-in
 * over the API, which creates its account, then sign that session out. Returns
 * `addJellyfinUser`'s record with the Editarr account `id`.
 */
export async function admitJellyfinUser(run, name, options = {}) {
  const user = await addJellyfinUser(run, name, options);
  const origin = run.manifest.origin;
  const { request } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
  try {
    const signedIn = await context.post(SIGN_IN_PATH, { data: { username: user.name, password: user.password } });
    assert.equal(signedIn.status(), 200, `${name} signs in with Jellyfin`);
    const body = await signedIn.json();
    run.secrets.add(body.csrf_token);
    for (const cookie of (await context.storageState()).cookies) run.secrets.add(cookie.value);
    assert.equal((await context.post("/api/auth/logout", { headers: { "X-CSRF-Token": body.csrf_token } })).status(), 200, `${name} signs out`);
    return { ...user, id: body.access.principal.user_id };
  } finally { await context.dispose(); }
}
