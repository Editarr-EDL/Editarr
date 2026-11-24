import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { acceptAllMasterUpdate, decideMasterUpdate, fetchMasterUpdate } from "../api/api-community-updates";
import { approveMasterAlignment, previewMasterAlignment } from "../api/api-community-masters";
import { masterLink, masterUpdate, updateRow } from "./community-master-test-fixtures";
import { MasterUpdateReview } from "./master-update-review";

vi.mock("../api/api-community-updates", () => ({
  fetchMasterUpdate: vi.fn(), decideMasterUpdate: vi.fn(), acceptAllMasterUpdate: vi.fn(),
}));
vi.mock("../api/api-community-masters", () => ({ previewMasterAlignment: vi.fn(), approveMasterAlignment: vi.fn() }));
vi.mock("./use-audio-tracks", () => ({ useAudioTracks: () => ({ inventory: null, selection: null, loading: false, error: null,
  ready: false, onChange: vi.fn(), onRetry: vi.fn() }) }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const target = { source: "jellyfin" as const, item_id: "movie-1", media_source_id: null };
const filters = [{ filterId: "c1", category: "violence", subcategory: null, startMs: 600_000, endMs: 605_000, action: "skip" }];
const renderReview = (onSettled = vi.fn()) => render(<MasterUpdateReview link={masterLink({ alignment: { offsetMs: 1300, scale: 1, anchors: 3 } })}
  target={target} filters={filters} onClose={vi.fn()} onSettled={onSettled} />);

it("groups rows, marks edited rows and accepts all without the edited ones", async () => {
  const touched = updateRow("c2", { touched: true, mine: { startMs: 600_100, endMs: 605_000, subcategory: null, severity: "medium", action: "skip" },
    allowed: ["accept", "keep_mine", "dismiss"] });
  vi.mocked(fetchMasterUpdate).mockResolvedValue(masterUpdate({
    new: [updateRow("n1", { oldMaster: null, changedFields: [] })], changed: [updateRow("c1"), touched],
    removed: [updateRow("r1", { newMaster: null })], nowLowConfidence: [updateRow("l1", { changedFields: ["lowConfidence"] })],
  }));
  vi.mocked(acceptAllMasterUpdate).mockResolvedValue(masterUpdate({ changed: [touched] }));
  renderReview();
  await screen.findByRole("region", { name: "New" });
  const groups = screen.getAllByRole("region");
  expect(groups.map((group) => group.getAttribute("aria-label")).filter(Boolean)).toEqual(["New", "Changed", "Removed", "Now Low Confidence"]);
  const changed = screen.getByRole("region", { name: "Changed" });
  expect(within(changed).getByText("You edited this")).toBeInTheDocument();
  expect(within(changed).getByText(/Accept all skips the filter you edited/)).toBeInTheDocument();
  expect(within(changed).getAllByRole("img")[1]).toHaveAccessibleName(/^Mine 10:00\.100–10:05\.000; Old .*; New /);
  expect(within(changed).getByRole("button", { name: /^Keep mine/ })).toBeEnabled();
  fireEvent.click(within(changed).getByRole("button", { name: "Accept all Changed (1)" }));
  await waitFor(() => expect(acceptAllMasterUpdate).toHaveBeenCalledWith("link-1", 13, "changed"));
  expect(await screen.findByRole("button", { name: "Accept all Changed (0)" })).toBeDisabled();
  expect(screen.queryByRole("region", { name: "New" })).toBeNull();
});

it("sends single decisions and reports when everything is reviewed", async () => {
  vi.mocked(fetchMasterUpdate).mockResolvedValueOnce(masterUpdate({ new: [updateRow("n1", { oldMaster: null })] }))
    .mockResolvedValueOnce(masterUpdate({}));
  vi.mocked(decideMasterUpdate).mockResolvedValue({});
  const settled = vi.fn();
  renderReview(settled);
  fireEvent.click(await screen.findByRole("button", { name: /^Dismiss/ }));
  await screen.findByText("All updates are reviewed.");
  expect(decideMasterUpdate).toHaveBeenCalledWith("link-1", 13, [{ rowId: "n1", decision: "dismiss" }]);
  expect(settled).toHaveBeenCalled();
});

it("requires alignment for fuzzy updates before Accept is enabled", async () => {
  vi.mocked(fetchMasterUpdate).mockResolvedValueOnce(masterUpdate({ changed: [updateRow("c1", { canAccept: false })] },
    { alignmentRequired: true, alignmentApproved: false }))
    .mockResolvedValueOnce(masterUpdate({ changed: [updateRow("c1", { canAccept: true })] }, { alignmentRequired: true, alignmentApproved: true }));
  vi.mocked(previewMasterAlignment).mockResolvedValue({ version: 1, token: "tok", alignment: { offsetMs: 1300, scale: 1, anchors: 0 },
    rows: [{ filterId: "c1", alignedStartMs: 601_300, alignedEndMs: 606_300, clipToken: "clip" }] });
  vi.mocked(approveMasterAlignment).mockResolvedValue({});
  renderReview();
  const accept = await screen.findByRole("button", { name: /^Accept violence/i });
  expect(accept).toBeDisabled();
  expect(screen.getByRole("button", { name: "Accept all Changed (0)" })).toBeDisabled();
  expect(screen.getByLabelText(/Offset \(seconds/)).toHaveValue(1.3);
  expect(screen.getByRole("button", { name: "Approve alignment" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview alignment" }));
  expect(await screen.findByText("Offset 1.300 s")).toBeInTheDocument();
  expect(previewMasterAlignment).toHaveBeenCalledWith("link-1", { anchors: null, offset_ms: 1300, audio_track_index: null });
  fireEvent.click(screen.getByRole("button", { name: "Approve alignment" }));
  await waitFor(() => expect(approveMasterAlignment).toHaveBeenCalledWith("link-1", "tok", 13));
  await waitFor(() => expect(screen.getByRole("button", { name: /^Accept violence/i })).toBeEnabled());
  expect(screen.queryByRole("button", { name: "Approve alignment" })).toBeNull();
});
