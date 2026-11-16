import assert from "node:assert/strict";

/**
 * Prove path-mapping update, reorder, and delete, and an authorized admin's edit of
 * another account's non-enforced filter values, against the real app. Each write is
 * refused first to an account without the capability, each success is read back,
 * and the mappings, values, and grants end as they began.
 */
export async function verifySettingsWrites({ run, step, ownerApi, adminApi, operatorApi, admin, user, fresh }) {
  const result = { refused: [], succeeded: [] };
  const refuse = async (api, actor, method, url, data, withheld) => {
    const refused = await api(method, url, data);
    assert.equal(refused.status, 403, `${actor} ${method} ${url}`);
    assert.equal(refused.body.code, "capability_denied");
    result.refused.push({ actor, method, path: url, status: refused.status, code: refused.body.code, withheld });
  };
  const succeed = async (api, actor, method, url, data, freshRead) => {
    const done = await api(method, url, data);
    assert.equal(done.status, 200, `${actor} ${method} ${url}: ${JSON.stringify(done.body)}`);
    result.succeeded.push({ actor, method, path: url, status: done.status, freshRead: await freshRead() });
    return done.body;
  };

  const mappings = async () => (await ownerApi("GET", "/api/settings/path-mappings")).body
    .map(({ id, remote_path, local_path, sort_order }) => ({ id, remote_path, local_path, sort_order }));
  const original = await mappings();
  const created = await ownerApi("POST", "/api/settings/path-mappings", { remote_path: "/synthetic/probe-remote", local_path: "/synthetic/probe-local" });
  assert.equal(created.status, 200);
  const probe = created.body.id;
  const order = () => mappings().then(items => items.map(item => item.id));
  const others = original.map(item => item.id);
  const url = `/api/settings/path-mappings/${probe}`;
  await refuse(operatorApi, "settings-operator", "PATCH", url, { local_path: "/synthetic/denied" }, "path_mappings.update");
  await refuse(operatorApi, "settings-operator", "POST", "/api/settings/path-mappings/reorder", { mapping_ids: [probe, ...others] }, "path_mappings.reorder");
  await refuse(operatorApi, "settings-operator", "DELETE", url, undefined, "path_mappings.delete");
  assert.deepEqual(await order(), [...others, probe], "refused writes changed no mapping");

  await succeed(ownerApi, "owner", "PATCH", url, { local_path: "/synthetic/probe-updated" }, async () => {
    const saved = (await mappings()).find(item => item.id === probe);
    assert.equal(saved.local_path, "/synthetic/probe-updated");
    return saved;
  });
  await succeed(ownerApi, "owner", "POST", "/api/settings/path-mappings/reorder", { mapping_ids: [probe, ...others] }, async () => {
    assert.deepEqual(await order(), [probe, ...others]);
    return order();
  });
  await succeed(ownerApi, "owner", "POST", "/api/settings/path-mappings/reorder", { mapping_ids: [...others, probe] }, async () => {
    assert.deepEqual(await order(), [...others, probe]);
    return order();
  });
  await succeed(ownerApi, "owner", "DELETE", url, undefined, async () => {
    assert.deepEqual(await mappings(), original, "the probe mapping is gone and the others are unchanged");
    return order();
  });
  step("Owner updated, reordered, and deleted a path mapping; the operator without those grants was refused", { mappings: others.length });

  const target = `/api/settings/filters/users/${user.id}`;
  const values = async () => (await ownerApi("GET", target)).body;
  const before = await values();
  assert.equal(before.mode, "live", "the probe edits live-mode values");
  const swearing = view => view.severities.find(item => item.subcategory === "swearing");
  const severity = swearing(before).severity === "high" ? "low" : "high";
  const change = revision => ({ mode: "live", revision, severity_overrides: [{ subcategory: "swearing", severity }] });
  await refuse(adminApi, "settings-admin", "PUT", target, change(before.revision), "filters.preferences.edit.other");
  assert.deepEqual(await values(), before, "the refused edit saved nothing");
  const grant = value => ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "filters.preferences.edit.other": value }, ...fresh });
  assert.equal((await grant("allow")).status, 200);
  try {
    const seen = await adminApi("GET", target);
    assert.equal(seen.status, 200);
    const edited = await succeed(adminApi, "settings-admin", "PUT", target, change(seen.body.revision), async () => {
      const saved = swearing(await values());
      assert.deepEqual([saved.severity, saved.origin, saved.enforced], [severity, "override", false]);
      return saved;
    });
    await succeed(adminApi, "settings-admin", "PUT", target, { mode: "live", revision: edited.revision, severity_overrides: [] }, async () => {
      const saved = swearing(await values());
      assert.deepEqual(saved, swearing(before), "the user's own values are restored");
      return saved;
    });
  } finally {
    assert.equal((await grant("inherit")).status, 200);
  }
  await refuse(adminApi, "settings-admin", "PUT", target, change((await values()).revision), "filters.preferences.edit.other");
  step("An admin granted filters.preferences.edit.other edited and restored another user's values; without the grant the edit is refused");
  run.write("settings-write-probes.json", { passed: true, ...result,
    scope: "Real authenticated source app. The owner updated, reordered, and deleted a probe path mapping; an admin with an explicit filters.preferences.edit.other grant edited and restored another user's live severity override. Accounts without each capability were refused first, and the grant was returned to inherit." });
}
