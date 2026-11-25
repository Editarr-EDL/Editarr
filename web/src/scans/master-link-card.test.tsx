import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { fetchCommunityMaster, fetchVersionAliases, findCommunityVersions, MasterApiError } from "../api/api-community-masters";
import { checkMasterUpdates } from "../api/api-community-updates";
import { masterFilter, masterLink, masterView } from "./community-master-test-fixtures";
import { notifyCommunityMastersChanged } from "./community-master-events";
import { MasterLinkCard } from "./master-link-card";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), fetchCommunityMaster: vi.fn(),
  findCommunityVersions: vi.fn(), fetchVersionAliases: vi.fn(),
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
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter({ active: false })], { alignmentState: "required", tier: "4" }));
  vi.mocked(checkMasterUpdates).mockResolvedValue({ version: 1, changed: false, latestRevision: 12, pendingUpdate: { new: 0, changed: 0, removed: 0, nowLowConfidence: 0 } });
  render(<MasterLinkCard summary={masterLink({ alignmentState: "required" })} target={target} onChanged={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "Align before applying" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show filters" }));
  expect(await screen.findByText("Not applied")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText("These community filters are up to date.")).toBeInTheDocument();
});

it("says updates are still waiting when a check finds no newer revision", async () => {
  const pendingUpdate = { new: 0, changed: 1, removed: 0, nowLowConfidence: 0 };
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter()], { pendingUpdate }));
  vi.mocked(checkMasterUpdates).mockResolvedValue({ version: 1, changed: false, latestRevision: 13, pendingUpdate });
  render(<MasterLinkCard summary={masterLink()} target={target} onChanged={vi.fn()} />);
  await screen.findByText(/Community filters updated/);
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText("No newer filters. You still have updates to review.")).toBeInTheDocument();
  expect(screen.queryByText("These community filters are up to date.")).toBeNull();
});

it("reloads its banner when the daily sync settles", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValueOnce(masterView([masterFilter()]))
    .mockResolvedValueOnce(masterView([masterFilter()], { pendingUpdate: { new: 2, changed: 0, removed: 0, nowLowConfidence: 0 } }));
  render(<MasterLinkCard summary={masterLink()} target={target} onChanged={vi.fn()} />);
  await waitFor(() => expect(fetchCommunityMaster).toHaveBeenCalledTimes(1));
  notifyCommunityMastersChanged();
  expect(await screen.findByText("Community filters updated: 2 new, 0 changed, 0 removed.")).toBeInTheDocument();
});

it("shows a merged link's state and finds community versions again", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter()], { linkState: "merged", mergedInto: "v_2" }));
  vi.mocked(findCommunityVersions).mockResolvedValue({ version: 1, findKey: "fk", localVersionId: "mv_1", keysUsed: [], error: null, results: [] });
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [{ kind: "other", label: "Other" }] });
  render(<MasterLinkCard summary={masterLink({ linkState: "merged", mergedInto: "v_2" })} target={target} onChanged={vi.fn()} />);
  expect(screen.getByText("Merged")).toBeInTheDocument();
  expect(screen.getByText(/merged this version into another one/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Check for filter updates" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Find again" }));
  expect(await screen.findByText("No community versions match this file.")).toBeInTheDocument();
  expect(findCommunityVersions).toHaveBeenCalledExactlyOnceWith(target, { alias_id: "theatrical" }, false);
});

it("shows an unavailable state after a check finds the version gone", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValueOnce(masterView([masterFilter()]))
    .mockResolvedValueOnce(masterView([masterFilter()], { linkState: "unavailable" }));
  vi.mocked(checkMasterUpdates).mockRejectedValue(new MasterApiError("version_unavailable", false, 409));
  render(<MasterLinkCard summary={masterLink()} target={target} onChanged={vi.fn()} />);
  await waitFor(() => expect(fetchCommunityMaster).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText("The community no longer offers this version. Find community filters again.")).toBeInTheDocument();
  expect(await screen.findByText("Unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Find again" })).toBeInTheDocument();
});
