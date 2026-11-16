import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReviewFilter, SavedReview } from "../api/api-review-types";
import * as reviews from "../api/api-reviews";
import { readAccount } from "../api/api-users";
import { ApiError } from "../api/api-transport";
import { createReviewDraft, applyReviewRetimeResult, applyCreditsAcceptance, updateReviewFilter, reviewDraftFilters, applyReviewFilterResult } from "./review-draft";
import { useSavedReviewWorkspace } from "./review-workspace-state";
import { ReviewWorkspaceEditor } from "./review-workspace-editor";
import { ReviewConflict } from "./review-conflict";
import { AdjustCueTimesDialog } from "./review-controls";
import { ImportDialog } from "./review-import";
import { AuthenticationGate } from "../auth/auth-gate";
import { json, signedInStatus } from "../test/app-test-fixtures";

vi.mock("../api/api-service", () => ({
  fetchMcfRegistry: vi.fn(async () => ({ families: [] })),
  applyFilterDefaults: vi.fn(),
}));
vi.mock("../api/api-reviews", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/api-reviews")>(),
  fetchSavedReviews: vi.fn(), fetchSavedReview: vi.fn(), saveSavedReview: vi.fn(),
  fetchMcfPreflight: vi.fn(), fetchExportStatus: vi.fn(),
  duplicateSavedReview: vi.fn(), createSavedReview: vi.fn(), renameSavedReview: vi.fn(),
  retimeSavedReview: vi.fn(),
}));
vi.mock("../api/api-users", () => ({ readAccount: vi.fn() }));

const detail = {
  id: "review-1", name: "Policy review", revision: 2, content_revision: 1,
  edl_content_revision: 1, mcf_content_revision: 1,
  policy_owner_id: 11, policy_revision: 7,
  memberships: [], filters: [], scans: [], accepted_credits: null,
  synchronization: { start_seconds: 0, end_seconds: 100, end_mode: "file_end" },
  mcf_document: {
    title: "Policy review", year: null, work_type: "movie", season: null, episode: null,
    source_url: null, imdb_url: null, release: null, comment: null,
    material_start_seconds: 0, material_end_seconds: 100,
  },
  exports: { exports: {} },
} as unknown as SavedReview;
const props = { item: { id: "media-1" }, mediaSourceId: null, scans: [], onImported: vi.fn() };

beforeEach(() => {
  vi.mocked(reviews.fetchSavedReviews).mockResolvedValue([detail]);
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue(detail);
  vi.mocked(reviews.saveSavedReview).mockResolvedValue(detail);
  vi.mocked(reviews.duplicateSavedReview).mockResolvedValue({ ...detail, id: "copy" });
  vi.mocked(reviews.fetchMcfPreflight).mockResolvedValue({ eligible: true, issues: [] } as never);
  vi.mocked(reviews.fetchExportStatus).mockResolvedValue({ exports: {}, publications: [] } as never);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

it("offers members a file upload and no server-path control", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ userId: 4, role: "user", capabilities: ["reviews.import.upload"] }))));
  render(<AuthenticationGate><ImportDialog target={{ source: "jellyfin", item_id: "media-1" }} durationSeconds={100} creditsScans={[]} /></AuthenticationGate>);
  fireEvent.click(await screen.findByRole("button", { name: "Import" }));
  expect(screen.getByLabelText("EDL file")).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Scanner path" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Absolute scanner EDL path")).not.toBeInTheDocument();
});

it("names the current account in ownership and conflict context without an account-directory lookup", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ userId: 4, role: "user", capabilities: ["reviews.save.own"] }))));
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue({ ...detail, owner_id: 4, original_author_id: 4, policy_owner_id: 4 });
  let workspace!: ReturnType<typeof useSavedReviewWorkspace>;
  function Workspace() {
    workspace = useSavedReviewWorkspace(props);
    return <><ReviewWorkspaceEditor workspace={workspace} /><ReviewConflict workspace={workspace} /></>;
  }
  render(<AuthenticationGate><Workspace /></AuthenticationGate>);
  expect(await screen.findByText(/Current owner: You\. Historical author: You\./)).toBeVisible();
  act(() => workspace.setDraft(current => current ? { ...current, name: "Retained" } : null));
  vi.mocked(reviews.saveSavedReview).mockRejectedValueOnce(new ApiError("Reload policy", 409, "stale_review_policy"));
  await act(async () => workspace.save());
  expect(screen.getByRole("alert", { name: "Review conflict" })).toHaveTextContent("policy owner You");
  act(() => { workspace.setConflict(null); workspace.setSelected({ ...detail, owner_id: 11, original_author_id: 4 }); });
  expect(screen.getByText(/Current owner: Another account\. Historical author: You\./)).toBeVisible();
  expect(readAccount).not.toHaveBeenCalled();
});

