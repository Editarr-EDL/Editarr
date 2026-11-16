import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import { createRequire } from "node:module";
import { capture } from "./capture.mjs";
import { scanEvidence } from "./instance.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";
import { fillSignIn } from "./run-jellyfin.mjs";

export function usersUI(run) {
  const require = createRequire(path.join(run.root, "web/package.json"));
  const { expect } = require("@playwright/test");
  const AxeBuilder = require("@axe-core/playwright").default;
  const steps = [], requests = [], errors = [], contexts = [], axe = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  const record = name => { steps.push({ name, passed: true }); run.write("ui-steps.json", steps); console.log(`PASS ${name}`); };
  const save = (page, name) => capture(page, run, `ui-${name}`);
  async function status(context) {
    const body = await (await context.request.get(run.manifest.origin + "/api/auth/status")).json();
    if (body.csrf_token) run.secrets.add(body.csrf_token);
    for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
    return body;
  }
  async function api(context, method, pathname, data, expected = 200) {
    const auth = await status(context);
    const response = await context.request.fetch(run.manifest.origin + pathname, { method, data, headers: { Origin: run.manifest.origin, ...(method === "GET" ? {} : { "X-CSRF-Token": auth.csrf_token }) } });
    requests.push({ method, path: pathname, status: response.status() });
    assert.equal(response.status(), expected, `${method} ${pathname}`);
    return response.status() === 204 ? null : response.json();
  }
  async function open(width) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push(context); assert.equal((await context.cookies()).length, 0);
    await context.route("**/*", route => new URL(route.request().url()).origin === run.manifest.origin ? route.continue() : route.abort());
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on("pageerror", error => errors.push(error.message));
    page.on("response", response => { const url = new URL(response.url()); if (url.pathname.startsWith("/api/")) requests.push({ method: response.request().method(), path: url.pathname, status: response.status() }); });
    return { context, page };
  }
  async function signIn(actor, username, password, route = "profile") {
    await actor.page.goto(`${run.manifest.origin}/#/settings/${route}`);
    await expect(actor.page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
    await fillSignIn(actor.page, username, password);
    await actor.page.getByRole("button", { name: "Sign in", exact: true }).click();
    await status(actor.context);
  }
  async function directory(page) {
    await page.goto(`${run.manifest.origin}/#/settings/users`);
    await expect(page.getByRole("heading", { name: "Users", exact: true })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
  }
  async function details(page, name) {
    await page.getByRole("button", { name: `View ${name}`, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Account details", exact: true });
    await expect(dialog.getByRole("heading", { name, exact: true })).toBeVisible();
    return dialog;
  }
  async function checkAxe(page, name) {
    const report = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    axe.push({ name, violations: report.violations.map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map(node => node.target) })) });
    run.write("ui-axe.json", axe); assert.equal(report.violations.length, 0, `axe ${name}`);
  }
  async function checkTrap(page, dialog, rounds = 8) {
    await expect.poll(() => dialog.evaluate(element => element.contains(document.activeElement)), { message: "Dialog receives initial focus" }).toBe(true);
    for (let index = 0; index < rounds; index++) {
      await page.keyboard.press(index % 2 ? "Shift+Tab" : "Tab");
      assert.ok(await dialog.evaluate(element => element.contains(document.activeElement)), "Focus stays inside the active dialog");
    }
  }
  const providerEvidence = `${run.evidence}-jellyfin`;
  const providerCommand = (...args) => JSON.parse(execFileSync(pythonPath(run.root), ["-B", path.join(run.root, ".github/skills/verify-editarr/scripts/users_ui_provider.py"), ...args], { cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 300000 }));
  let providerStarted = false;
  async function provider() {
    if (run.jellyfin) {
      const { base, image, port, call } = run.jellyfin;
      const linkedPassword = secret();
      const linked = await call("POST", "/Users/New", { Name: "jf-linked", Password: linkedPassword });
      await call("POST", "/Users/New", { Name: "jf-second", Password: secret() });
      return { base, image, port, key: (await run.jellyfin.editarrKeys())[0], linked, linkedPassword, call };
    }
    const port = await run.ports.next();
    const instance = providerCommand("launch", providerEvidence, String(port)); providerStarted = true;
    run.write("ui-jellyfin-instance.json", { ...instance, port });
    let token;
    const client = 'MediaBrowser Client="editarr-users-ui", Device="verify", DeviceId="users-ui", Version="1"';
    async function call(method, pathname, data) {
      const response = await fetch(instance.base + pathname, { method, signal: AbortSignal.timeout(60000), headers: { Authorization: client + (token ? `, Token="${token}"` : ""), ...(data ? { "Content-Type": "application/json" } : {}) }, ...(data ? { body: JSON.stringify(data) } : {}) });
      assert.ok(response.ok, `Jellyfin ${method} ${pathname} ${response.status}`);
      const text = await response.text(); return text ? JSON.parse(text) : null;
    }
    const password = secret(), linkedPassword = secret();
    await call("POST", "/Startup/Configuration", { UICulture: "en-US", MetadataCountryCode: "US", PreferredMetadataLanguage: "en" });
    await call("GET", "/Startup/User"); await call("POST", "/Startup/User", { Name: "jf-root", Password: password });
    await call("POST", "/Startup/RemoteAccess", { EnableRemoteAccess: true, EnableAutomaticPortMapping: false });
    await call("POST", "/Startup/Complete");
    token = (await call("POST", "/Users/AuthenticateByName", { Username: "jf-root", Pw: password })).AccessToken; run.secrets.add(token);
    await call("POST", "/Auth/Keys?app=editarr-users-ui");
    const key = (await call("GET", "/Auth/Keys")).Items.find(entry => entry.AppName === "editarr-users-ui").AccessToken; run.secrets.add(key);
    const linked = await call("POST", "/Users/New", { Name: "jf-linked", Password: linkedPassword });
    await call("POST", "/Users/New", { Name: "jf-second", Password: secret() });
    return { ...instance, port, key, linked, linkedPassword, call };
  }
  async function close() {
    try {
      if (providerStarted) {
        try { run.write("ui-jellyfin-cleanup.json", providerCommand("cleanup", providerEvidence)); }
        finally {
          const redaction = scanEvidence(providerEvidence, run.secrets);
          run.write("ui-jellyfin-redaction.json", redaction);
          assert.deepEqual(redaction.findings, [], "Provider evidence contains no run secrets");
        }
      }
    } finally {
      for (const context of contexts) { await status(context).catch(() => {}); await context.close().catch(() => {}); }
      run.write("ui-http-statuses.json", requests); run.write("ui-browser-errors.json", errors);
    }
  }
  return { run, expect, secret, record, save, status, api, open, signIn, directory, details, checkAxe, checkTrap, provider, close, steps, requests, errors, contexts };
}