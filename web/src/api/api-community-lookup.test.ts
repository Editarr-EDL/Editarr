import { afterEach, expect, it, vi } from "vitest";
import { LOOKUP_POLICY, lookupCommunityFilters } from "./api-community-lookup";
import { setCsrfToken } from "./api-transport";
import { resetRuntimeUrlsForTests } from "../shared/runtime-urls";
import { communityResponse, mixedCandidate } from "../test/community-lookup-fixtures";
import contract from "../../../tests/fixtures/community/ranking-v1.json";

afterEach(() => {
  setCsrfToken(null);
  document.getElementById("editarr-bootstrap")?.remove();
  document.head.insertAdjacentHTML("beforeend", '<script id="editarr-bootstrap" type="application/json">{"version":1,"basePath":"/","apiBasePath":"/api"}</script>');
  resetRuntimeUrlsForTests();
  vi.unstubAllGlobals();
});
const target = { source: "jellyfin" as const, item_id: "movie", media_source_id: "version-1" };
const successful = { version: 1, policyVersion: LOOKUP_POLICY, targetKey: "a".repeat(64),
  targetEvidence: { media: {}, fingerprints: [], video_stream: null, audio_stream: null }, warnings: [],
  availability: null, availabilityError: null, page: { version: 1, policyVersion: LOOKUP_POLICY, candidates: [], nextCursor: null }, pageError: null };

it("uses mounted same-origin administrator/CSRF transport and propagates cancellation", async () => {
  document.getElementById("editarr-bootstrap")?.remove();
  document.head.insertAdjacentHTML("beforeend", '<script id="editarr-bootstrap" type="application/json">{"version":1,"basePath":"/editarr","apiBasePath":"/editarr/api"}</script>');
  resetRuntimeUrlsForTests();
  setCsrfToken("csrf-test");
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(successful), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  await lookupCommunityFilters({ target }, { signal: controller.signal, refresh: true });
  expect(fetch.mock.calls[0][0]).toBe("/editarr/api/community/lookup");
  const init = fetch.mock.calls[0][1];
  expect(init.signal).toBe(controller.signal);
  expect(init.credentials).toBe("same-origin");
  expect(new Headers(init.headers).get("x-csrf-token")).toBe("csrf-test");
  expect(JSON.parse(init.body)).toEqual({ target, refresh: true, cursor: null, target_key: null });
  controller.abort();
  fetch.mockRejectedValue(new DOMException("Cancelled", "AbortError"));
  await expect(lookupCommunityFilters({ target }, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
});

it("preserves errors and rejects unsupported policy or preselected remote responses", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: "service_unavailable", retryable: true }), { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...successful, policyVersion: "future-policy" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...successful, page: { ...successful.page, candidates: [{ compatibility: { selected: true } }] } })));
  vi.stubGlobal("fetch", fetch);
  const options = { signal: new AbortController().signal };
  await expect(lookupCommunityFilters({ target }, options)).rejects.toMatchObject({ code: "service_unavailable", retryable: true });
  await expect(lookupCommunityFilters({ target }, options)).rejects.toMatchObject({ code: "unsupported_policy" });
  await expect(lookupCommunityFilters({ target }, options)).rejects.toMatchObject({ code: "invalid_response" });
});

it.each([{ ...successful, page: null }, { ...successful, pageError: {} }, { ...successful, availability: {} }])(
  "rejects malformed success envelopes rather than showing zero matches", async (value) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value))));
    await expect(lookupCommunityFilters({ target }, { signal: new AbortController().signal })).rejects.toMatchObject({ code: "invalid_response" });
  });

it("distinguishes network failure from successful empty pages", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Private network details")));
  await expect(lookupCommunityFilters({ target }, { signal: new AbortController().signal })).rejects.toMatchObject({ code: "network_unavailable" });
});

it("accepts the shared ranking DTO and legacy null rankings", async () => {
  const score = contract.cases[0].score;
  const candidate = { ...mixedCandidate, ranking: { policyVersion: "community-ranking-wilson-1" as const,
    qualityScore: score, voteCount: 0, uploaderBoost: 0 as const, score } };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(communityResponse([candidate])))));
  expect((await lookupCommunityFilters({ target }, { signal: new AbortController().signal })).page?.candidates[0].ranking).toEqual(candidate.ranking);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(communityResponse()))));
  expect((await lookupCommunityFilters({ target }, { signal: new AbortController().signal })).page?.candidates[0].ranking).toBeNull();
});

it.each([{ policyVersion: "future-ranking" }, { voteCount: 1 }, { score: 0.99 }, { uploaderBoost: 0.1 },
  { qualityScore: "0.1" }, { private_path: "PRIVATE" }])("rejects inconsistent or unknown ranking objects", async (change) => {
  const score = contract.cases[0].score;
  const ranking = { policyVersion: contract.policyVersion, qualityScore: score, voteCount: 0, uploaderBoost: 0, score, ...change };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(communityResponse([{ ...mixedCandidate, ranking } as never])))));
  await expect(lookupCommunityFilters({ target }, { signal: new AbortController().signal })).rejects.toMatchObject({ code: "invalid_response" });
});