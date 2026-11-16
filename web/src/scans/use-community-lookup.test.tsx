import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommunityApiError } from "../api/api-community";
import { LOOKUP_POLICY, lookupCommunityFilters, type LookupInput, type LookupResponse, type RemoteFilterCandidate } from "../api/api-community-lookup";
import { useCommunityLookup } from "./use-community-lookup";

vi.mock("../api/api-community-lookup", async (original) => ({ ...await original<typeof import("../api/api-community-lookup")>(), lookupCommunityFilters: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const input: LookupInput = { target: { source: "jellyfin", item_id: "movie", media_source_id: "version-1" } };
const candidate: RemoteFilterCandidate = {
  contributionId: "root-1",
  revisionId: "revision-1", artifactDigest: "a".repeat(64), artifactSchema: "community-artifact-1",
  releaseId: "release", catalogCreatedAt: 1, attribution: { contributorId: "author", displayName: "Public author" },
  summary: { schema_version: "community-summary-1", submission_id: "revision-1", kind: "scan", total: 1,
    registry_version: "mcf-1.1.0", work_ids: [], release: null, duration_seconds: 120,
    release_version: null, scanner_behavior_version: null, channels: { video: 1 }, sources: { visual: 1 }, taxonomy: {}, coverage: [] },
  evidence: { media: { entity_type: "movie", identifiers: [], series_identifiers: [], episode_numbers: [], episode_span: [],
    release: { scene_name: null, group: null, edition: null, quality: null, size_bytes: null, duration: null, audio: null }, input_requirement: null },
    fingerprints: [], video_stream: null, audio_stream: null },
  compatibility: { policy_version: LOOKUP_POLICY, selected: false,
    video: { band: "release_match", reasons: [], conflicts: [], missing: [], strong_eligible: false },
    audio: { band: "possible_cut", reasons: [], conflicts: [], missing: [], strong_eligible: false } },
  statistics: { imports: 0, likes: 0, dislikes: 0 }, ranking: null,
};
const response = (candidates = [candidate]): LookupResponse => ({ version: 1, policyVersion: LOOKUP_POLICY,
  targetKey: "a".repeat(64), targetEvidence: candidate.evidence, warnings: [], availability: null, availabilityError: null,
  page: { version: 1, policyVersion: LOOKUP_POLICY, candidates, nextCursor: null }, pageError: null });

it("waits for an explicit action, preserves channel evidence and keeps candidates unselected", async () => {
  vi.mocked(lookupCommunityFilters).mockResolvedValue(response());
  const { result } = renderHook(() => useCommunityLookup(input));
  expect(lookupCommunityFilters).not.toHaveBeenCalled();
  await act(async () => { await result.current.start(); });
  expect(result.current.status).toBe("ready");
  expect(result.current.candidates[0].compatibility).toEqual(candidate.compatibility);
  expect(result.current.displayCandidates[0].id).toBe("community:revision-1");
});

it("retains successful results visibly stale on service/offline failure, not an empty match page", async () => {
  vi.mocked(lookupCommunityFilters).mockResolvedValueOnce(response()).mockRejectedValueOnce(new CommunityApiError("service_unavailable", true, 503));
  const { result } = renderHook(() => useCommunityLookup(input));
  await act(async () => { await result.current.start(); });
  await act(async () => { await result.current.refresh(); });
  expect(result.current.status).toBe("error");
  expect(result.current.stale).toBe(true);
  expect(result.current.candidates).toEqual([candidate]);
  expect(result.current.error?.code).toBe("service_unavailable");
  expect(vi.mocked(lookupCommunityFilters).mock.calls[1][1].refresh).toBe(true);
});

it("distinguishes missing identity and page failure from successful empty results", async () => {
  vi.mocked(lookupCommunityFilters).mockResolvedValueOnce({ ...response([]), page: null, pageError: { code: "missing_identity", retryable: false } })
    .mockResolvedValueOnce(response([]));
  const { result } = renderHook(() => useCommunityLookup(input));
  await act(async () => { await result.current.start(); });
  expect(result.current.status).toBe("error");
  expect(result.current.response?.page).toBeNull();
  await act(async () => { await result.current.refresh(); });
  expect(result.current.status).toBe("ready");
  expect(result.current.candidates).toEqual([]);
});

it("invalidates selected-track generations and ignores late replies even if transport ignores abort", async () => {
  let finish!: (value: LookupResponse) => void;
  vi.mocked(lookupCommunityFilters).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })).mockResolvedValue(response([]));
  const { result, rerender } = renderHook(({ target }) => useCommunityLookup(target), { initialProps: { target: input } });
  let old!: Promise<void>;
  act(() => { old = result.current.start(); });
  const signal = vi.mocked(lookupCommunityFilters).mock.calls[0][1].signal;
  rerender({ target: { ...input, target: { ...input.target, media_source_id: "version-2" } } });
  expect(signal.aborted).toBe(true);
  expect(result.current.response).toBeNull();
  await act(async () => { await result.current.start(); });
  await act(async () => { finish(response()); await old; });
  expect(result.current.candidates).toEqual([]);
});

