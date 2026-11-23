import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { CommunityApiError } from "../api/api-community";
import { communityDownloadStatus, downloadCommunityFilter } from "../api/api-community-downloads";
import { mixedCandidate } from "../test/community-lookup-fixtures";
import { useCommunityDownloads } from "./use-community-downloads";

vi.mock("../api/api-community-downloads", () => ({ communityDownloadStatus: vi.fn(), downloadCommunityFilter: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
const input = { target: { source: "jellyfin" as const, item_id: "movie", media_source_id: "version" } };
const second = { ...mixedCandidate, contributionId: "other-root", revisionId: "second", artifactDigest: "b".repeat(64) };
const selection = (revisionId: string, artifactDigest: string) => ({ revisionId, artifactDigest, targetKey: "a".repeat(64), channels: ["video" as const], reviewRequired: [] });
const success = { version: 1 as const, revisionId: "mixed", status: "imported" as const, scan: { id: "saved" }, receiptState: "pending", reviewRequired: false };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(communityDownloadStatus).mockImplementation(async (_target, _key, choices) => choices.map((choice) => ({ revisionId: choice.revisionId, status: "not_imported" }))); });

it("keeps successes across independent failure and retries only the failed item", async () => {
  vi.mocked(downloadCommunityFilter).mockResolvedValueOnce(success as never).mockRejectedValueOnce(new CommunityApiError("service_unavailable", false, 409));
  const imported = vi.fn();
  const { result } = renderHook(() => useCommunityDownloads(input, imported));
  await act(() => result.current.start([mixedCandidate, second], [selection("mixed", mixedCandidate.artifactDigest), selection("second", second.artifactDigest)], false));
  expect(result.current.entries.map((entry) => entry.status)).toEqual(["imported", "failed"]);
  vi.mocked(downloadCommunityFilter).mockResolvedValueOnce({ ...success, revisionId: "second" } as never);
  await act(() => result.current.retry(result.current.entries[1], false));
  expect(result.current.entries.every((entry) => entry.status === "imported")).toBe(true);
  expect(downloadCommunityFilter).toHaveBeenCalledTimes(3);
  expect(imported).toHaveBeenCalledTimes(2);
});

it("does not send uncertain choices without deliberate confirmation", async () => {
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn()));
  await act(() => result.current.start([mixedCandidate], [{ ...selection("mixed", mixedCandidate.artifactDigest), channels: ["audio"], reviewRequired: ["audio"] }], false));
  expect(downloadCommunityFilter).not.toHaveBeenCalled();
});

it("finishes every selected download before opening the first staged scan", async () => {
  const firstStaged = { ...success, verificationPending: true };
  const secondStaged = { ...firstStaged, revisionId: "second", scan: { id: "other" } };
  const onStaged = vi.fn(() => expect(downloadCommunityFilter).toHaveBeenCalledTimes(2));
  vi.mocked(downloadCommunityFilter).mockResolvedValueOnce(firstStaged as never)
    .mockResolvedValueOnce(secondStaged as never);
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn(), onStaged));

  await act(() => result.current.start([mixedCandidate, second], [
    selection("mixed", mixedCandidate.artifactDigest), selection("second", second.artifactDigest),
  ], false));

  expect(result.current.entries.map((entry) => entry.status)).toEqual(["imported", "imported"]);
  expect(onStaged).toHaveBeenCalledExactlyOnceWith(firstStaged.scan);
});

it("cancelled in-flight outcomes remain unknown and reconcile to committed truth", async () => {
  vi.mocked(downloadCommunityFilter).mockImplementation((_input, _key, _choice, _confirmed, signal) => new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")))));
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn()));
  let work: Promise<void> | void;
  act(() => { work = result.current.start([mixedCandidate], [selection("mixed", mixedCandidate.artifactDigest)], false); });
  await waitFor(() => expect(result.current.entries[0].status).toBe("verifying"));
  act(() => result.current.cancel());
  await act(async () => { await work; });
  expect(result.current.entries[0].status).toBe("unknown");
  vi.mocked(communityDownloadStatus).mockResolvedValueOnce([{ revisionId: "mixed", status: "partial", approvedChannels: ["video"], scanId: "saved", receiptState: "pending", reviewRequired: false }]);
  await act(() => result.current.reconcile([mixedCandidate], "a".repeat(64)));
  expect(result.current.entries[0].status).toBe("imported");
  expect(downloadCommunityFilter).toHaveBeenCalledTimes(1);
});

it.each([
  { status: "imported" as const, approvedChannels: ["audio" as const], expected: ["audio", "video"], retry: [] },
  { status: "partial" as const, approvedChannels: [], expected: ["video"], retry: ["audio"] },
])("recovers a $status staged download after its reply is lost", async ({ status, approvedChannels, expected, retry }) => {
  vi.mocked(downloadCommunityFilter).mockRejectedValueOnce(new Error("response lost"));
  const onStaged = vi.fn();
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn(), onStaged));
  const mixed = { ...selection("mixed", mixedCandidate.artifactDigest), channels: ["audio" as const, "video" as const] };
  await act(() => result.current.start([mixedCandidate], [mixed], false));
  expect(result.current.entries[0].status).toBe("unknown");
  vi.mocked(communityDownloadStatus).mockResolvedValueOnce([{
    revisionId: "mixed", status, approvedChannels, pendingChannels: ["video"],
    verificationPending: true, scanId: "saved", receiptState: "pending", reviewRequired: false, reviewed: false,
  }]);

  await act(() => result.current.reconcile([mixedCandidate], mixed.targetKey));

  const committed = result.current.entries.find((entry) => entry.status === "imported");
  expect([...committed!.choice.channels].sort()).toEqual(expected);
  expect(committed!.result?.pendingChannels).toEqual(["video"]);
  expect(result.current.entries.find((entry) => entry.status === "cancelled")?.choice.channels ?? []).toEqual(retry);
  expect(downloadCommunityFilter).toHaveBeenCalledTimes(1);
  expect(onStaged).not.toHaveBeenCalled();
});

it("does not infer staged channels from a legacy partial status", async () => {
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn()));
  vi.mocked(communityDownloadStatus).mockResolvedValueOnce([{
    revisionId: "mixed", status: "partial", approvedChannels: [], verificationPending: true,
    scanId: "saved", receiptState: "pending", reviewRequired: false, reviewed: false,
  }]);
  await act(() => result.current.reconcile([mixedCandidate], "a".repeat(64)));
  expect(result.current.entries).toEqual([]);
});
