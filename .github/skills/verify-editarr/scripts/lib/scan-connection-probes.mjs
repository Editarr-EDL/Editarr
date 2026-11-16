import assert from "node:assert/strict";
import { query } from "./database.mjs";

/** Routes removed with Other Files; each must be unknown to the API, even for the owner. */
export const REMOVED_ROUTES = [
  ["POST", "/api/manual-scans"], ["GET", "/api/manual-scans"],
  ["GET", "/api/manual-files/identity"], ["POST", "/api/manual-files/identity"],
  ["GET", "/api/manual-files/reviews"], ["POST", "/api/manual-files/reviews"],
  ["GET", "/api/manual-files/exports"], ["DELETE", "/api/manual-files/exports/edl"],
  ["POST", "/api/edl-imports/path"], ["POST", "/api/mcf-imports/path"],
];

/**
 * Prove the owner-only connection test and disconnect routes against the real app and
 * the synthetic provider, each refused to the member first, then reconnect Jellyfin so
 * later steps see the same connection. The owner then finds every Other Files route
 * and both path import routes gone, and an upload aimed at another source instead of
 * a Jellyfin item is refused without writing anything.
 */
export async function verifyConnectionWritesAndRemovedRoutes({ run, send, ownerContext, memberContext, memberId, providerUrl, providerCalls, media, secret }) {
  const call = async (context, method, route, data) => {
    const response = await send(context, method, route, data);
    return { status: response.status(), body: await response.json().catch(() => null) };
  };
  const result = { refused: [], succeeded: [], removed: [] };
  const jellyfin = async () => {
    const { server_url, api_key_configured, last_error } = (await call(ownerContext, "GET", "/api/settings/jellyfin")).body;
    return { server_url, api_key_configured, last_error };
  };
  const connected = await jellyfin();
  assert.equal(connected.api_key_configured, true);

  for (const [method, route] of [["POST", "/api/settings/jellyfin/test"], ["DELETE", "/api/settings/jellyfin"]]) {
    const refused = await call(memberContext, method, route, method === "POST" ? {} : undefined);
    assert.equal(refused.status, 403, `${method} ${route}`);
    assert.equal(refused.body.code, "capability_denied");
    result.refused.push({ method, route, status: refused.status, code: refused.body.code });
  }
  assert.deepEqual(await jellyfin(), connected, "the member's refused disconnect left Jellyfin connected");

  const route = "/api/settings/jellyfin/test";
  const reached = "/Library/VirtualFolders";
  const before = providerCalls.filter(call => call.startsWith(reached)).length;
  const tested = await call(ownerContext, "POST", route, {});
  assert.equal(tested.status, 200, `${route}: ${JSON.stringify(tested.body)}`);
  assert.equal(tested.body.api_key_configured, true);
  assert.ok(providerCalls.filter(call => call.startsWith(reached)).length > before, `${route} reached the synthetic provider`);
  result.succeeded.push({ method: "POST", route, status: tested.status, providerReached: reached });

  const disconnected = await call(ownerContext, "DELETE", "/api/settings/jellyfin");
  assert.equal(disconnected.status, 200);
  assert.equal((await jellyfin()).api_key_configured, false, "a fresh read shows Jellyfin disconnected");
  result.succeeded.push({ method: "DELETE", route: "/api/settings/jellyfin", status: disconnected.status, freshRead: "api_key_configured false" });
  const reconnected = await call(ownerContext, "PUT", "/api/settings/jellyfin", { server_url: providerUrl, api_key: secret() });
  assert.equal(reconnected.status, 200);
  assert.deepEqual(await jellyfin(), connected, "Jellyfin is connected again for later steps");

  const removed = [...REMOVED_ROUTES, ["PUT", `/api/users/${memberId}/media-access/exceptions`]];
  for (const [method, route] of removed) {
    const response = await call(ownerContext, method, route, method === "GET" || method === "DELETE" ? undefined : { video_path: media.file });
    assert.equal(response.status, 404, `${method} ${route} is removed`);
    result.removed.push({ method, route, status: response.status });
  }
  const inventory = () => query(run, "SELECT (SELECT count(*) FROM media_items) AS items, (SELECT count(*) FROM scans) AS scans, (SELECT count(*) FROM saved_reviews) AS reviews")[0];
  const stored = inventory();
  const refused = await send(ownerContext, "POST", "/api/edl-imports/upload", undefined, {
    options: JSON.stringify({ target: { source: "manual", video_path: media.file }, name: "Other source import" }),
    file: { name: "other-source.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") },
  });
  const detail = (await refused.json()).detail;
  assert.deepEqual([refused.status(), detail.code], [400, "jellyfin_item_required"]);
  assert.deepEqual(inventory(), stored, "the refused import wrote nothing");
  result.refused.push({ method: "POST", route: "/api/edl-imports/upload", target: "manual video_path", status: refused.status(), code: detail.code, inventory: stored });

  run.write("scan-connection-routes.json", { passed: true, ...result,
    scope: "Real authenticated source app against the synthetic Jellyfin transport (a labelled fake). The owner tested, disconnected, and reconnected Jellyfin; the member's same requests were refused. Every Other Files route and both path import routes answered 404 to the owner, and an upload naming another source was refused with jellyfin_item_required and no new row." });
}
