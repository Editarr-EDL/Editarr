import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { launchBrowser } from "./instance.mjs";
import { childEnvironment, pythonPath, runLogs } from "./prerequisites.mjs";

const USERNAME = "fixture-admin";
const WRONG_PASSWORD = "sentinel-wrong-password-mu10";
export const HEAD = "0030_remove_passwords";
export const SERVICE = "https://community.test";
const SEED = [
  "import hashlib, json, sys",
  "from pathlib import Path",
  "from tests.activity_migration_support import build_activity_database",
  "from tests.community_identity_migration_support import add_community_state",
  "from tests.test_account_migration import KEY",
  "cookie, _ = build_activity_database(Path(sys.argv[1]), Path(sys.argv[2]), '0009_readable_api_key', queued_status='awaiting_model_consent')",
  "files = add_community_state(Path(sys.argv[1]))",
  "manifest = json.loads(files['identity.json'])",
  "print(json.dumps({'legacy': {'cookie': cookie, 'key': KEY}, 'identity': {",
  "  'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},",
  "  'installationId': manifest['installation_id'], 'keyId': manifest['key_id'], 'contributorId': manifest['contributor_id'],",
  "}}))",
].join("\n");
const INVENTORY = [
  "import json, sqlite3, sys",
  "db = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)",
  "query = lambda sql: [list(row) for row in db.execute(sql)]",
  "print(json.dumps({",
  "  'reviews': query('SELECT id, revision, owner_id, original_author_id FROM saved_reviews ORDER BY id'),",
  "  'scans': query('SELECT id, status, initiator_id, initiator_permission_revision FROM scans ORDER BY id'),",
  "  'queue': query('SELECT scan_id FROM scan_queue ORDER BY id'),",
  "  'exports': query('SELECT id, format, scope, review_id, actor_id, policy_owner_id, original_author_id, policy_revision FROM media_item_exports ORDER BY id'),",
  "  'personalValues': query('SELECT (SELECT count(*) FROM user_filter_copy_rules), (SELECT count(*) FROM user_filter_copy_severities), (SELECT count(*) FROM user_filter_severity_overrides), (SELECT count(*) FROM user_filter_rule_overrides), (SELECT count(*) FROM user_filter_personal_rules)'),",
  "  'communityIdentities': query('SELECT credential_ref, user_id FROM community_identities'),",
  "  'communityConnections': query('SELECT credential_ref, service_url, contributor_id FROM community_connections'),",
  "  'communityOperations': query('SELECT operation_id, kind, state, owner_id, actor_id FROM community_operations ORDER BY operation_id'),",
  "  'auditEvents': query('SELECT action, outcome, actor_kind, actor_id, credential, target_user_id, media_key, resource, details_json FROM audit_events ORDER BY id'),",
  "  'mediaAccess': query('SELECT (SELECT count(*) FROM archived_media_access_rows), (SELECT all_sources FROM media_access_scopes WHERE id = 1), (SELECT count(*) FROM media_access_scopes), (SELECT count(*) FROM sqlite_master WHERE name = \\'media_access_exceptions\\')'),",
  "  'archivedItems': query('SELECT id, archived_source, name FROM media_items ORDER BY id'),",
  "  'apiKeys': query('SELECT user_id, token_value IS NULL, secret_envelope LIKE \\'v1.%\\' FROM api_keys'),",
  "  'freePages': query('PRAGMA freelist_count'),",
  "  'schema': query('SELECT version_num FROM alembic_version'),",
  "  'users': query('SELECT id, role, status, disable_cause FROM users ORDER BY id'),",
  "  'userColumns': [row[1] for row in db.execute('PRAGMA table_info(users)')],",
  "  'accountSettingsColumns': [row[1] for row in db.execute('PRAGMA table_info(account_settings)')],",
  "  'identities': query('SELECT count(*) FROM user_provider_identities'),",
  "  'foreignKeyViolations': query('PRAGMA foreign_key_check'),",
  "}))",
].join("\n");

