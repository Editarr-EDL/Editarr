import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { query } from "./database.mjs";

const digest = content => createHash("sha256").update(content).digest("hex");

export async function regenerationStatus(actor, exportId) {
  const url = `/api/exports/${exportId}`;
  const response = await actor.send("GET", url);
  if (response.status === 409 && response.body.detail?.code === "stale_export_revision") return null;
  assert.equal(response.status, 200, `GET ${url}: ${JSON.stringify(response.body)}`);
  return response.body;
}

const publications = (run, reviewId) => query(run, "SELECT e.id, e.destination_path, e.content_bytes, e.snapshot_json, e.policy_revision, e.publication_revision, e.exported_format_revision, j.state AS regeneration_state, j.due_at AS regeneration_due_at FROM media_item_exports e LEFT JOIN media_item_export_regenerations j ON j.export_id = e.id WHERE e.review_id = ? AND e.scope = 'personal' ORDER BY e.format", reviewId);

export async function verifyExportRegeneration(run, { api, admin, member, policyUrl, detail, url, fresh }) {
  const { expect } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const before = publications(run, detail.id);
  assert.equal(before.length, 2);
  for (const row of before) assert.deepEqual(fs.readFileSync(row.destination_path), Buffer.from(row.content_bytes));
  const changed = await api(admin, "PUT", `${policyUrl}/policy`, { policy_revision: detail.policy_revision, rules: [], severities: [], ...fresh });
  const observations = [];
  for (const row of before) {
    await expect.poll(async () => {
      const status = await regenerationStatus(admin, row.id);
      if (status === null) {
        observations.push({ id: row.id, state: "stale_export_revision", httpStatus: 409 });
        return false;
      }
      observations.push({ id: row.id, state: status.regeneration_state, serving: status.serving, policyRevision: status.policy_revision, diskSha256: digest(fs.readFileSync(row.destination_path)) });
      assert.equal(status.serving, true);
      assert.equal(status.failed, false);
      return status.policy_revision === changed.policy_revision && status.regeneration_state === "succeeded";
    }, { timeout: 30000, intervals: [100, 200, 500] }).toBe(true);
  }
  const restored = await api(member, "GET", url);
  const evidence = [];
  for (const row of publications(run, detail.id)) {
    const prior = before.find(value => value.id === row.id);
    const snapshot = JSON.parse(row.snapshot_json);
    const previous = JSON.parse(prior.snapshot_json);
    const content = fs.readFileSync(row.destination_path);
    assert.deepEqual(content, Buffer.from(row.content_bytes));
    assert.notEqual(digest(content), digest(prior.content_bytes));
    assert.equal(row.policy_revision, changed.policy_revision);
    assert.ok(row.publication_revision > prior.publication_revision);
    assert.notEqual(snapshot.publication_id, previous.publication_id);
    assert.deepEqual(snapshot.sources, previous.sources);
    assert.deepEqual(snapshot.review, previous.review);
    assert.deepEqual(snapshot.policy_evidence, previous.policy_evidence);
    assert.equal(row.exported_format_revision, prior.exported_format_revision);
    assert.equal(row.regeneration_state, "succeeded");
    assert.equal(row.regeneration_due_at, null);
    assert.equal(snapshot.regeneration, undefined);
    assert.equal(snapshot.policy_owner_id, restored.policy_owner_id);
    assert.notEqual(snapshot.target_user_id, snapshot.policy_owner_id);
    const format = path.extname(row.destination_path).slice(1);
    const download = await member.send("POST", `${url}/exports/${format}/download`, {
      revision: restored.revision, policy_owner_id: restored.policy_owner_id, policy_revision: restored.policy_revision,
    }, undefined, true);
    assert.equal(download.status, 200);
    assert.deepEqual(download.body, content);
    evidence.push({ id: row.id, format, publicationId: snapshot.publication_id, publicationRevision: row.publication_revision, policyOwnerId: snapshot.policy_owner_id, targetUserId: snapshot.target_user_id, policyRevision: row.policy_revision, beforeSha256: digest(prior.content_bytes), sha256: digest(content), sourcesUnchanged: true, baseUnchanged: true });
  }
  const audit = query(run, "SELECT action, outcome, actor_kind, resource, details_json FROM audit_events WHERE action = 'export.regenerate' ORDER BY id");
  assert.ok(audit.length >= 2);
  for (const row of audit) {
    assert.equal(row.actor_kind, "system");
    assert.equal(row.outcome, "succeeded");
  }
  assert.deepEqual(query(run, "PRAGMA foreign_key_check"), []);
  run.write("export-regeneration-result.json", { passed: true, policyRevision: changed.policy_revision, observations, publications: evidence, audit, scope: "Authenticated policy edit and automatic source-app scheduler; real disk bytes, retained source/base snapshots, SQL and system audit. Faults, hard exits, deleted reviews and stale workers use deterministic pytest evidence." });
  return restored;
}