import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CommunityApiError } from "../api/api-community";
import { communityDownloadStatus, downloadCommunityFilter } from "../api/api-community-downloads";
import { lookupCommunityFilters } from "../api/api-community-lookup";
import { readCommunityFeedback, sendCommunityVote } from "../api/api-community-votes";
import { communityResponse, mixedCandidate } from "../test/community-lookup-fixtures";
import { CommunityFeedback } from "./community-feedback";
import { CommunityFilterResults } from "./community-filter-results";
import { useCommunityDownloads } from "./use-community-downloads";
import { useCommunityLookup } from "./use-community-lookup";

const access = vi.hoisted(() => ({ allowed: true, current: true }));
vi.mock("../auth/auth-gate", async original => ({ ...await original<typeof import("../auth/auth-gate")>(),
  useCan: () => access.allowed, useOptionalAuthenticationGate: () => ({ isAuthenticationCurrent: () => access.current }) }));
vi.mock("../api/api-community-downloads", () => ({ communityDownloadStatus: vi.fn(), downloadCommunityFilter: vi.fn() }));
vi.mock("../api/api-community-lookup", async original => ({ ...await original<typeof import("../api/api-community-lookup")>(), lookupCommunityFilters: vi.fn() }));
vi.mock("../api/api-community-votes", () => ({ readCommunityFeedback: vi.fn(), sendCommunityVote: vi.fn() }));
const input = { target: { source: "jellyfin" as const, item_id: "movie", media_source_id: "version" } };
const choice = { revisionId: mixedCandidate.revisionId, artifactDigest: mixedCandidate.artifactDigest,
  targetKey: "a".repeat(64), channels: ["video" as const], reviewRequired: [] };
beforeEach(() => { access.allowed = true; access.current = true; });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("hides search and feedback, and emits no search/download/status requests without grants", async () => {
  access.allowed = false;
  const lookup = renderHook(() => useCommunityLookup(input));
  const downloads = renderHook(() => useCommunityDownloads(input, vi.fn()));
  render(<><CommunityFilterResults lookup={lookup.result.current} />
    <CommunityFeedback revisionId="private" disabled={false} refreshKey="target" /></>);
  await act(async () => {
    await lookup.result.current.start();
    await downloads.result.current.start([mixedCandidate], [choice], false);
    await downloads.result.current.reconcile([mixedCandidate], choice.targetKey);
  });
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
  expect(lookupCommunityFilters).not.toHaveBeenCalled();
  expect(communityDownloadStatus).not.toHaveBeenCalled();
  expect(downloadCommunityFilter).not.toHaveBeenCalled();
  expect(readCommunityFeedback).not.toHaveBeenCalled();
});
it("does not continue a download queue after authentication ends during the status read", async () => {
  let finish!: () => void;
  vi.mocked(communityDownloadStatus).mockImplementation(() => new Promise(resolve => { finish = () => resolve([{ revisionId: "mixed", status: "not_imported" }]); }));
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn()));
  let work: Promise<void> | undefined;
  act(() => { work = result.current.start([mixedCandidate], [choice], false); });
  access.current = false;
  await act(async () => { finish(); await work; });
  expect(downloadCommunityFilter).not.toHaveBeenCalled();
});
it.each([403, 404])("discards previously visible lookup summaries on %s", async status => {
  vi.mocked(lookupCommunityFilters).mockResolvedValueOnce(communityResponse()).mockRejectedValueOnce(new CommunityApiError("not_found", false, status));
  const { result } = renderHook(() => useCommunityLookup(input));
  await act(() => result.current.start());
  expect(result.current.candidates).toHaveLength(1);
  await act(() => result.current.refresh());
  expect(result.current.candidates).toEqual([]);
  expect(result.current.response).toBeNull();
});
it("does not read feedback after a vote finishes in an expired authentication lifetime", async () => {
  let finish!: () => void;
  vi.mocked(readCommunityFeedback).mockResolvedValue({ version: 1, revisionId: "mixed",
    scope: { serviceUrl: "https://community.test", contributorId: "acting" },
    current: { vote: "neutral", eligible: true, reason: null }, totals: null, pending: null, error: null });
  vi.mocked(sendCommunityVote).mockImplementation(() => new Promise(resolve => { finish = () => resolve(undefined); }));
  render(<CommunityFeedback revisionId="mixed" disabled={false} refreshKey="target" />);
  fireEvent.click(screen.getByRole("button", { name: /^Thumbs up/ }));
  await waitFor(() => expect(sendCommunityVote).toHaveBeenCalledOnce());
  access.current = false;
  await act(async () => finish());
  expect(readCommunityFeedback).toHaveBeenCalledOnce();
});
it("clears a denied download status without attempting an import", async () => {
  vi.mocked(communityDownloadStatus).mockRejectedValue(new CommunityApiError("capability_denied", false, 403));
  const { result } = renderHook(() => useCommunityDownloads(input, vi.fn()));
  await act(() => result.current.start([mixedCandidate], [choice], false));
  expect(result.current.entries).toEqual([]);
  expect(downloadCommunityFilter).not.toHaveBeenCalled();
});