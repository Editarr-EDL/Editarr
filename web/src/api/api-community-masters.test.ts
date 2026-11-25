import { afterEach, expect, it, vi } from "vitest";
import {
  applyCommunityMaster, decideMasterFilter, fetchMasterActiveFilters, findCommunityVersions, listCommunityMasters,
  reportCommunityMaster, saveMasterPreferences, voteMasterFilter,
} from "./api-community-masters";
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
  await fetchMasterActiveFilters("l 1");
  await decideMasterFilter("l1", "f1", { decision: "adjust", expected_state: "pending", severity: "low" });
  await reportCommunityMaster("l1", { target: { type: "version", id: "v_1" }, reason: "spam", note: "  " });
  await reportCommunityMaster("l1", { target: { type: "filter", id: "f1" }, reason: "inaccurate", note: " Off by a minute " });
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
    ["GET", "/api/community/masters/l%201/active-filters", null],
    ["PATCH", "/api/community/masters/l1/filters/f1", { decision: "adjust", expected_state: "pending", severity: "low" }],
    ["POST", "/api/community/masters/l1/reports", { target: { type: "version", id: "v_1" }, reason: "spam", confirmed: true }],
    ["POST", "/api/community/masters/l1/reports",
      { target: { type: "filter", id: "f1" }, reason: "inaccurate", note: "Off by a minute", confirmed: true }],
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

it("accepts 204 No Content from the routes that return no body", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
  await expect(markSyncNotificationsRead("all")).resolves.toBeUndefined();
  await expect(decideMasterUpdate("l1", 13, [{ rowId: "r1", decision: "accept" }])).resolves.toBeUndefined();
});

it("maps validation lists, rate limits and bare 404s to codes the UI can explain", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [
      { type: "value_error", loc: ["body"], msg: "Value error, Send either anchors or offset_ms" }] }), { status: 422 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ code: "rate_limited", retryable: true, retryAfter: 7 }),
      { status: 429, headers: { "Retry-After": "7" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })));
  await expect(fetchMasterActiveFilters("l1")).rejects.toMatchObject({
    code: "validation_failed", status: 422, validation: ["Send either anchors or offset_ms"] });
  await expect(findCommunityVersions(target, null, false)).rejects.toMatchObject({ code: "rate_limited", retryable: true, retryAfter: 7 });
  await expect(fetchMasterActiveFilters("l1")).rejects.toMatchObject({ code: "not_found", status: 404, retryAfter: null });
});
