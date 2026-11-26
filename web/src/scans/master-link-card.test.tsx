import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { fetchCommunityMaster, fetchVersionAliases, findCommunityVersions, MasterApiError, previewMasterAlignment } from "../api/api-community-masters";
import { fetchDraftFilterPreview } from "../api/api-reviews";
import { checkMasterUpdates } from "../api/api-community-updates";
import { masterFilter, masterLink, masterView } from "./community-master-test-fixtures";
import { COMMUNITY_FILTERS_CHANGED_EVENT, notifyCommunityMastersChanged } from "./community-master-events";
import { MasterLinkCard } from "./master-link-card";
import { linkPickerValue } from "./master-link-state";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), fetchCommunityMaster: vi.fn(),
  findCommunityVersions: vi.fn(), fetchVersionAliases: vi.fn(),
  previewMasterAlignment: vi.fn(), approveMasterAlignment: vi.fn() }));
vi.mock("../api/api-community-updates", () => ({ checkMasterUpdates: vi.fn() }));
vi.mock("../api/api-reviews", () => ({ fetchDraftFilterPreview: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
vi.mock("./use-audio-tracks", () => ({ useAudioTracks: () => ({ inventory: null, selection: null, loading: false, error: null,
  ready: false, onChange: vi.fn(), onRetry: vi.fn() }) }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const target = { source: "jellyfin" as const, item_id: "movie-1", media_source_id: null };

it("checks on demand, downloads newer filters and tells open reviews, without an update review", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValueOnce(masterView([masterFilter()]))
    .mockResolvedValueOnce(masterView([masterFilter()], { latestRevision: 13, appliedRevision: 13 }));
  vi.mocked(checkMasterUpdates).mockResolvedValue({ version: 1, changed: true, latestRevision: 13 });
  const changed = vi.fn();
  window.addEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, changed);
  render(<MasterLinkCard summary={masterLink({ unconfirmed: true, matchPercent: 30 })} target={target} onChanged={vi.fn()} />);
  expect(screen.getByText("30% match")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Unconfirmed" })).toBeInTheDocument();
  await waitFor(() => expect(fetchCommunityMaster).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText(/Downloaded the latest community filters/)).toBeInTheDocument();
  expect(checkMasterUpdates).toHaveBeenCalledWith("link-1");
  expect(await screen.findByText("Revision 13")).toBeInTheDocument();
  expect(changed).toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
  expect(screen.queryByText(/Community filters updated/)).toBeNull();
  window.removeEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, changed);
});

it("reports when a check finds nothing new and gates a fuzzy link behind alignment", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter({ active: false })], { alignmentState: "required", tier: "4" }));
  vi.mocked(checkMasterUpdates).mockResolvedValue({ version: 1, changed: false, latestRevision: 12 });
  render(<MasterLinkCard summary={masterLink({ alignmentState: "required" })} target={target} onChanged={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "Align before applying" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check for filter updates" }));
  expect(await screen.findByText("These community filters are up to date.")).toBeInTheDocument();
});

it("plays aligned clips only from the local version the link is aligned to", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter()], { alignmentState: "required", tier: "4" }));
  vi.mocked(previewMasterAlignment).mockResolvedValue({ version: 1, token: "t",
    alignment: { offsetMs: 1000, scale: 1, anchors: 0 }, rows: [{ filterId: "f1", alignedStartMs: 601_000, alignedEndMs: 606_050 }] });
  vi.mocked(fetchDraftFilterPreview).mockResolvedValue(new Blob(["clip"]));
  URL.createObjectURL = vi.fn(() => "blob:clip");
  render(<MasterLinkCard summary={masterLink({ alignmentState: "required" })} target={target} onChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Preview alignment" }));
  fireEvent.click(await screen.findByRole("button", { name: /Play clip at/ }));
  await waitFor(() => expect(fetchDraftFilterPreview).toHaveBeenCalledExactlyOnceWith(target, {
    start_seconds: 601, end_seconds: 606.05, source: "visual", edit_action: "skip", stable_version_id: "mv_1" }, expect.any(AbortSignal)));
});

it("reloads its filters when the daily sync settles", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValueOnce(masterView([masterFilter()]))
    .mockResolvedValueOnce(masterView([masterFilter()], { appliedRevision: 14, latestRevision: 14 }));
  render(<MasterLinkCard summary={masterLink()} target={target} onChanged={vi.fn()} />);
  await waitFor(() => expect(fetchCommunityMaster).toHaveBeenCalledTimes(1));
  notifyCommunityMastersChanged();
  expect(await screen.findByText("Revision 14")).toBeInTheDocument();
});

it("shows a merged link's state and finds community versions again for its version", async () => {
  const merged = { linkState: "merged" as const, mergedInto: "v_2", aliasId: "extended", label: "Extended Edition" };
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([masterFilter()], merged));
  vi.mocked(findCommunityVersions).mockResolvedValue({ version: 1, findKey: "fk", localVersionId: "mv_1", keysUsed: [], error: null, results: [] });
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [{ kind: "other", label: "Other" }] });
  render(<MasterLinkCard summary={masterLink(merged)} target={target} onChanged={vi.fn()} />);
  expect(screen.getByText("Merged")).toBeInTheDocument();
  expect(screen.getByText(/merged this version into another one/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Check for filter updates" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Find again" }));
  expect(await screen.findByText("No community versions match this file.")).toBeInTheDocument();
  expect(findCommunityVersions).toHaveBeenCalledExactlyOnceWith(target, { alias_id: "extended" }, false);
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
  expect(screen.queryByRole("button", { name: "Check for filter updates" })).toBeNull();
  vi.mocked(findCommunityVersions).mockResolvedValue({ version: 1, findKey: "fk", localVersionId: "mv_1", keysUsed: [], error: null, results: [] });
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [{ kind: "other", label: "Other" }] });
  fireEvent.click(screen.getByRole("button", { name: "Find again" }));
  await screen.findByText("No community versions match this file.");
  expect(findCommunityVersions).toHaveBeenCalledExactlyOnceWith(target, { alias_id: "theatrical" }, false);
});

it("finds an unlisted version again by its text label", () => {
  expect(linkPickerValue({ aliasId: null, label: "IMAX" })).toEqual({ selection: "__other__", otherText: "IMAX" });
  expect(linkPickerValue({ aliasId: null, label: "Other" })).toEqual({ selection: "__other__", otherText: "" });
});
