import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { cleanup, createRun, inspect, launch, launchBrowser, ready, stopOnSignals } from "./instance.mjs";
import { childEnvironment, pythonPath, runLogs } from "./prerequisites.mjs";
import { SIGN_IN_PATH, fillSignIn, startRunJellyfin } from "./run-jellyfin.mjs";
import { HEAD, SERVICE, identityFiles, inventory, seedLegacyDatabase, sidecars } from "./upgrade-journey.mjs";

const LINKED_REVISION = "0029_jellyfin_user_blocks";
const LINK = [
  "import json, sqlite3, sys",
  "from pathlib import Path",
  "sys.path.insert(0, sys.argv[2])",
  "from review_repairs_fixture import _link_owner",
  "_link_owner(Path(sys.argv[1]), json.load(sys.stdin))",
  "db = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)",
  "print(json.dumps({'revision': db.execute('SELECT version_num FROM alembic_version').fetchone()[0],",
  "  'users': [list(row) for row in db.execute('SELECT id, role, status, password_hash IS NOT NULL FROM users')],",
  "  'linkedUsers': [row[0] for row in db.execute('SELECT user_id FROM user_provider_identities')]}))",
].join("\n");

/**
 * Seed the same synthetic 0009 database as `seedLegacyDatabase`, then migrate it
 * offline with the real migrations to 0029 and link its owner to the run Jellyfin's
 * administrator with the run's minted key (`review_repairs_fixture._link_owner`), so
 * real startup applies only 0030 and the owner stays active and signs in with Jellyfin.
 */
async function seedLinkedLegacyDatabase(run) {
  seedLegacyDatabase(run);
  const jellyfin = await startRunJellyfin(run);
  await jellyfin.call("POST", "/Auth/Keys?app=Editarr");
  const [key] = await jellyfin.editarrKeys();
  const output = execFileSync(pythonPath(run.root), ["-B", "-P", "-c", LINK, path.join(run.data, "scanner.db"), path.join(run.root, ".github/skills/verify-editarr/scripts")], {
    cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 120000,
    input: JSON.stringify({ base: jellyfin.base, key, serverId: jellyfin.serverId, adminId: jellyfin.admin.id }),
  });
  run.linkedSeed = JSON.parse(output.trim().split("\n").at(-1));
  assert.equal(run.linkedSeed.revision, LINKED_REVISION);
  assert.deepEqual(run.linkedSeed.linkedUsers, [1]);
  run.write("linked-seed.json", run.linkedSeed);
}

/** Save a personal filter rule through the real routes and read it back from the database. */
async function verifyPersonalFilters(run, key) {
  const url = `${run.manifest.origin}/api/settings/filters/personal`;
  const headers = { "X-Api-Key": key, "Content-Type": "application/json", Origin: run.manifest.origin };
  const before = await (await fetch(url, { headers })).json();
  assert.deepEqual([before.mode, before.revision, before.policy_revision], ["live", 0, 0]);
  const personal = { id: "4f6c0d4e-8d1e-4c47-9a35-2d7c2f6f0c11", disposition: "deny", category: "sex", subcategory: "kissing" };
  const body = JSON.stringify({ mode: "live", revision: 0, personal_rules: [personal] });
  assert.equal((await fetch(url, { method: "PUT", headers, body })).status, 200);
  const stale = await fetch(url, { method: "PUT", headers, body });
  assert.equal((await stale.json()).code, "stale_settings_revision", "a replayed revision is refused");
  const after = await (await fetch(url, { headers })).json();
  assert.deepEqual(after.rules.filter(entry => entry.origin === "personal").map(entry => entry.rule.id), [personal.id]);
  assert.equal(after.revision, 1);
  const policy = await fetch(`${run.manifest.origin}/api/settings/filters/users/1/policy`, {
    method: "PUT", headers, body: JSON.stringify({ policy_revision: 0, rules: [], severities: [] }),
  });
  assert.equal((await policy.json()).code, "interactive_session_required", "a key cannot enforce policy");
  assert.deepEqual(inventory(run).personalValues, [[0, 0, 0, 0, 1]], "the personal rule is stored");
  return { personalRule: personal.id, revision: after.revision, policyByKey: "interactive_session_required" };
}

/**
 * Prove the linked owner's 0009 data survived 0009 -> 0029 offline and startup's 0030:
 * the migrated key, archive API and UI, orphan import refusal, personal filter writes,
 * API key reveal, community status, the identity move on first owner session, and one
 * stored audit row per Jellyfin sign-in and reveal.
 */
