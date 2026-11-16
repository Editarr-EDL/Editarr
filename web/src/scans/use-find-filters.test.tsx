import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../api/api-scans";
import * as remoteApi from "../api/api-community-lookup";
import { CommunityApiError } from "../api/api-community";
import { completeScan } from "../test/app-test-fixtures";
import { useFindFilters, type LocalFilterTarget } from "./use-find-filters";

vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
vi.mock("../api/api-scans", () => ({ findFilterCandidates: vi.fn(), associateFilterCandidates: vi.fn() }));
vi.mock("../api/api-community-lookup", async (original) => ({
  ...await original<typeof import("../api/api-community-lookup")>(), lookupCommunityFilters: vi.fn(),
}));

const target: LocalFilterTarget = {
  source: "jellyfin", item_id: "movie-1", library_id: "movies", source_path: "/media/movie.mkv",
  item_name: "Example Movie", item_type: "Movie", duration_seconds: 120,
  scene_name: "Example.Movie", release_group: "GROUP", media_format: "MKV", media_provider_id: null,
};
const matches = [
  { scan: completeScan({ id: "existing", name: "Existing" }), match_reason: "quick_hash" as const, already_associated: true },
  { scan: completeScan({ id: "new", name: "New" }), match_reason: "scene_release" as const, already_associated: false },
  { scan: completeScan({ id: "other", name: "Other" }), match_reason: "format" as const, already_associated: false },
  { scan: completeScan({ id: "filename", name: "Filename" }), match_reason: "file_name" as const, already_associated: false },
];
const onMessage = vi.fn();
const onAssociated = vi.fn(async () => {});

beforeEach(() => {
  vi.mocked(api.findFilterCandidates).mockResolvedValue({ media_item_id: "resolved-media", matches });
  vi.mocked(api.associateFilterCandidates).mockResolvedValue();
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("does no work until Find, preselects only new local matches, and associates checked IDs once", async () => {
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated }));
  expect(api.findFilterCandidates).not.toHaveBeenCalled();
  await act(async () => { await result.current.find(); });
  expect(api.findFilterCandidates).toHaveBeenCalledWith(target, expect.any(AbortSignal));
  expect(result.current.open).toBe(true);
  expect(result.current.selectedIds).toEqual(["new", "other", "filename"]);
  expect(result.current.candidates.map((candidate) => candidate.matchDescription)).toEqual([
    "Already associated", "Found by scene name + release group", "Found by format + duration", "Found by file name + duration",
  ]);
  act(() => {
    result.current.select("existing", true);
    result.current.select("unknown", true);
    result.current.select("new", true);
    result.current.select("other", false);
    result.current.select("filename", false);
  });
  expect(result.current.selectedIds).toEqual(["new"]);
  await act(async () => { await result.current.associate(); });
  expect(api.associateFilterCandidates).toHaveBeenCalledExactlyOnceWith("resolved-media", ["new"]);
  expect(result.current.open).toBe(false);
  expect(onMessage).toHaveBeenLastCalledWith("Associated 1 scan.");
  expect(onAssociated).toHaveBeenCalledOnce();
});

it("keeps selection and results after association failure, then permits retry", async () => {
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated }));
  await act(async () => { await result.current.find(); });
  vi.mocked(api.associateFilterCandidates).mockRejectedValueOnce(new Error("Association unavailable"));
  await act(async () => { await result.current.associate(); });
  expect(onMessage).toHaveBeenLastCalledWith("Association unavailable");
  expect(result.current.open).toBe(true);
  expect(result.current.selectedIds).toEqual(["new", "other", "filename"]);
  expect(result.current.associating).toBe(false);
  expect(onAssociated).not.toHaveBeenCalled();
  await act(async () => { await result.current.associate(); });
  expect(onMessage).toHaveBeenLastCalledWith("Associated 3 scans.");
  expect(result.current.open).toBe(false);
});

it("locks cancellation and selection during association", async () => {
  let finish!: () => void;
  vi.mocked(api.associateFilterCandidates).mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }));
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated }));
  await act(async () => { await result.current.find(); });
  let pending!: Promise<void>;
  act(() => { pending = result.current.associate(); });
  expect(result.current.associating).toBe(true);
  act(() => { result.current.close(); result.current.select("new", false); });
  expect(result.current.open).toBe(true);
  expect(result.current.selectedIds).toContain("new");
  await act(async () => { finish(); await pending; });
  expect(result.current.open).toBe(false);
});

