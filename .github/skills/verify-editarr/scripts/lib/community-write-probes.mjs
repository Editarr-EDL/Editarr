import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const OTHER = ["profile", "keys"].map(name => `community.identity.${name}.other`)
  .concat(["check", "retry", "retract"].map(name => `community.publications.${name}.other`));

/**
 * Prove community Find, publication check/retry/retract, profile rename, and key
 * rotation against the real app and the labelled contract-checked service double.
 * Every own and other capability is first withheld and refused with its state
 * unchanged, then used once with an independently read effect. Find reaches
 * `/v1/match`; a check reads local state only and never contacts the service; a
 * retry sends the saved submission to `/v1/submissions`. The profile name is
 * restored; the rotated key and two retracted publications end the journey.
 */
export async function verifyCommunityWrites({ run, step, ownerApi, memberApi, adminApi, member, admin, fresh, job, source, target }) {
  const result = { refused: [], succeeded: [] };
  const transport = () => JSON.parse(fs.readFileSync(path.join(run.evidence, "community-double.json"), "utf8"));
  const boundary = (method, url) => transport().requests.filter(item => item.method === method && item.path === url).length;
  // The double records each accepted `/v1/submissions` post as [contributor, operation].
  const posts = operation => transport().posts.filter(([, sent]) => sent === operation).length;
  const expectStatus = async (actor, api, expected, method, url, data) => {
    const answer = await api(method, url, data);
    assert.equal(answer.status, expected, `${actor} ${method} ${url}: ${JSON.stringify(answer.body)}`);
    if (expected === 404) assert.equal(answer.body.code, "not_found", "a withheld publication is concealed");
    if (expected === 403) assert.equal(answer.body.code, "capability_denied", `${actor} ${method} ${url} names the denied capability`);
    (expected === 200 ? result.succeeded : result.refused).push({ actor, method, path: url, status: answer.status, code: answer.body?.code ?? null });
    return answer.body;
  };
  const permissions = (account, values) => ownerApi("PATCH", `/api/users/${account.id}/permissions`, { overrides: values, ...fresh });
  const withheld = async (account, capabilities, refusals) => {
    assert.equal((await permissions(account, Object.fromEntries(capabilities.map(name => [name, "deny"])))).status, 200);
    try { await refusals(); } finally {
      assert.equal((await permissions(account, Object.fromEntries(capabilities.map(name => [name, "inherit"])))).status, 200);
    }
  };
  const status = async () => (await memberApi("GET", "/api/community/connection/status")).body;
  const read = async operation => (await ownerApi("GET", `/api/community/publications/${operation}`)).body;
  const find = { target };
  const rename = (name, operation) => ({ display_name: name, operation_id: operation });
  const first = job.operationId, publication = operation => `/api/community/publications/${operation}`;
  const counts = () => ({ matches: boundary("POST", "/v1/match"), renames: boundary("PATCH", "/v1/identity"),
    rotations: boundary("POST", "/v1/keys/rotate"), submissions: boundary("POST", "/v1/submissions"),
    requests: transport().requests.length, posts: transport().posts.length });

  const original = await status();
  const queued = await read(first);
  assert.equal(queued.state === "published" || queued.cancelRequested, false, "the first publication starts queued");
  const before = counts();
  await withheld(member, ["community.search"], () => expectStatus("mu32-member", memberApi, 403, "POST", "/api/community/find", find));
  await withheld(member, ["community.identity.view.own"], () => expectStatus("mu32-member", memberApi, 403, "GET", "/api/community/connection/status"));
  await withheld(member, ["community.identity.profile.own"], async () => {
    await expectStatus("mu32-member", memberApi, 403, "PATCH", "/api/community/profile", rename("Refused Own Name", "probe-refused-own-rename"));
    await expectStatus("mu32-member", memberApi, 403, "DELETE", "/api/community/profile");
  });
  await withheld(member, ["community.identity.keys.own"], () => expectStatus("mu32-member", memberApi, 403, "POST", "/api/community/keys/rotate", { confirmed: true }));
  await withheld(member, ["community.publications.check.own"], () => expectStatus("mu32-member", memberApi, 403, "POST", `${publication(first)}/check`));
  await withheld(member, ["community.publications.retry.own"], () => expectStatus("mu32-member", memberApi, 403, "POST", `${publication(first)}/retry`, { confirmed: true }));
  await withheld(member, ["community.publications.retract.own"], () => expectStatus("mu32-member", memberApi, 403, "DELETE", publication(first)));
  await withheld(admin, OTHER, async () => {
    await expectStatus("mu32-admin", adminApi, 403, "PATCH", `/api/users/${member.id}/community/profile`, rename("Refused Probe", "probe-refused-rename"));
    await expectStatus("mu32-admin", adminApi, 403, "POST", `/api/users/${member.id}/community/keys/rotation-status`);
    await expectStatus("mu32-admin", adminApi, 404, "POST", `${publication(first)}/check`);
    await expectStatus("mu32-admin", adminApi, 404, "POST", `${publication(first)}/retry`, { confirmed: true });
    await expectStatus("mu32-admin", adminApi, 404, "DELETE", publication(first));
  });
  assert.deepEqual(await read(first), queued, "refused publication requests changed nothing");
  assert.deepEqual((await status()).profile, original.profile, "refused profile and key requests changed nothing");
  assert.deepEqual(counts(), before, "no refused request reached the service double");
  step("Every withheld own and other community capability was refused and changed nothing");

  const found = await expectStatus("mu32-member", memberApi, 200, "POST", "/api/community/find", find);
  assert.deepEqual(found.results, [], "the double's /v1/match offers no community versions");
  assert.equal(found.error, null);
  assert.equal(boundary("POST", "/v1/match"), before.matches + 1, "Find reached the service double's /v1/match");

  const beforeChecks = counts();
  await expectStatus("mu32-admin", adminApi, 200, "POST", `${publication(first)}/check`);
  await expectStatus("mu32-member", memberApi, 200, "POST", `${publication(first)}/check`);
  assert.deepEqual(await read(first), queued, "own and other checks leave the queued publication unchanged");
  assert.deepEqual(counts(), beforeChecks, "a check reads local state and never contacts the service");

  await expectStatus("mu32-member", memberApi, 200, "POST", `${publication(first)}/retry`, { confirmed: true });
  const retried = await read(first);
  assert.equal(retried.state, "published", "a fresh read shows the member's retry published the first contribution");
  assert.equal(posts(first), 1, "the member's retry sent the first contribution once");
  assert.equal(boundary("POST", "/v1/submissions"), beforeChecks.submissions + 1, "the retry posted one submission");
  await expectStatus("mu32-admin", adminApi, 200, "DELETE", publication(first));
  const retracted = await read(first);
  assert.equal(retracted.cancelRequested, true, "a fresh read shows the admin's retraction of the first contribution");
  await expectStatus("mu32-admin", adminApi, 200, "POST", `${publication(first)}/retry`, { confirmed: true });
  await expectStatus("mu32-member", memberApi, 200, "DELETE", publication(first));
  assert.equal(posts(first), 1, "repeating a retry or retraction is idempotent");
  assert.equal(boundary("POST", "/v1/submissions"), beforeChecks.submissions + 1, "a repeated retry posts nothing");
  assert.deepEqual(await read(first), retracted, "repeating a retry or retraction is idempotent");

  const preview = await memberApi("POST", `${source}/preview`, { name: "Second member contribution", identity_confirmed: true,
    media: { provider: "tmdb", entity_type: "movie", reference: "123" }, version_choice: { choice: "alias", alias_id: "theatrical" } });
  assert.equal(preview.status, 200, JSON.stringify(preview.body));
  const approved = await memberApi("POST", `/api/community/sharing/previews/${preview.body.previewId}/approve`, { confirmed: true });
  assert.equal(approved.status, 200, JSON.stringify(approved.body));
  const second = (await adminApi("GET", source)).body.jobs.map(entry => entry.operationId).find(operation => operation !== first);
  assert.ok(second, "a second publication was queued");
  const secondQueued = await read(second);
  assert.equal(secondQueued.state === "published" || secondQueued.cancelRequested, false, "the second publication starts queued");
  await expectStatus("mu32-admin", adminApi, 200, "POST", `${publication(second)}/retry`, { confirmed: true });
  const otherRetried = await read(second);
  assert.equal(otherRetried.state, "published", "a fresh read shows the admin's retry published the second contribution");
  assert.equal(posts(second), 1, "the admin's retry sent the second contribution once");
  assert.equal(boundary("POST", "/v1/submissions"), beforeChecks.submissions + 2, "the second retry posted one submission");
  await expectStatus("mu32-member", memberApi, 200, "DELETE", publication(second));
  const ownRetracted = await read(second);
  assert.equal(ownRetracted.cancelRequested, true, "a fresh read shows the member's retraction of the second contribution");
  step("Own and other checks changed nothing; retry and retract each caused an independently read change", {
    first: { retriedBy: "member", retractedBy: "admin", state: retracted.state }, second: { retriedBy: "admin", retractedBy: "member", state: ownRetracted.state } });

  await expectStatus("mu32-member", memberApi, 200, "PATCH", "/api/community/profile", rename("Probe Member Name", "probe-own-rename"));
  assert.equal((await status()).profile.displayName, "Probe Member Name", "a fresh read shows the own rename");
  await expectStatus("mu32-admin", adminApi, 200, "PATCH", `/api/users/${member.id}/community/profile`, rename(original.profile.displayName, "probe-other-rename"));
  assert.equal((await status()).profile.displayName, original.profile.displayName, "the admin restored the member's name");
  await expectStatus("mu32-member", memberApi, 200, "DELETE", "/api/community/profile");
  assert.equal((await status()).profile.displayName, original.profile.displayName);
  step("Own and named profile renames persisted and the original name is restored");

  await expectStatus("mu32-member", memberApi, 200, "POST", "/api/community/keys/rotate", { confirmed: true });
  const rotated = await status();
  assert.notEqual(rotated.profile.keyId, original.profile.keyId, "a fresh read shows the new signing key");
  assert.equal(rotated.profile.contributorId, original.profile.contributorId);
  await expectStatus("mu32-admin", adminApi, 200, "POST", `/api/users/${member.id}/community/keys/rotation-status`);
  step("Own key rotation committed a new key and the admin read its rotation status");

  run.write("community-write-probes.json", { passed: true, ...result, retried, retracted, otherRetried, ownRetracted, keyRotated: true,
    scope: "Real authenticated source app against the signature-verifying community service double (labelled boundary fake). The double parses each request, including /v1/match and /v1/submissions, with the community service's own validators, answers Find with no versions, and keeps its rename uniqueness and replay rules. Publication checks are local-only. No live community service." });
}
