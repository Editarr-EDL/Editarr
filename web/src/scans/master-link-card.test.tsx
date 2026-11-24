import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { fetchCommunityMaster } from "../api/api-community-masters";
import { checkMasterUpdates } from "../api/api-community-updates";
import { masterFilter, masterLink, masterView } from "./community-master-test-fixtures";
import { MasterLinkCard } from "./master-link-card";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), fetchCommunityMaster: vi.fn(),
  previewMasterAlignment: vi.fn(), approveMasterAlignment: vi.fn() }));
vi.mock("../api/api-community-updates", () => ({ checkMasterUpdates: vi.fn(), fetchMasterUpdate: vi.fn(() => new Promise(() => {})) }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
vi.mock("./use-audio-tracks", () => ({ useAudioTracks: () => ({ inventory: null, selection: null, loading: false, error: null,
  ready: false, onChange: vi.fn(), onRetry: vi.fn() }) }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const target = { source: "jellyfin" as const, item_id: "movie-1", media_source_id: null };

it("checks on demand and shows the update banner with counts and Review", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValueOnce(masterView([masterFilter()]))
    .mockResolvedValueOnce(masterView([masterFilter()], { latestRevision: 13,
      pendingUpdate: { new: 3, changed: 2, removed: 1, nowLowConfidence: 0 } }));
  vi.mocked(checkMasterUpdates).mockResolvedValue({ version: 1, changed: true, latestRevision: 13,
    pendingUpdate: { new: 3, changed: 2, removed: 1, nowLowConfidence: 0 } });
  render(<MasterLinkCard summary={masterLink({ unconfirmed: true, matchPercent: 30 })} target={target} onChanged={vi.fn()} />);
  expect(screen.getByText("30% match")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Unconfirmed" })).toBeInTheDocument();
  await waitFor(() => expect(fetchCommunityMaster).toHaveBeenCalledTimes(1));
  expect(screen.queryByText(/Community filters updated/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText("Community filters updated: 3 new, 2 changed, 1 removed.")).toBeInTheDocument();
  expect(checkMasterUpdates).toHaveBeenCalledWith("link-1");
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  expect(await screen.findByRole("heading", { name: /Review filter updates/ })).toBeInTheDocument();
});

it("reports when a check finds nothing new and gates a fuzzy first import behind alignment", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter()], { alignmentState: "required", tier: "4" }));
  vi.mocked(checkMasterUpdates).mockResolvedValue({ version: 1, changed: false, latestRevision: 12, pendingUpdate: null });
  render(<MasterLinkCard summary={masterLink({ alignmentState: "required" })} target={target} onChanged={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "Align before applying" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show filters" }));
  expect(await screen.findByText("Not applied")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText("These community filters are up to date.")).toBeInTheDocument();
});
