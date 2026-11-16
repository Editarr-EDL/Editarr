import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID, createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { query } from "./database.mjs";
import { capture } from "./capture.mjs";
import { chooseSeverity, defaultFiltersHeading, modeRadio, myFiltersHeading, openFilters, saveFiltersButton, severityChip } from "./filters-page.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";
import { verifyExportRegeneration } from "./export-regeneration-journey.mjs";
import { verifyAccountLink } from "./account-link-journey.mjs";
import { verifyUploadImportsOnly } from "./review-upload-imports.mjs";
import { FILM, itemRoute } from "./synthetic-jellyfin.mjs";
import { SIGN_IN_PATH, addJellyfinUser, fillSignIn } from "./run-jellyfin.mjs";

export async function verifyReviewPolicy(run, owner, media, target) {
  const { request, expect } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const { default: AxeBuilder } = createRequire(path.join(run.root, "web/package.json"))("@axe-core/playwright");
  const origin = run.manifest.origin;
  const contexts = [];
  const requests = [];
  const browserWrites = [];
  const failures = [];
  const password = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  let chosen;
  const username = "policy-review-member";
  let browserContext;
  let page;
  async function actor() {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    contexts.push(context);
    let csrf;
    return {
      async send(method, url, data, multipart, binary = false) {
        const response = await context.fetch(url, { method, headers: csrf ? { "X-CSRF-Token": csrf } : {}, ...(multipart ? { multipart } : data ? { data } : {}) });
        const body = response.status() === 204 ? {} : binary ? await response.body() : await response.json();
        if (body.csrf_token) { csrf = body.csrf_token; run.secrets.add(csrf); }
        for (const cookie of (await context.storageState()).cookies) run.secrets.add(cookie.value);
        return { status: response.status(), body };
      },
    };
  }
  const fresh = { reauthentication: { password: owner.password } };
  const api = async (actor, method, url, data, status = 200, multipart) => {
    const result = await actor.send(method, url, data, multipart);
    requests.push({ method, path: url, status: result.status });
    assert.equal(result.status, status, `${method} ${url}: ${JSON.stringify(result.body)}`);
    return result.body;
  };
  const draft = detail => Object.fromEntries(["revision", "name", "memberships", "filters", "mcf_document", "synchronization", "accepted_credits", "policy_owner_id", "policy_revision"].map(key => [key, detail[key]]));
  const hash = file => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  try {
    const admin = await actor();
    await api(admin, "POST", SIGN_IN_PATH, { username: owner.username, password: owner.password });
    ({ password: chosen } = await addJellyfinUser(run, username));
    const member = await actor();
    const created = { id: (await api(member, "POST", SIGN_IN_PATH, { username, password: chosen })).access.principal.user_id };
    const mcf = "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Policy proof\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n00:00:01.000 --> 00:00:02.000\nviolence=low=video\n";
    const imported = await api(member, "POST", "/api/mcf-imports/upload", null, 201, {
      options: JSON.stringify({ target, name: "Policy proof", synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
      file: { name: "policy.mcf", mimeType: "text/vtt", buffer: Buffer.from(mcf) },
    });
    const reviewId = imported.review.id;
    await verifyUploadImportsOnly(run, { api, owner: admin, member, target, media, sameMediaAs: reviewId });
    const url = `/api/reviews/${reviewId}`;
    const communityStatus = {
      target, target_key: "a".repeat(64),
      choices: [{ contributionId: "mu29-absent", revisionId: "mu29-absent", artifactDigest: "0".repeat(64), channels: ["video"] }],
    };
    const communityInventory = () => query(run, "SELECT (SELECT count(*) FROM scans) AS scans, (SELECT count(*) FROM saved_reviews) AS reviews, (SELECT count(*) FROM community_imports) AS imports, (SELECT count(*) FROM community_operations) AS operations")[0];
    const inventory = communityInventory();
    const available = await api(member, "POST", "/api/community/downloads/status", communityStatus);
    assert.equal(available.outcomes[0].status, "not_imported");
    await api(admin, "PATCH", `/api/users/${created.id}/permissions`, { overrides: { "community.download": "deny" }, ...fresh });
    const denied = await api(member, "POST", "/api/community/downloads/status", communityStatus, 403);
    assert.equal(denied.code, "capability_denied");
    assert.deepEqual(communityInventory(), inventory);
    await api(admin, "PATCH", `/api/users/${created.id}/permissions`, { overrides: { "community.download": "inherit" }, ...fresh });
    assert.equal((await api(member, "POST", "/api/community/downloads/status", communityStatus)).outcomes[0].status, "not_imported");
    run.write("community-activities-result.json", { passed: true, member: created.id, statuses: [200, 403, 200], inventoryUnchanged: true, inventory, scope: "Real authenticated source app and Jellyfin-item community status. No service request, enrollment, remote publication, vote or receipt is claimed. Signed service-double coverage is in the MU-29 pytest slice." });
    const base = draft(imported.review);
    base.filters = imported.review.base_filters.map(value => ({ ...value, selected: false }));
    base.filter_basis = "base";
    const saved = await api(member, "PUT", url, base);
    assert.equal(saved.filters[0].selected, false);
    const listed = await api(member, "GET", `/api/library-items/${target.item_id}/reviews?media_source_id=${target.media_source_id}`);
    assert.deepEqual(listed.map(review => review.id), [reviewId]);
    const peer = (await api(admin, "GET", `/api/library-items/${target.item_id}/reviews?media_source_id=${target.media_source_id}`)).find(review => review.id !== reviewId);
    assert.ok(peer, "An owner-private review exists on the same visible media");
    for (const suffix of ["", "/mcf/preflight", "/exports"]) await api(member, "GET", `/api/reviews/${peer.id}${suffix}`, null, 404);
    const detachable = await api(member, "POST", `${url}/duplicate`, {
      revision: saved.revision, policy_owner_id: saved.policy_owner_id,
      policy_revision: saved.policy_revision, name: "Legitimately detached evidence",
    }, 201);
    const detachedUrl = `/api/reviews/${detachable.id}`;
    await api(member, "PUT", detachedUrl, { ...draft(detachable), memberships: [], filters: [] });
    const incomplete = await api(member, "POST", `${url}/duplicate`, {
      revision: saved.revision, policy_owner_id: saved.policy_owner_id,
      policy_revision: saved.policy_revision, name: "Missing policy evidence",
    }, 201);
    const incompleteUrl = `/api/reviews/${incomplete.id}`;
    await api(member, "PUT", incompleteUrl, { ...draft(incomplete), filters: [] });
    const disposable = await api(member, "POST", "/api/mcf-imports/upload", null, 201, {
      options: JSON.stringify({ target, name: "Disposable policy evidence", synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
      file: { name: "disposable.mcf", mimeType: "text/vtt", buffer: Buffer.from(mcf) },
    });
    const disposableScan = disposable.review.memberships[0].scan_id;
    await api(member, "DELETE", `/api/reviews/${disposable.review.id}?revision=${disposable.review.revision}`, null, 204);
    await api(member, "DELETE", `/api/scans/${disposableScan}`);
    const admission = JSON.parse(execFileSync(pythonPath(run.root), ["-B", "-c", [
      "import json, pathlib, sqlite3, sys, scanner",
      "from scanner.persistence.migrations import adoption",
      "from scanner.persistence.migrations.preflight import inspect_schema",
      "assert pathlib.Path(scanner.__file__).resolve().is_relative_to(pathlib.Path.cwd())",
      "database = pathlib.Path(sys.argv[1])",
      "revision = inspect_schema(database, pathlib.Path(sys.argv[2]))",
      "assert revision == adoption.HEAD_REVISION",
      "with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:",
      "    foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()",
      "    integrity = connection.execute('PRAGMA integrity_check').fetchall()",
      "    remaining = connection.execute('SELECT count(*) FROM review_policy_evidence WHERE review_id = ?', (sys.argv[3],)).fetchone()[0]",
      "assert foreign_keys == [] and integrity == [('ok',)] and remaining == 0",
      "print(json.dumps(dict(revision=revision, foreignKeys=foreign_keys, integrity=integrity, deletedReviewBindings=remaining)))",
    ].join("\n"), path.join(run.data, "scanner.db"), run.data, disposable.review.id], {
      cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8",
    }));
    run.write("review-policy-deletion-admission.json", admission);
    const instance = await api(admin, "GET", "/api/settings/filters");
    const ruleId = randomUUID();
    await api(admin, "PUT", "/api/settings/filters", { ...instance, selection_rules: [...instance.selection_rules, { id: ruleId, disposition: "deny", category: "violence" }], severity_defaults: instance.severity_defaults.map(value => value.subcategory === "violence" ? { ...value, severity: "high" } : value), ...fresh });
    const policyUrl = `/api/settings/filters/users/${created.id}`;
    const before = await api(admin, "GET", policyUrl);
    const enforced = await api(admin, "PUT", `${policyUrl}/policy`, { policy_revision: before.policy_revision, rules: [ruleId], severities: ["violence"], ...fresh });
    let detail = await api(member, "GET", url);
    assert.equal(detail.policy_owner_id, created.id);
    assert.equal(detail.policy_revision, enforced.policy_revision);
    assert.deepEqual([detail.filters[0].selected, detail.filters[0].severity], [true, "high"]);
    assert.deepEqual([detail.base_filters[0].selected, detail.base_filters[0].severity], [false, "low"]);
    assert.deepEqual(detail.policy_required_filter_ids, [detail.filters[0].id]);
    assert.equal(detail.policy_locked_severities[detail.filters[0].id], "high");
    const normalized = await api(member, "PUT", url, { ...draft(detail), filters: detail.filters.map(filter => ({ ...filter, selected: false, severity: "low" })) });
    assert.deepEqual([normalized.filters[0].selected, normalized.filters[0].severity], [true, "high"]);
    const bypass = await api(member, "PUT", url, { ...draft(normalized), memberships: [], filters: [] }, 409);
    assert.equal(bypass.detail.code, "review_policy_enforced");
    const detached = await api(member, "GET", detachedUrl);
    assert.deepEqual(detached.policy_missing_evidence, []);
    assert.deepEqual(detached.memberships, []);
    const stale = await api(member, "PUT", url, draft(saved), 409);
    assert.equal(stale.detail.code, "stale_review_policy");
    const unchanged = await api(member, "PUT", url, draft(detail));
    assert.equal(unchanged.base_filters[0].selected, false);
    const missing = await api(member, "GET", incompleteUrl);
    assert.equal(missing.policy_missing_evidence.length, 1);
    for (const format of ["edl", "mcf"]) {
      const refused = await api(member, "POST", `${incompleteUrl}/${format}`, {
        revision: missing.revision, policy_owner_id: created.id,
        policy_revision: missing.policy_revision,
      }, 409);
      assert.equal(refused.detail.code, "review_policy_evidence_missing");
    }
    const renamedMissing = await api(member, "PATCH", incompleteUrl, {
      revision: missing.revision, name: "Repairable policy evidence",
    });
    const removedEvidence = await api(member, "PUT", incompleteUrl, {
      ...draft(renamedMissing), memberships: [], filters: [],
    }, 409);
    assert.equal(removedEvidence.detail.code, "review_policy_enforced");
    const movedEvidence = await api(member, "PUT", incompleteUrl, {
      ...draft(renamedMissing), filters: incomplete.filters.map(value => ({
        ...value, start_seconds: 2.5, end_seconds: 3.5,
      })),
    }, 409);
    assert.equal(movedEvidence.detail.code, "review_policy_enforced");
    for (const format of ["edl", "mcf"]) {
      const refused = await api(member, "POST", `${incompleteUrl}/${format}`, {
        revision: renamedMissing.revision, policy_owner_id: created.id,
        policy_revision: renamedMissing.policy_revision,
      }, 409);
      assert.equal(refused.detail.code, "review_policy_evidence_missing");
    }
    const repaired = await api(member, "PUT", incompleteUrl, {
      ...draft(renamedMissing), filters: incomplete.filters,
    });
    assert.deepEqual(repaired.policy_missing_evidence, []);
    assert.equal(repaired.filters[0].selected, true);
    await api(member, "DELETE", `${incompleteUrl}?revision=${repaired.revision}`, null, 204);
    const sidecars = [];
    for (const format of ["edl", "mcf"]) {
      const old = await api(admin, "POST", `${url}/${format}`, { revision: detail.revision, policy_owner_id: created.id, policy_revision: 0 }, 409);
      assert.equal(old.detail.code, "stale_review_policy");
      const result = await api(admin, "POST", `${url}/${format}`, { revision: unchanged.revision, policy_owner_id: created.id, policy_revision: detail.policy_revision });
      assert.equal(result.policy_owner_id, created.id);
      assert.equal(result.policy_revision, detail.policy_revision);
      const [{ destination_path: file }] = query(run, "SELECT destination_path FROM media_item_exports WHERE review_id = ? AND format = ? AND scope = 'personal'", reviewId, format);
      const content = fs.readFileSync(file, "utf8");
      if (format === "edl") assert.deepEqual(content.split("\n").filter(line => line && !line.startsWith("#")), ["1.000 2.000 3"]);
      else assert.ok(content.includes("violence=high=video"));
      fs.copyFileSync(file, path.join(run.evidence, `policy.${format}`));
      sidecars.push({ format, sha256: hash(file), bytes: fs.statSync(file).size });
    }
    const [stored] = query(run, "SELECT f.selected, f.severity, s.severity AS source_severity FROM review_filters f JOIN scan_findings s ON s.id = f.source_finding_id WHERE f.review_id = ?", reviewId);
    assert.deepEqual(stored, { selected: 0, severity: "low", source_severity: "low" });
    assert.deepEqual(query(run, "PRAGMA foreign_key_check"), []);
    assert.equal(query(run, "SELECT count(*) AS remaining FROM review_policy_evidence WHERE review_id = ?", incomplete.id)[0].remaining, 0);
    admission.postPolicyDeletionIntegrity = true;
    run.write("review-policy-deletion-admission.json", admission);
    async function memberPage(width, unlockedMode) {
      const context = await run.browser.newContext({ viewport: { width, height: 900 } });
      await context.route("**/*", route => {
        const candidate = new URL(route.request().url());
        if (["http:", "https:"].includes(candidate.protocol) && candidate.origin !== origin) { failures.push(`External browser request ${candidate.origin}`); return route.abort("blockedbyclient"); }
        return route.continue();
      });
      const memberPage = await context.newPage();
      memberPage.setDefaultTimeout(20000);
      memberPage.on("pageerror", error => failures.push(error.message));
      try {
        await memberPage.goto(`${origin}/${itemRoute({ id: target.item_id })}`);
        await fillSignIn(memberPage, username, chosen);
        await memberPage.getByRole("button", { name: "Sign in", exact: true }).click();
        await expect(memberPage.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
        if (unlockedMode) {
          await openFilters(memberPage, origin);
          await expect(myFiltersHeading(memberPage)).toBeVisible();
          const mode = modeRadio(memberPage, "mine", unlockedMode);
          await expect(mode).toBeEnabled();
          await mode.check();
          await expect(memberPage.getByText(unlockedMode === "copy" ? "You now use your own copy." : "You now keep in sync with the server.", { exact: true })).toBeVisible();
          const savedMode = await api(member, "GET", "/api/settings/filters/personal");
          assert.deepEqual([savedMode.mode, savedMode.mode_locked], [unlockedMode, false]);
          assert.ok(await memberPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
          await capture(memberPage, run, `mu33-member-mode-${unlockedMode}-${width}`);
          const modeAxe = await new AxeBuilder({ page: memberPage }).analyze();
          assert.deepEqual(modeAxe.violations, [], `Unlocked mode axe at ${width}`);
          run.write(`mu33-mode-${width}.json`, { mode: savedMode.mode, locked: savedMode.mode_locked, violations: modeAxe.violations });
          return;
        }
        await expect(memberPage.getByRole("button", { name: `Open review ${peer.name}`, exact: true })).toHaveCount(0);
        await memberPage.getByRole("button", { name: "Open review Policy proof", exact: true }).click();
        const panel = memberPage.locator(".saved-review-panel");
        await expect(panel.getByRole("heading", { name: "Policy proof", exact: true })).toBeVisible();
        await expect(panel.getByRole("button", { name: "Save Changes", exact: true })).toBeDisabled();
        await expect(panel.getByRole("button", { name: "Review Credits Result", exact: true })).toBeDisabled();
        await capture(memberPage, run, `mu33-member-before-${width}`);
        const timing = panel.getByRole("button", { name: "Adjust Cue Times", exact: true });
        await timing.click();
        const timingDialog = memberPage.getByRole("dialog", { name: "Adjust Cue Times", exact: true });
        await expect(timingDialog.getByLabel("Proposed START", { exact: true })).toBeFocused();
        await timingDialog.getByLabel("Proposed START", { exact: true }).fill("00:00:00.100");
        await timingDialog.getByRole("button", { name: "Cancel", exact: true }).click();
        const video = panel.getByRole("region", { name: "Video review filters", exact: true });
        await video.getByRole("button", { name: /subcategory, 1 result/ }).click();
        await video.getByRole("button", { name: /finding group, 1 result/ }).click();
        await expect(video.getByRole("checkbox").first()).toBeDisabled();
        await video.getByRole("button", { name: `Edit filter ${detail.filters[0].id}`, exact: true }).click();
        await expect(video.getByRole("radio", { name: "High", exact: true })).toBeChecked();
        await expect(video.getByRole("radio", { name: "Low", exact: true })).toBeDisabled();
        await capture(memberPage, run, `mu33-member-locks-${width}`);
        const required = detail.filters[0];
        const cueEnd = video.getByLabel(`Filter ${required.id} end seconds`, { exact: true });
        const cueLabel = video.getByLabel(`Filter ${required.id} label`, { exact: true });
        const retainedLabel = `Retained cue edits ${width}`;
        await cueEnd.fill(String((required.start_seconds + required.end_seconds) / 2));
        await cueLabel.fill(retainedLabel);
        const refusedSave = memberPage.waitForResponse(response => response.url().endsWith(url) && response.request().method() === "PUT");
        await panel.getByRole("button", { name: "Save Changes", exact: true }).click();
        const refusal = await refusedSave;
        assert.equal(refusal.status(), 409);
        const refusalBody = await refusal.json();
        assert.equal(refusalBody.detail.code, "review_policy_enforced");
        await expect(panel.getByRole("alert")).toHaveText(refusalBody.detail.message);
        await expect(panel.getByRole("alert", { name: "Review conflict" })).toHaveCount(0);
        await expect(cueEnd).toBeEnabled();
        await expect(cueLabel).toHaveValue(retainedLabel);
        await capture(memberPage, run, `mu33-editable-refusal-${width}`);
        await cueEnd.fill(String(required.end_seconds));
        const correctedSave = memberPage.waitForResponse(response => response.url().endsWith(url) && response.request().method() === "PUT");
        await panel.getByRole("button", { name: "Save Changes", exact: true }).click();
        assert.equal((await correctedSave).status(), 200);
        const corrected = await api(member, "GET", url);
        assert.equal(corrected.filters[0].label, retainedLabel);
        assert.equal(corrected.filters[0].end_seconds, required.end_seconds);
        await cueLabel.fill(required.label || "");
        const restoredSave = memberPage.waitForResponse(response => response.url().endsWith(url) && response.request().method() === "PUT");
        await panel.getByRole("button", { name: "Save Changes", exact: true }).click();
        assert.equal((await restoredSave).status(), 200);
        detail = await api(member, "GET", url);
        run.write(`mu33-editable-refusal-${width}.json`, { refused: 409, code: refusalBody.detail.code, editable: true, correctedSaved: true, preservedLabel: true, restored: true });
        const axe = await new AxeBuilder({ page: memberPage }).analyze();
        assert.deepEqual(axe.violations, [], `Member review axe at ${width}`);
        run.write(`mu33-review-axe-${width}.json`, { violations: axe.violations });
        if (width === 1440) {
          await panel.getByRole("button", { name: "Import", exact: true }).click();
          const dialog = memberPage.getByRole("dialog", { name: "Import", exact: true });
          await expect(dialog).toBeVisible();
          await expect(dialog.getByLabel("EDL file", { exact: true })).toBeVisible();
          await expect(dialog.getByRole("button", { name: "Scanner path", exact: true })).toHaveCount(0);
          await dialog.getByLabel("Import review name", { exact: true }).fill("Member browser upload");
          await dialog.getByLabel("EDL file", { exact: true }).setInputFiles({ name: "member.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") });
          const completed = memberPage.waitForResponse(response => response.url().endsWith("/api/edl-imports/upload") && response.request().method() === "POST");
          await dialog.getByRole("button", { name: "Import EDL", exact: true }).click();
          const response = await completed;
          assert.equal(response.status(), 201);
          const uploaded = (await response.json()).review;
          assert.equal((await api(member, "GET", `/api/reviews/${uploaded.id}`)).owner_id, created.id);
          await expect(panel.getByRole("heading", { name: "Member browser upload", exact: true })).toBeVisible();
          await panel.getByRole("button", { name: "Adjust Cue Times", exact: true }).click();
          const retime = memberPage.getByRole("dialog", { name: "Adjust Cue Times", exact: true });
          await expect(retime).toBeVisible();
          await retime.getByLabel("Proposed START", { exact: true }).fill("00:00:00.100");
          const adjusted = memberPage.waitForResponse(response => response.url().endsWith(`/api/reviews/${uploaded.id}/retime`) && response.request().method() === "POST");
          await retime.getByRole("button", { name: "Apply to draft", exact: true }).click();
          assert.equal((await adjusted).status(), 200);
          const persisted = memberPage.waitForResponse(response => response.url().endsWith(`/api/reviews/${uploaded.id}`) && response.request().method() === "PUT");
          await panel.getByRole("button", { name: "Save Changes", exact: true }).click();
          const savedResponse = await persisted;
          assert.equal(savedResponse.status(), 200, await savedResponse.text());
          assert.equal((await api(member, "GET", `/api/reviews/${uploaded.id}`)).synchronization.start_seconds, 0.1);
          await capture(memberPage, run, "mu33-member-import-retime-saved-1440");
        }
        await openFilters(memberPage, origin);
        await expect(myFiltersHeading(memberPage)).toBeVisible();
        await expect(memberPage.getByText("An admin requires some of your filters, so they manage this choice for you.", { exact: true })).toBeVisible();
        for (const value of ["live", "copy"]) await expect(modeRadio(memberPage, "mine", value)).toBeDisabled();
        await expect(defaultFiltersHeading(memberPage)).toHaveCount(0);
        await expect(severityChip(memberPage, "Violence (General)")).toHaveAccessibleName(/required by an admin$/);
        if (width === 1440) {
          await chooseSeverity(memberPage, "Swearing", "low");
          await saveFiltersButton(memberPage).click();
          await expect(memberPage.getByText("Your filters are saved.", { exact: true })).toBeVisible();
          assert.equal((await api(member, "GET", "/api/settings/filters/personal")).severities.find(value => value.subcategory === "swearing").severity, "low");
        }
        await capture(memberPage, run, `mu33-member-preferences-${width}`);
        const preferencesAxe = await new AxeBuilder({ page: memberPage }).analyze();
        assert.deepEqual(preferencesAxe.violations, [], `Member preferences axe at ${width}`);
        run.write(`mu33-preferences-axe-${width}.json`, { violations: preferencesAxe.violations });
      } catch (error) {
        await capture(memberPage, run, `mu33-member-failure-${width}`).catch(() => {});
        throw error;
      } finally {
        for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
        await context.close();
      }
    }
    await memberPage(1440);
    await memberPage(320);
    detail = await api(member, "GET", url);
    browserContext = await run.browser.newContext({ viewport: { width: 1440, height: 900 } });
    await browserContext.route("**/*", route => {
      const url = new URL(route.request().url());
      if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) {
        failures.push(`External browser request ${url.origin}`);
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    page = await browserContext.newPage();
    page.setDefaultTimeout(20000);
    page.on("pageerror", error => failures.push(error.message));
    await page.goto(`${origin}/${itemRoute({ id: target.item_id })}`);
    await fillSignIn(page, owner.username, owner.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: FILM.title, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Open review Policy proof", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Policy proof", exact: true })).toBeVisible();
    const browserWrite = async (method, suffix, action, policyRevision, status = 200) => {
      const responsePromise = page.waitForResponse(response => (
        response.request().method() === method && response.url() === `${origin}${url}${suffix}`
      ));
      await action();
      const response = await responsePromise;
      const sent = response.request().postDataJSON();
      assert.equal(sent.policy_owner_id, created.id);
      assert.equal(sent.policy_revision, policyRevision);
      assert.equal(response.status(), status);
      browserWrites.push({ method, suffix, policyOwner: sent.policy_owner_id, policyRevision, status });
      return response.json();
    };
    const comment = page.getByLabel("MCF document comment", { exact: true });
    const documentEditor = page.locator(".mcf-document-editor");
    if (await documentEditor.getAttribute("open") === null) await documentEditor.locator(":scope > summary").click();
    await comment.fill("Browser policy save");
    await browserWrite("PUT", "", () => page.getByRole("button", { name: "Save Changes", exact: true }).click(), detail.policy_revision);
    await expect(page.getByRole("button", { name: "Save Changes", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Export", exact: true }).click();
    const exportDialog = page.getByRole("dialog", { name: "Export", exact: true });
    for (const format of ["edl", "mcf"]) {
      const sent = await browserWrite("POST", `/exports/${format}`, () => exportDialog.getByRole("button", { name: `Publish ${format.toUpperCase()}`, exact: true }).click(), detail.policy_revision);
      assert.deepEqual([sent.target, sent.policy_owner_id], ["self", created.id]);
    }
    await exportDialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByRole("button", { name: "Export", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Duplicate", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Duplicate Review", exact: true })).toBeVisible();
    await page.getByLabel("Duplicate review name", { exact: true }).fill("Browser policy copy");
    const copy = await browserWrite("POST", "/duplicate", () => page.getByRole("dialog").getByRole("button", { name: "Duplicate", exact: true }).click(), detail.policy_revision, 201);
    assert.notEqual(copy.owner_id, created.id);
    assert.equal(copy.policy_owner_id, copy.owner_id);
    assert.equal(copy.original_author_id, created.id);
    const copiedRead = await api(admin, "GET", `/api/reviews/${copy.id}`);
    assert.deepEqual([copiedRead.filters[0].selected, copiedRead.filters[0].severity], [false, "low"]);
    await page.getByRole("button", { name: "Open review Policy proof", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Policy proof", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Export", exact: true })).toBeEnabled();
    await capture(page, run, "review-policy-1440");
    const latestPolicy = await api(admin, "GET", policyUrl);
    const restored = await verifyExportRegeneration(run, { api, admin, member, policyUrl, detail: { ...detail, policy_revision: latestPolicy.policy_revision }, url, fresh });
    assert.deepEqual([restored.filters[0].selected, restored.filters[0].severity], [false, "low"]);
    if (await documentEditor.getAttribute("open") === null) await documentEditor.locator(":scope > summary").click();
    await comment.fill("Stale browser draft");
    await browserWrite("PUT", "", () => page.getByRole("button", { name: "Save Changes", exact: true }).click(), detail.policy_revision, 409);
    await expect(page.getByRole("button", { name: "Save Changes", exact: true })).toBeDisabled();
    await expect(comment).toHaveValue("Stale browser draft");
    await capture(page, run, "mu33-conflict-retained-1440");
    await browserWrite("PUT", "", () => page.getByRole("button", { name: "Save retained draft with latest policy", exact: true }).click(), restored.policy_revision);
    await expect(page.getByRole("button", { name: "Save Changes", exact: true })).toBeDisabled();
    await page.setViewportSize({ width: 320, height: 900 });
    await page.reload();
    await page.getByRole("button", { name: "Open review Policy proof", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Policy proof", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Export", exact: true })).toBeEnabled();
    await capture(page, run, "review-policy-restored-320");
    await memberPage(1440, "copy");
    await memberPage(320, "live");
    assert.equal(hash(media.file), media.sha256);
    assert.deepEqual(failures, []);
    run.write("review-policy-result.json", { passed: true, feature: "MU-33", policyOwner: created.id, policyRevision: detail.policy_revision, exportingActorIsReviewOwner: false, exportingActorIsInstanceOwner: true, staleSave: 409, staleExports: [409, 409], privateGuessedIds: [404, 404, 404], enforcementBypass: 409, ownerCopy: { owner: copy.owner_id, author: copy.original_author_id, refreshedSelection: false, refreshedSeverity: "low" }, missingEvidenceRepaired: true, preEnforcementDetachAllowed: true, deletionAdmission: admission, browserWrites, stored, sidecars, restoration: { selected: false, severity: "low" }, mediaUnchanged: true, scope: "Real member upload, edit, timing save, policy locks, personal values and owner copy/export UI at 1440/320 with axe. The member reaches the synthetic Jellyfin item through its default media scope; server-path imports remain denied and hidden. Credits entry is disabled without a candidate. Independent SQL and sidecar checks. No provider, inference, completed credits, or installed-runtime claim." });
    if (run.accountLink) {
      await verifyAccountLink(run, { api, admin, owner, member: { actor: member, id: created.id, username, password: chosen, reviewUrl: url } });
    }
  } catch (error) {
    if (page && !page.isClosed()) await capture(page, run, "review-policy-failure").catch(() => {});
    throw error;
  } finally {
    run.write("review-policy-http-statuses.json", requests);
    run.write("review-policy-browser-errors.json", failures);
    if (browserContext) {
      for (const cookie of await browserContext.cookies()) run.secrets.add(cookie.value);
      await browserContext.close();
    }
    for (const context of contexts) await context.dispose();
  }
}