async function verifyLinkedUpgrade(run) {
  const { root, manifest: { origin }, legacy, jellyfin } = run;
  const { name: username, password } = jellyfin.admin;
  const journal = JSON.parse(fs.readFileSync(path.join(run.data, "migration-upgrade.json"), "utf8"));
  assert.equal(journal.status, "complete");
  assert.equal(journal.from_revision, LINKED_REVISION);
  assert.equal(journal.to_revision, HEAD);
  assert.ok(fs.existsSync(path.join(journal.generation, "snapshot.sqlite3")), "pre-upgrade snapshot retained");
  const oldCookie = await fetch(`${origin}/api/system/version`, { headers: { Cookie: `editarr_session=${legacy.cookie}` } });
  assert.equal(oldCookie.status, 401);
  const keyVersion = await fetch(`${origin}/api/system/version`, { headers: { "X-Api-Key": legacy.key } });
  assert.equal(keyVersion.status, 200, "the linked owner's migrated key works");
  const schema = (await keyVersion.json()).database_schema;
  assert.equal(schema, HEAD);
  const shown = await (await fetch(`${origin}/api/auth/api-key`, { headers: { "X-Api-Key": legacy.key } })).json();
  assert.equal(shown.configured && shown.revealable, true, "migrated key sealed and revealable");
  assert.ok(!JSON.stringify(shown).includes(legacy.key), "status never carries the key");
  const keyReveal = await fetch(`${origin}/api/auth/api-key/reveal`, {
    method: "POST", headers: { "X-Api-Key": legacy.key, "Content-Type": "application/json" },
    body: JSON.stringify({ reauthentication: { password } }),
  });
  assert.equal(keyReveal.status, 403);
  assert.equal((await keyReveal.json()).code, "interactive_session_required", "a key cannot reveal itself");
  const bytes = Buffer.concat(["scanner.db", "scanner.db-wal"].map(name => path.join(run.data, name)).filter(file => fs.existsSync(file)).map(file => fs.readFileSync(file)));
  assert.equal(bytes.includes(Buffer.from("$argon2")), false, "no Argon2 hash bytes remain in the upgraded database");
  const video = path.join(fs.realpathSync(run.media), "Movie.mkv");
  const archive = await fetch(`${origin}/api/archive`, { headers: { "X-Api-Key": legacy.key } });
  assert.equal(archive.status, 200);
  const [archived, ...others] = (await archive.json()).items;
  assert.deepEqual(others, [], "the legacy Other Files item is the only archived item");
  assert.deepEqual([archived.source, archived.title, archived.path], ["manual_files", "Movie.mkv", video]);
  const reviews = archived.reviews.map(review => ({ id: review.id, name: review.name })).sort((a, b) => a.id.localeCompare(b.id));
  assert.deepEqual(reviews, [
    { id: "review-imported-002", name: "Imported review" },
    { id: "review-legacy-001", name: "Synthetic review" },
  ]);
  assert.deepEqual(archived.scans.map(scan => [scan.id, scan.status]).sort(), [
    ["imported-scan-003", "complete"], ["legacy-scan-001", "complete"], ["queued-scan-002", "cancelled"],
  ], "the archive stopped the queued scan");
  assert.deepEqual(archived.exports.map(value => [value.id, value.format, value.scope]), [[1, "edl", "legacy"], [2, "mcf", "legacy"]]);
  const before = inventory(run);
  const form = new FormData();
  form.set("options", JSON.stringify({ target: { source: "manual", video_path: video }, name: "Orphan import" }));
  form.set("file", new Blob(["1 2 1\n"], { type: "text/plain" }), "orphan.edl");
  const orphan = await fetch(`${origin}/api/edl-imports/upload`, { method: "POST", headers: { "X-Api-Key": legacy.key, Origin: origin }, body: form });
  const orphanBody = await orphan.json();
  assert.deepEqual([orphan.status, orphanBody.detail.code], [400, "jellyfin_item_required"], JSON.stringify(orphanBody));
  assert.deepEqual(inventory(run), before, "the refused orphan import wrote nothing");
  const database = inventory(run);
  assert.deepEqual(database.schema, [[HEAD]]);
  assert.deepEqual(database.users, [[1, "owner", "active", null]], "the linked owner stays active through 0030");
  assert.deepEqual(database.identities, [[1]]);
  for (const column of ["password_hash", "local_credential", "password_changed_at"]) assert.ok(!database.userColumns.includes(column), `users has no ${column}`);
  assert.ok(!database.accountSettingsColumns.includes("registration_mode"), "account_settings has no registration_mode");
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
  const filters = await verifyPersonalFilters(run, legacy.key);
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
  let communityStatus;
  const { expect } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
  await launchBrowser(run);
  const viewports = [];
  const externalRequests = [];
  const refusals = [];
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
      if (width === 1440) {
        await fillSignIn(page, username, `${password}-wrong`);
        const refused = page.waitForResponse(response => new URL(response.url()).pathname === SIGN_IN_PATH);
        await page.getByRole("button", { name: "Sign in", exact: true }).click();
        refusals.push((await refused).status());
        assert.equal(refusals.at(-1), 401, "a wrong Jellyfin password is refused");
      }
      await fillSignIn(page, username, password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("heading", { name: "General", exact: true })).toBeVisible();
      const body = await (await context.request.get(`${origin}/api/auth/status`)).json();
      if (body.csrf_token) run.secrets.add(body.csrf_token);
      for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
      assert.equal(body.state, "authenticated");
      assert.deepEqual([body.access.principal.user_id, body.access.principal.role], [1, "owner"], "the Jellyfin administrator signs in to the migrated owner");
      const panel = page.getByRole("region", { name: "API Key", exact: true });
      await expect(panel.getByLabel("Current API key", { exact: true })).toHaveCount(0);
      await panel.getByRole("button", { name: "Show API key", exact: true }).click();
      const confirm = page.getByRole("dialog", { name: "Show API key?" });
      await confirm.getByLabel("Jellyfin password", { exact: true }).fill(password);
      await confirm.getByRole("button", { name: "Show key", exact: true }).click();
      assert.ok(await panel.getByLabel("Current API key", { exact: true }).inputValue() === legacy.key, "API key panel reveals the migrated key after Jellyfin password confirmation");
      if (communityStatus === undefined) {
        const response = await context.request.get(`${origin}/api/community/connection/status`);
        assert.equal(response.status(), 200);
        const status = await response.json();
        communityStatus = {
          status: response.status(), installationId: status.identity.installationId, keyId: status.identity.keyId,
          connected: status.connected, pendingRecovery: status.pendingRecovery,
        };
        assert.deepEqual(communityStatus, {
          status: 200, installationId: run.identity.installationId, keyId: run.identity.keyId, connected: true, pendingRecovery: true,
        });
        const named = await context.request.get(`${origin}/api/users/1/community/connection/status`);
        assert.equal(named.status(), 200);
        assert.deepEqual(await named.json(), status, "the owner's named-account route reads the same migrated identity");
        communityStatus.namedRoute = named.status();
      }
      await capture(page, run, `upgraded-linked-owner-${width}`);
      await page.goto(`${origin}/#/archive`);
      await expect(page.getByRole("heading", { name: "Archive", exact: true })).toBeVisible();
      const entry = page.locator(".archive-item").filter({ has: page.getByRole("heading", { name: "Movie.mkv", exact: true }) });
      await expect(entry.getByText("Other Files · Video", { exact: true })).toBeVisible();
      const listed = entry.getByRole("region", { name: "Reviews", exact: true });
      await expect(listed.getByText("Synthetic review", { exact: true })).toBeVisible();
      await expect(listed.getByText("Imported review", { exact: true })).toBeVisible();
      assert.equal(await entry.getByRole("button").count(), 0, "the archived entry offers no action");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `archive overflows at ${width}`);
      await capture(page, run, `upgraded-archive-${width}`);
      viewports.push({ width, state: body.state, ownerId: body.access.principal.user_id, apiKeyFieldMasked: true, archivedReviewsVisible: 2 });
    } finally {
      for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
      await context.close();
    }
  }
  assert.equal(externalRequests.length, 0, "Unexpected browser requests outside the owned origin");
  assert.equal(identityFiles(incoming), null, "incoming identity directory emptied");
  assert.deepEqual(identityFiles(home), run.identity.files, "identity bytes unchanged in the owner's home");
  assert.equal(fs.statSync(home).mode & 0o777, 0o700);
  const homes = fs.readdirSync(path.dirname(home)).filter(name => fs.statSync(path.join(path.dirname(home), name)).isDirectory());
  assert.deepEqual(homes, [reference], "no second identity");
  const audit = fs.readFileSync(path.join(runLogs(run.data), "auth-audit.jsonl"));
  assert.ok(audit.subarray(0, run.auditBefore.length).equals(run.auditBefore), "legacy audit records unchanged");
  const after = inventory(run);
  assert.deepEqual(after.communityOperations, database.communityOperations, "no actor added to legacy operations");
  const appended = audit.subarray(run.auditBefore.length).toString();
  const logged = appended.trim().split("\n").filter(Boolean).map(line => JSON.parse(line)).filter(record => record.event === "auth.login");
  run.write("linked-audit.json", { storedAuditEvents: after.auditEvents, loggedSignIns: logged.map(record => [record.outcome, record.http_status, record.route, record.http_method]) });
  const signIn = (outcome, kind, actor, credential, details) => ["auth.sign_in", outcome, kind, actor, credential, actor === null ? null : 1, null, null, details];
  const signedIn = signIn("succeeded", "user", 1, "session", '{"method":"password","remember_me":true}');
  const revealed = ["api_key.reveal", "succeeded", "user", 1, "session", 1, null, null, "{}"];
  assert.deepEqual(after.auditEvents, [
    signIn("failed", "anonymous", null, null, '{"method":"password","reason":"invalid_credentials"}'), signedIn, revealed, signedIn, revealed,
  ], "each Jellyfin sign-in and reveal stored once and attributed to the owner, nothing invented for legacy activity");
  const route = "/api/auth/jellyfin/login";
  assert.deepEqual(logged.map(record => [record.outcome, record.http_status, record.route, record.reason_code, record.admin_user_id]), [
    ["failure", 401, route, "invalid_credentials", null], ["success", 200, route, null, 1], ["success", 200, route, null, 1],
  ], "each Jellyfin sign-in also appends one auth.login line to the file log, after the legacy records");
  const storedText = JSON.stringify(after.auditEvents);
  for (const secret of run.secrets) {
    assert.ok(!storedText.includes(secret) && !appended.includes(secret), "no secret in audit rows or appended log lines");
  }
  const sidecarsAfter = sidecars(run.media);
  assert.deepEqual(sidecarsAfter, run.sidecarsBefore, "legacy sidecars and media unchanged");
  run.write("result.json", {
    feature: "mu-activity-migration", phase: "linked-owner", passed: true, seededRevision: "0009_readable_api_key", sourceRevision: journal.from_revision, schema,
    snapshotRetained: true, oldCookieStatus: oldCookie.status, apiKeyStatus: keyVersion.status, apiKeySealedAndRevealedUnchanged: true, argon2BytesRemaining: false,
    archive: archived, orphanImport: { status: orphan.status, code: orphanBody.detail.code }, filters, reviews, database, refusedSignIns: refusals,
    community: { reference, identityOwner, connections: database.communityConnections, operations: database.communityOperations, identityFiles: identityFiles(home), status: communityStatus, homes, legacyAuditPreserved: true, storedAuditEvents: after.auditEvents, loggedSignIns: logged.length },
    sidecars: sidecarsAfter, viewports, externalBrowserRequests: externalRequests,
    scope: "The synthetic 0009 fixture migrated offline with the real migrations to 0029, its owner linked to the run's Jellyfin 12 administrator, then upgraded to 0030 by real source startup and driven signed in with Jellyfin. No real data, installed release, provider, community service, or native shell.",
  });
}