it("cancels explicitly and on unmount without accepting late results", async () => {
  let finish!: (value: LookupResponse) => void;
  vi.mocked(lookupCommunityFilters).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const { result, unmount } = renderHook(() => useCommunityLookup(input));
  let pending!: Promise<void>;
  act(() => { pending = result.current.start(); });
  act(() => result.current.cancel());
  expect(result.current.status).toBe("cancelled");
  expect(vi.mocked(lookupCommunityFilters).mock.calls[0][1].signal.aborted).toBe(true);
  await act(async () => { finish(response()); await pending; });
  expect(result.current.candidates).toEqual([]);
  unmount();
});

it("binds pagination to targetKey, deduplicates pages and clears stale evidence after mutation", async () => {
  vi.mocked(lookupCommunityFilters).mockResolvedValueOnce({ ...response(), page: { ...response().page!, nextCursor: "cursor" } })
    .mockResolvedValueOnce(response([candidate, { ...candidate, revisionId: "revision-2" }]))
    .mockRejectedValueOnce(new CommunityApiError("file_changed", false, 409));
  const { result } = renderHook(() => useCommunityLookup(input));
  await act(async () => { await result.current.start(); });
  await act(async () => { await result.current.more(); });
  expect(vi.mocked(lookupCommunityFilters).mock.calls[1][1]).toMatchObject({ cursor: "cursor", targetKey: "a".repeat(64) });
  expect(result.current.candidates.map((item) => item.revisionId)).toEqual(["revision-1", "revision-2"]);
  await act(async () => { await result.current.refresh(); });
  expect(result.current.response).toBeNull();
  expect(result.current.candidates).toEqual([]);
  expect(result.current.error?.code).toBe("file_changed");
});

it("reports disabled enrollment without starting OAuth or polling", async () => {
  vi.mocked(lookupCommunityFilters).mockRejectedValue(new CommunityApiError("not_connected", false, 409));
  const { result } = renderHook(() => useCommunityLookup(input));
  await act(async () => { await result.current.start(); });
  expect(result.current.status).toBe("disabled");
  expect(lookupCommunityFilters).toHaveBeenCalledOnce();
});

it("invalidates evidence when the same file selects a different audio track", async () => {
  const track = { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2,
    disposition: 1, start_seconds: 0, duration_seconds: 120, time_base: "1/48000" };
  vi.mocked(lookupCommunityFilters).mockResolvedValue(response());
  const first = { ...input, audio_selection: { file_version: "a".repeat(64), track } };
  const { result, rerender } = renderHook(({ selected }) => useCommunityLookup(selected), { initialProps: { selected: first } });
  await act(async () => { await result.current.start(); });
  rerender({ selected: { ...first, audio_selection: { ...first.audio_selection, track: { ...track, stream_index: 2 } } } });
  expect(result.current.status).toBe("idle");
  expect(result.current.response).toBeNull();
  expect(result.current.candidates).toEqual([]);
});