it("cancels without mutation and resets selection on the next search", async () => {
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated }));
  await act(async () => { await result.current.find(); });
  act(() => { result.current.select("new", false); result.current.close(); });
  expect(result.current.open).toBe(false);
  expect(api.associateFilterCandidates).not.toHaveBeenCalled();
  await act(async () => { await result.current.find(); });
  expect(result.current.selectedIds).toContain("new");
});

it("shows empty results and never associates an empty selection", async () => {
  vi.mocked(api.findFilterCandidates).mockResolvedValueOnce({ media_item_id: "resolved-media", matches: [] });
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated }));
  await act(async () => { await result.current.find(); await result.current.associate(); });
  expect(result.current.open).toBe(true);
  expect(result.current.candidates).toEqual([]);
  expect(api.associateFilterCandidates).not.toHaveBeenCalled();
});

it("rejects missing item IDs locally and recovers from search failures", async () => {
  const { result, rerender } = renderHook(({ search }) => useFindFilters({ target: search, onMessage, onAssociated }), {
    initialProps: { search: { ...target, item_id: "" } },
  });
  await act(async () => { await result.current.find(); });
  expect(api.findFilterCandidates).not.toHaveBeenCalled();
  expect(onMessage).toHaveBeenLastCalledWith("Choose a media item for filter matching.");
  rerender({ search: target });
  vi.mocked(api.findFilterCandidates).mockRejectedValueOnce(new Error("Search unavailable"));
  await act(async () => { await result.current.find(); });
  expect(result.current.finding).toBe(false);
  expect(result.current.open).toBe(false);
  expect(onMessage).toHaveBeenLastCalledWith("Search unavailable");
  await act(async () => { await result.current.find(); });
  expect(result.current.open).toBe(true);
});

it("announces loading and preserves quick-hash reason text", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.findFilterCandidates>>) => void;
  vi.mocked(api.findFilterCandidates).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated }));
  let pending!: Promise<void>;
  act(() => { pending = result.current.find(); });
  expect(result.current.finding).toBe(true);
  await act(async () => {
    finish({ media_item_id: "resolved-media", matches: [{ ...matches[0], already_associated: false }] });
    await pending;
  });
  expect(result.current.finding).toBe(false);
  expect(result.current.candidates[0].matchDescription).toBe("Found by quick hash + duration");
});

it("keeps local-first selection and associations usable during a failed remote query", async () => {
  vi.mocked(remoteApi.lookupCommunityFilters).mockRejectedValue(new CommunityApiError("service_unavailable", true, 503));
  const { result } = renderHook(() => useFindFilters({ target, onMessage, onAssociated,
    communityInput: { target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" } } }));
  await act(async () => { await result.current.find(); });
  expect(result.current.open).toBe(true);
  expect(result.current.community.error?.code).toBe("service_unavailable");
  expect(result.current.selectedIds).toEqual(["new", "other", "filename"]);
  expect(result.current.results.every((item) => item.source === "local")).toBe(true);
  act(() => result.current.select("community:revision", true));
  expect(result.current.selectedIds).not.toContain("community:revision");
  await act(async () => { await result.current.associate(); });
  expect(api.associateFilterCandidates).toHaveBeenCalledExactlyOnceWith("resolved-media", ["new", "other", "filename"]);
});

it("rejects out-of-order local discoveries after the selected target changes", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.findFilterCandidates>>) => void;
  vi.mocked(api.findFilterCandidates).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ media_item_id: "second-media", matches: [] });
  const { result, rerender } = renderHook(({ search }) => useFindFilters({ target: search, onMessage, onAssociated }), { initialProps: { search: target } });
  let first!: Promise<void>;
  act(() => { first = result.current.find(); });
  const signal = vi.mocked(api.findFilterCandidates).mock.calls[0][1];
  rerender({ search: { ...target, source_path: "/second.mkv" } });
  expect(signal?.aborted).toBe(true);
  await act(async () => { await result.current.find(); });
  await act(async () => { finish({ media_item_id: "old-media", matches }); await first; });
  expect(result.current.candidates).toEqual([]);
  expect(result.current.selectedIds).toEqual([]);
});