/** The second `upgrade` phase: a fresh run whose 0009 owner is linked before startup. */
export async function runLinkedUpgrade(root, portRange) {
  const run = createRun(root, { portRange });
  stopOnSignals(run);
  let exitCode = 0;
  try {
    await seedLinkedLegacyDatabase(run);
    await launch(run);
    console.log(`Evidence: ${run.evidence}`);
    await ready(run, "unauthenticated");
    const report = await inspect(run.manifest, root);
    run.write("doctor.json", report);
    await verifyLinkedUpgrade(run);
    console.log("PASS: the 0009 fixture migrated offline to 0029 with its owner linked to Jellyfin, then startup's 0030 kept the owner active; the migrated key, archive API and UI, orphan import refusal, personal filter writes, API key reveal, community status, identity move, and sign-in audit rows all held at 1440 and 320.");
  } catch (error) {
    run.write("failure.json", { message: error.message, stack: error.stack });
    console.error(error.message);
    exitCode = 1;
  } finally {
    try { await cleanup(run); }
    catch (error) { console.error(`Cleanup failed: ${error.message}`); exitCode = 1; }
    try { await run.stopJellyfin?.(); }
    catch (error) { console.error(`Jellyfin cleanup failed: ${error.message}`); exitCode = 1; }
  }
  return exitCode;
}
