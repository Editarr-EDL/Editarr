import { afterEach, expect, it, vi } from "vitest";
import { applyCommunityMaster, findCommunityVersions, listCommunityMasters, saveMasterPreferences, voteMasterFilter } from "./api-community-masters";
import { acceptAllMasterUpdate, decideMasterUpdate, markSyncNotificationsRead, runCommunitySync } from "./api-community-updates";
import { setCsrfToken } from "./api-transport";

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });
const target = { source: "jellyfin" as const, item_id: "abc", media_source_id: null };
const ok = () => new Response(JSON.stringify({ version: 1 }));

it("sends snake_case bodies with CSRF to the documented routes", async () => {
  const fetch = vi.fn(async () => ok());
  vi.stubGlobal("fetch", fetch);
  setCsrfToken("csrf-master");
  await findCommunityVersions(target, { alias_id: "theatrical" }, false);
  await applyCommunityMaster({ target, findKey: "fk", communityVersionId: "v_12ab" });
  await listCommunityMasters("abc", "src 1");
  await saveMasterPreferences({ confidenceThreshold: 0.25, showLowConfidence: true });
  await voteMasterFilter("l/1", "f1", { vote: "up", operationId: "vote-1" });
  await voteMasterFilter("l/1", "f1", { vote: "up", operationId: "vote-1" }, true);
  await runCommunitySync();
  await markSyncNotificationsRead(["n1"]);
  await decideMasterUpdate("l1", 13, [{ rowId: "r1", decision: "keep_mine" }]);
  await acceptAllMasterUpdate("l1", 13, "nowLowConfidence");
  const calls = fetch.mock.calls as unknown as [string, RequestInit][];
  const sent = calls.map(([url, init]) => [init.method ?? "GET", url, init.body ? JSON.parse(String(init.body)) : null]);
  expect(sent).toEqual([
    ["POST", "/api/community/find", { target, label: { alias_id: "theatrical" }, refresh: false }],
    ["POST", "/api/community/masters", { target, find_key: "fk", community_version_id: "v_12ab", confirmed: true }],
    ["GET", "/api/community/masters?item_id=abc&media_source_id=src+1", null],
    ["PATCH", "/api/community/preferences", { confidence_threshold: 0.25, show_low_confidence: true }],
    ["POST", "/api/community/masters/l%2F1/filters/f1/vote", { vote: "up", operation_id: "vote-1", confirmed: true }],
    ["POST", "/api/community/masters/l%2F1/filters/f1/vote/retry", { operation_id: "vote-1", confirmed: true }],
    ["POST", "/api/community/sync/run", {}],
    ["POST", "/api/community/sync/notifications/read", { notification_ids: ["n1"] }],
    ["POST", "/api/community/masters/l1/update/decisions", { to_revision: 13, decisions: [{ row_id: "r1", decision: "keep_mine" }] }],
    ["POST", "/api/community/masters/l1/update/accept-all", { to_revision: 13, group: "nowLowConfidence" }],
  ]);
  for (const [, init] of calls) {
    expect(init).toMatchObject({ cache: "no-store", credentials: "same-origin" });
    if (init.method) expect(new Headers(init.headers).get("x-csrf-token")).toBe("csrf-master");
  }
});

it("keeps error codes, vote reasons and merged targets, and never turns failures into empty results", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ code: "vote_ineligible", retryable: false, reason: "self_vote" }), { status: 409 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ code: "version_merged", retryable: false, mergedInto: "v_2" }), { status: 409 }))
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValueOnce(new Response(JSON.stringify({ version: 2 }))));
  await expect(voteMasterFilter("l1", "f1", { vote: "down", operationId: "vote-2" }))
    .rejects.toMatchObject({ code: "vote_ineligible", status: 409, reason: "self_vote" });
  await expect(applyCommunityMaster({ target, findKey: "fk", communityVersionId: "v_1" }))
    .rejects.toMatchObject({ code: "version_merged", mergedInto: "v_2" });
  await expect(listCommunityMasters("abc", null)).rejects.toMatchObject({ code: "network_unavailable", retryable: true });
  await expect(listCommunityMasters("abc", null)).rejects.toMatchObject({ code: "invalid_response" });
});