it.each([
  ["Review member", "member", "Review member"],
  [null, "member", "member"],
])("uses account names already readable to an authorized directory caller (%s)", async (displayName, username, expected) => {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ userId: 4, role: "admin", capabilities: ["accounts.list", "reviews.view.other"] }))));
  vi.mocked(readAccount).mockResolvedValue({ id: 11, display_name: displayName, username } as never);
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue({ ...detail, owner_id: 11, original_author_id: 4 });
  function Workspace() { return <ReviewWorkspaceEditor workspace={useSavedReviewWorkspace(props)} />; }
  render(<AuthenticationGate><Workspace /></AuthenticationGate>);
  expect(await screen.findByText(new RegExp(`Current owner: ${expected}\\. Historical author: You\\.`))).toBeVisible();
  expect(readAccount).toHaveBeenCalledWith(11, expect.any(AbortSignal));
});

it("aborts a related account lookup when its review unmounts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ userId: 4, role: "admin", capabilities: ["accounts.list", "reviews.view.other"] }))));
  let resolveAccount!: (value: Awaited<ReturnType<typeof readAccount>>) => void;
  vi.mocked(readAccount).mockImplementation(() => new Promise(resolve => { resolveAccount = resolve; }));
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue({ ...detail, owner_id: 11, original_author_id: 4 });
  function Workspace() { return <ReviewWorkspaceEditor workspace={useSavedReviewWorkspace(props)} />; }
  const view = render(<AuthenticationGate><Workspace /></AuthenticationGate>);
  await waitFor(() => expect(readAccount).toHaveBeenCalled());
  const signal = vi.mocked(readAccount).mock.calls[0][1]!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => resolveAccount({ id: 11, display_name: "Late account" } as never));
});

it("sends current owner policy tokens on save and duplicate", async () => {
  const { result } = renderHook(() => useSavedReviewWorkspace(props));
  await waitFor(() => expect(result.current.selected?.id).toBe(detail.id));
  act(() => result.current.setName("Copy"));
  await act(async () => result.current.duplicateReview());
  act(() => result.current.setDraft((current) => current ? { ...current, name: "Saved" } : current));
  await act(async () => result.current.save());
  for (const request of [reviews.duplicateSavedReview, reviews.saveSavedReview]) {
    expect(request).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      policy_owner_id: 11, policy_revision: 7,
    }));
  }
});

it("preserves unsaved input after a conflict and blocks mutation until fresh-view resolution", async () => {
  const { result } = renderHook(() => useSavedReviewWorkspace(props));
  await waitFor(() => expect(result.current.selected?.id).toBe(detail.id));
  const fresh = { ...detail, policy_owner_id: 12, policy_revision: 9 };
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue(fresh);
  vi.mocked(reviews.saveSavedReview).mockRejectedValueOnce(new ApiError(
    "Reload policy", 409, "stale_review_policy",
  ));
  act(() => result.current.setDraft((current) => current ? { ...current, name: "Retry" } : current));
  await act(async () => result.current.save());
  expect(result.current.error).toContain("Reload policy");
  expect(result.current.selected?.policy_revision).toBe(9);
  expect(result.current.draft?.name).toBe("Retry");
  await act(async () => result.current.save());
  expect(reviews.saveSavedReview).toHaveBeenCalledOnce();
});

it("refreshes draft tokens from detail, retiming and credits responses", () => {
  const draft = createReviewDraft(detail);
  expect(draft).toMatchObject({ policy_owner_id: 11, policy_revision: 7 });
  const retimed = applyReviewRetimeResult(draft, {
    synchronization: detail.synchronization, mcf_document: detail.mcf_document,
    filters: [], accepted_credits: null, new_manual_filters: [],
    policy_owner_id: 12, policy_revision: 8,
  });
  expect(retimed).toMatchObject({ policy_owner_id: 12, policy_revision: 8 });
  const accepted = applyCreditsAcceptance(retimed, {
    accepted_credits: { scan_id: "credits", candidate_id: "candidate", accepted_boundary_seconds: 90 },
    membership: { scan_id: "credits", role: "video" },
    filter: { id: "credits-filter" } as never,
    policy_owner_id: 12, policy_revision: 9,
  });
  expect(accepted).toMatchObject({ policy_owner_id: 12, policy_revision: 9 });
});