export function sidecars(media) {
  return Object.fromEntries(fs.readdirSync(media).sort().map(name => {
    const file = path.join(media, name);
    return [name, { sha256: crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"), mtimeNs: String(fs.statSync(file, { bigint: true }).mtimeNs) }];
  }));
}

/** Digests of the identity files in one directory, without lock files; null when it is absent. */
export function identityFiles(directory) {
  if (!fs.existsSync(directory)) return null;
  return Object.fromEntries(fs.readdirSync(directory).filter(name => !name.endsWith(".lock")).sort().map(name => (
    [name, crypto.createHash("sha256").update(fs.readFileSync(path.join(directory, name))).digest("hex")]
  )));
}

/**
 * Write the MU-03 synthetic 0009 database (owner, live session, readable key, reviews,
 * scans, a consent-waiting job, legacy EDL and MCF exports with real sidecars) as this
 * run's database before launch, with the MU-06 enrolled community identity in the legacy
 * `secrets/community` directory, its connection and operations, and a legacy
 * authentication audit log. Its credentials become run secrets. The instance gets a
 * reserved `.test` community service so the network-free status route resolves the
 * owner's identity; nothing contacts a community service.
 */
export function seedLegacyDatabase(run) {
  fs.mkdirSync(run.data, { mode: 0o700 });
  run.media = path.join(run.scratch, "media");
  const output = execFileSync(pythonPath(run.root), ["-P", "-c", SEED, path.join(run.data, "scanner.db"), run.media], {
    cwd: run.root, env: childEnvironment(run.root, path.join(run.scratch, "fixture-data")), encoding: "utf8", timeout: 60000,
  });
  ({ legacy: run.legacy, identity: run.identity } = JSON.parse(output.trim().split("\n").at(-1)));
  for (const value of Object.values(run.legacy)) run.secrets.add(value);
  run.secrets.add(WRONG_PASSWORD);
  run.sidecarsBefore = sidecars(run.media);
  run.auditBefore = fs.readFileSync(path.join(runLogs(run.data), "auth-audit.jsonl"));
  run.environment = { EDITARR_COMMUNITY_SERVICE_URL: SERVICE, EDITARR_COMMUNITY_SERVICE_AUDIENCE: "community.test" };
}

export function inventory(run) {
  const output = execFileSync(pythonPath(run.root), ["-P", "-c", INVENTORY, path.join(run.data, "scanner.db")], {
    cwd: run.root, env: childEnvironment(run.root, path.join(run.scratch, "fixture-data")), encoding: "utf8", timeout: 30000,
  });
  return JSON.parse(output.trim());
}

/** Prove startup upgraded the seeded database, assigned its activity to the owner, and left sidecars alone. */
export async function verifyUpgrade(run) {
  const { root, manifest: { origin }, legacy } = run;
  const journal = JSON.parse(fs.readFileSync(path.join(run.data, "migration-upgrade.json"), "utf8"));
  assert.equal(journal.status, "complete");
  assert.equal(journal.from_revision, "0009_readable_api_key");
  assert.equal(journal.to_revision, HEAD);
  assert.ok(fs.existsSync(path.join(journal.generation, "snapshot.sqlite3")), "pre-upgrade snapshot retained");
  const oldCookie = await fetch(`${origin}/api/system/version`, { headers: { Cookie: `editarr_session=${legacy.cookie}` } });
  assert.equal(oldCookie.status, 401);
  const keyVersion = await fetch(`${origin}/api/system/version`, { headers: { "X-Api-Key": legacy.key } });
  const keyBody = await keyVersion.json();
  assert.equal(keyVersion.status, 403, "the disabled owner's migrated key is refused");
  const keyRefusal = keyBody.code ?? keyBody.detail?.code ?? null;
  const removedLogin = await fetch(`${origin}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json", Origin: origin }, body: JSON.stringify({ username: USERNAME, password: WRONG_PASSWORD }) });
  assert.ok([404, 405].includes(removedLogin.status), `local sign-in is gone: ${removedLogin.status}`);
  const status = await (await fetch(`${origin}/api/auth/status`)).json();
  assert.deepEqual([status.state, status.sign_in], ["unauthenticated", { jellyfin: false }], "no Jellyfin is configured, so nobody can sign in");
  const bytes = Buffer.concat(["scanner.db", "scanner.db-wal"].map(name => path.join(run.data, name)).filter(file => fs.existsSync(file)).map(file => fs.readFileSync(file)));
  assert.equal(bytes.includes(Buffer.from("$argon2")), false, "no Argon2 hash bytes remain in the upgraded database");
  const database = inventory(run);
  assert.deepEqual(database.reviews, [["review-imported-002", 5, 1, 1], ["review-legacy-001", 2, 1, 1]]);
  assert.deepEqual(database.scans.map(([id, , initiator, revision]) => [id, initiator, revision]), [
    ["imported-scan-003", 1, 1], ["legacy-scan-001", 1, 1], ["queued-scan-002", 1, 1],
  ]);
  assert.deepEqual(database.queue, [], "the archived item's queued scan left the queue");
  assert.deepEqual(database.archivedItems.map(([, source, name]) => [source, name]), [["manual_files", "Movie.mkv"]]);
  assert.deepEqual(database.exports, [
    [1, "edl", "legacy", "review-legacy-001", 1, 1, 1, null],
    [2, "mcf", "legacy", null, 1, null, null, null],
  ]);
  assert.deepEqual(database.personalValues, [[0, 0, 0, 0, 0]]);
  assert.deepEqual(database.foreignKeyViolations, []);
  const schema = database.schema[0][0];
  assert.equal(schema, HEAD);
  assert.deepEqual(database.users, [[1, "owner", "disabled", "unlinked"]], "the seeded owner has no Jellyfin link, so 0030 disables it with cause unlinked");
  assert.deepEqual(database.identities, [[0]]);
  for (const column of ["password_hash", "local_credential", "password_changed_at"]) assert.ok(!database.userColumns.includes(column), `users has no ${column}`);
  assert.ok(!database.accountSettingsColumns.includes("registration_mode"), "account_settings has no registration_mode");
  assert.equal(database.communityIdentities.length, 1, "one community identity");
  const [[reference, identityOwner]] = database.communityIdentities;
  assert.match(reference, /^[0-9a-f]{32}$/);
  assert.equal(identityOwner, 1);
  assert.deepEqual(database.communityConnections, [[reference, SERVICE, run.identity.contributorId]]);
  assert.deepEqual(database.communityOperations, [
    // 0026 ends the archived Other Files scan's unsent publication cancelled.
    ["op-publish", "publish", "failed", 1, null],
    ["op-receipt", "receipt", "acknowledged", 1, null],
    ["op-vote", "vote", "submitted", 1, null],
  ]);
  assert.deepEqual(database.auditEvents, [], "the upgrade stores no audit event");
  assert.deepEqual(database.mediaAccess, [[0, 1, 1, 0]], "default scope reaches all sources; nothing archived; the exception table is gone");
  assert.deepEqual(database.apiKeys, [[1, 1, 1]], "the owner's key is wrapped, with no plaintext column value");
  assert.deepEqual(database.freePages, [[0]], "sealing rewrote the file, so no freed page keeps old plaintext");
  const incoming = path.join(run.data, "secrets", "community");
  const home = path.join(run.data, "secrets", "community-identities", reference);
  assert.deepEqual(identityFiles(incoming), run.identity.files, "startup moved no identity file");
  assert.equal(identityFiles(home), null);
  const { expect } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
  await launchBrowser(run);
  const viewports = [];
  const externalRequests = [];
  for (const width of [1440, 320]) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    try {
      await context.route("**/*", route => {
        const url = new URL(route.request().url());
        if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) {
          externalRequests.push({ origin: url.origin, method: route.request().method() });
          return route.abort("blockedbyclient");
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(20000);
      await page.goto(`${origin}/#/settings/general`);
      await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
      await expect(page.getByText("Sign-in is unavailable because Editarr is not connected to a Jellyfin server.", { exact: true })).toBeVisible();
      assert.equal(await page.getByRole("radio").count(), 0, "no sign-in method choice");
      assert.equal(await page.getByRole("button", { name: "Create an account", exact: true }).count(), 0, "no registration");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `sign-in overflows at ${width}`);
      await capture(page, run, `upgraded-locked-out-${width}`);
      viewports.push({ width, state: (await (await context.request.get(`${origin}/api/auth/status`)).json()).state, signInUnavailable: true });
    } finally {
      for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
      await context.close();
    }
  }
  assert.equal(externalRequests.length, 0, "Unexpected browser requests outside the owned origin");
  assert.deepEqual(identityFiles(incoming), run.identity.files, "with no owner session, the identity files stay unchanged in the legacy directory");
  assert.equal(identityFiles(home), null, "no identity home was made");
  const audit = fs.readFileSync(path.join(runLogs(run.data), "auth-audit.jsonl"));
  assert.ok(audit.subarray(0, run.auditBefore.length).equals(run.auditBefore), "legacy audit records unchanged");
  const after = inventory(run);
  assert.deepEqual(after.communityOperations, database.communityOperations, "no actor added to legacy operations");
  assert.deepEqual(after.auditEvents, [], "nothing signed in, so no audit event is stored");
  const appended = audit.subarray(run.auditBefore.length).toString();
  const logged = appended.trim().split("\n").filter(Boolean).map(line => JSON.parse(line)).filter(record => record.event === "auth.login");
  const storedText = JSON.stringify(after.auditEvents);
  for (const secret of run.secrets) {
    assert.ok(!storedText.includes(secret) && !appended.includes(secret), "no secret in audit rows or appended log lines");
  }
  const community = {
    reference, identityOwner, connections: database.communityConnections, operations: database.communityOperations,
    identityFiles: identityFiles(incoming), legacyAuditPreserved: true, storedAuditEvents: after.auditEvents, loggedSignIns: logged.length,
  };
  const sidecarsAfter = sidecars(run.media);
  assert.deepEqual(sidecarsAfter, run.sidecarsBefore, "legacy sidecars and media unchanged");
  run.write("result.json", {
    feature: "mu-activity-migration", passed: true, sourceRevision: journal.from_revision, schema,
    snapshotRetained: true, oldCookieStatus: oldCookie.status, apiKeyStatus: keyVersion.status, apiKeyRefusal: keyRefusal, removedLocalSignIn: removedLogin.status,
    owner: database.users[0], argon2BytesRemaining: false, database, community, sidecars: sidecarsAfter, viewports, externalBrowserRequests: externalRequests,
    scope: "Synthetic 0009 fixture upgraded by real source startup to 0030. Its owner has no Jellyfin link and no Jellyfin is configured, so 0030 disables it and nobody can sign in (plan Appendix C); owner-session and key checks of this upgrade are BLOCKED. No real data, installed release, provider, community service, or native shell.",
  });
}