it.each([
  "review_policy_enforced", "invalid_review_filter", "invalid_membership",
  "review_policy_timing_precision", "media_runtime_unavailable", "unknown_refusal",
])("keeps a refused draft editable after %s", async (code) => {
  let workspace!: ReturnType<typeof useSavedReviewWorkspace>;
  function Workspace() {
    workspace = useSavedReviewWorkspace(props);
    return <><ReviewWorkspaceEditor workspace={workspace} /><ReviewConflict workspace={workspace} /></>;
  }
  render(<Workspace />);
  await screen.findByRole("heading", { name: "Policy review" });
  vi.mocked(reviews.saveSavedReview).mockRejectedValueOnce(new ApiError("Correct the cue without losing your edits", 409, code));
  act(() => workspace.setDraft(current => current ? { ...current, name: "Retained edits" } : null));
  await act(async () => workspace.save());
  expect(workspace.error).toBe("Correct the cue without losing your edits");
  expect(workspace.draft?.name).toBe("Retained edits");
  expect(screen.queryByRole("alert", { name: "Review conflict" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled();
  act(() => workspace.setDraft(current => current ? { ...current, name: "Corrected draft" } : null));
  await act(async () => workspace.save());
  expect(reviews.saveSavedReview).toHaveBeenLastCalledWith(detail.id, expect.objectContaining({ name: "Corrected draft" }));
});

it("keeps current MCF bounds in retime requests and stages the chosen canonical file-end bound", async () => {
  const applied = vi.fn();
  vi.mocked(reviews.retimeSavedReview).mockResolvedValue({
    synchronization: { start_seconds: 10, end_seconds: 100, end_mode: "file_end" },
    mcf_document: { ...detail.mcf_document, material_start_seconds: 10 },
    filters: [], new_manual_filters: [], accepted_credits: null,
    policy_owner_id: 11, policy_revision: 7,
  });
  render(<AdjustCueTimesDialog reviewId={detail.id} draft={createReviewDraft(detail)} disabled={false} onApply={applied} />);
  fireEvent.click(screen.getByRole("button", { name: "Adjust Cue Times" }));
  fireEvent.change(screen.getByLabelText("Proposed START"), { target: { value: "00:00:10.000" } });
  fireEvent.change(screen.getByLabelText("Proposed canonical before-credits END"), { target: { value: "00:01:30.000" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply to draft" }));
  await waitFor(() => expect(applied).toHaveBeenCalledOnce());
  expect(reviews.retimeSavedReview).toHaveBeenCalledWith(detail.id, expect.objectContaining({
    current_synchronization: detail.synchronization,
    proposed_synchronization: { start_seconds: 10, end_seconds: 100, end_mode: "file_end" },
    mcf_document: detail.mcf_document,
  }));
  expect(applied).toHaveBeenCalledWith(expect.objectContaining({
    mcf_document: expect.objectContaining({ material_start_seconds: 10, material_end_seconds: 90 }),
  }));
});

it("explicitly saves a retained draft with fresh tokens without requiring timing permission", async () => {
  let workspace!: ReturnType<typeof useSavedReviewWorkspace>;
  function Workspace() {
    workspace = useSavedReviewWorkspace(props);
    return <><ReviewWorkspaceEditor workspace={workspace} /><ReviewConflict workspace={workspace} /></>;
  }
  render(<Workspace />);
  await screen.findByRole("heading", { name: "Policy review" });
  vi.mocked(reviews.saveSavedReview).mockRejectedValueOnce(new ApiError("Changed elsewhere", 409, "stale_review_policy"));
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue({ ...detail, revision: 3, policy_revision: 9 });
  act(() => workspace.setDraft(current => current ? { ...current, mcfDocument: { ...current.mcfDocument, comment: "Retained input" } } : null));
  await act(async () => workspace.save());
  expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
  expect(workspace.draft?.mcfDocument.comment).toBe("Retained input");
  vi.mocked(reviews.saveSavedReview).mockResolvedValueOnce({ ...detail, revision: 4, policy_revision: 9, mcf_document: { ...detail.mcf_document, comment: "Retained input" } });
  fireEvent.click(screen.getByRole("button", { name: "Save retained draft with latest policy" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save retained draft with latest policy" })).not.toBeInTheDocument());
  expect(vi.mocked(reviews.saveSavedReview).mock.calls[1][1]).toMatchObject({ revision: 3, policy_revision: 9 });
  expect(reviews.retimeSavedReview).not.toHaveBeenCalled();
  expect(workspace.draft?.mcfDocument.comment).toBe("Retained input");
});

it("returns a retained stale draft to editing if its explicit save is refused", async () => {
  const { result } = renderHook(() => useSavedReviewWorkspace(props));
  await waitFor(() => expect(result.current.selected?.id).toBe(detail.id));
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue({ ...detail, revision: 3, policy_revision: 9 });
  vi.mocked(reviews.saveSavedReview)
    .mockRejectedValueOnce(new ApiError("Policy changed", 409, "stale_review_policy"))
    .mockRejectedValueOnce(new ApiError("Correct the required cue", 409, "review_policy_enforced"));
  act(() => result.current.setDraft(current => current ? { ...current, name: "Retained draft" } : null));
  await act(async () => result.current.save());
  expect(result.current.conflict).toBe("ready");
  await act(async () => result.current.save(true));
  expect(result.current.conflict).toBeNull();
  expect(result.current.error).toBe("Correct the required cue");
  expect(result.current.draft).toMatchObject({ name: "Retained draft", revision: 3, policy_revision: 9 });
  await act(async () => result.current.save());
  expect(reviews.saveSavedReview).toHaveBeenCalledTimes(3);
});

it("sends accepted credits base severity so removal of enforcement restores it", async () => {
  const { result } = renderHook(() => useSavedReviewWorkspace(props));
  await waitFor(() => expect(result.current.selected?.id).toBe(detail.id));
  const base = {
    id: "credits-filter", source_scan_id: "credits", source_finding_id: 9,
    source: "visual", category: "dispensable_scenes", subcategory: "dispensable",
    severity: "medium", mcf_channel: "video", mcf_comment: null, label: "End Credits",
    details: null, group_key: null, start_seconds: 90, end_seconds: 100,
    confidence: 0.9, edit_action: "skip", selected: true,
  } satisfies ReviewFilter;
  act(() => result.current.stageCreditsAcceptance({
    accepted_credits: { scan_id: "credits", candidate_id: "candidate", accepted_boundary_seconds: 90 },
    membership: { scan_id: "credits", role: "video" },
    filter: { ...base, severity: "high" }, base_filter: base,
    policy_owner_id: 11, policy_revision: 7,
  }));
  expect(result.current.draft?.filters[0].severity).toBe("high");
  await act(async () => result.current.save());
  expect(reviews.saveSavedReview).toHaveBeenCalledOnce();
  const sent = vi.mocked(reviews.saveSavedReview).mock.calls[0][1];
  expect(sent).toMatchObject({ policy_owner_id: 11, policy_revision: 7 });
  expect(sent.filters.find(value => value.source_finding_id === 9)?.severity).toBe("medium");
});

it("retains staged base values through repeated credits, retime and Apply results while preserving explicit edits", () => {
  const base = { id: "staged", source_scan_id: "credits", source_finding_id: 9,
    selected: false, severity: "medium", start_seconds: 90, end_seconds: 100 } as ReviewFilter;
  const effective = { ...base, selected: true, severity: "high" as const };
  const accepted = {
    accepted_credits: { scan_id: "credits", candidate_id: "candidate", accepted_boundary_seconds: 90 },
    membership: { scan_id: "credits", role: "video" as const },
    filter: effective, base_filter: base, policy_owner_id: 11, policy_revision: 7,
  };
  let draft = applyCreditsAcceptance(applyCreditsAcceptance(createReviewDraft(detail), accepted), accepted);
  expect(draft.filters).toEqual([effective]);
  expect(reviewDraftFilters(draft)).toEqual([base]);
  draft = applyReviewRetimeResult(draft, {
    synchronization: detail.synchronization, mcf_document: detail.mcf_document,
    filters: [{ ...effective, start_seconds: 91 }], base_filters: [{ ...base, start_seconds: 91 }],
    accepted_credits: null, new_manual_filters: [], policy_owner_id: 11, policy_revision: 7,
  });
  expect(reviewDraftFilters(draft)[0]).toMatchObject({ selected: false, severity: "medium", start_seconds: 91 });
  draft = applyReviewFilterResult(draft, { filters: [effective], base_filters: [base] });
  draft = updateReviewFilter(draft, "staged", { severity: "high", selected: true });
  expect(reviewDraftFilters(draft)[0]).toMatchObject({ selected: true, severity: "high" });
});

it("preserves explicit grouped selection even when it returns to the enforced display value", async () => {
  const base = { id: "grouped", source_scan_id: "scan", source_finding_id: 5,
    source: "visual", category: null, subcategory: null, label: "Evidence",
    severity: "low", mcf_channel: "video", mcf_comment: null, details: null, group_key: null,
    start_seconds: 1, end_seconds: 2, confidence: null, edit_action: "skip", selected: false,
  } satisfies ReviewFilter;
  vi.mocked(reviews.fetchSavedReview).mockResolvedValue({ ...detail,
    filters: [{ ...base, selected: true }], base_filters: [base],
  });
  function Workspace() {
    return <ReviewWorkspaceEditor workspace={useSavedReviewWorkspace(props)} />;
  }
  render(<Workspace />);
  const group = await screen.findByRole("checkbox", { name: /Use all video filters.*family/ });
  fireEvent.click(group);
  fireEvent.click(group);
  const save = screen.getByRole("button", { name: "Save Changes" });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() => expect(reviews.saveSavedReview).toHaveBeenCalledOnce());
  expect(vi.mocked(reviews.saveSavedReview).mock.calls[0][1].filters[0].selected).toBe(true);
});