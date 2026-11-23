import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as exportsApi from "../api/api-exports";
import type { ExportPublication } from "../api/api-mcf-types";
import type { ReviewDeletionPreview, SavedReview, SavedReviewSummary } from "../api/api-review-types";
import * as reviews from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { readAccount } from "../api/api-users";
import { AuthenticationGate } from "../auth/auth-gate";
import type { Capability } from "../auth/capabilities";
import { json, signedInStatus } from "../test/app-test-fixtures";
import { testPublication } from "./export-test-fixtures";
import type { VersionPublications } from "./export-publications";
import { ReviewDeletionDialog } from "./review-deletion-dialog";
import { PublishedFiles } from "./review-published-files";

vi.mock("../api/api-exports", () => ({ publishReviewExport: vi.fn(), downloadReviewExport: vi.fn(), deletePublication: vi.fn() }));
vi.mock("../api/api-reviews", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/api-reviews")>(), fetchReviewDeletion: vi.fn(), deleteReviewWithChoices: vi.fn(),
}));
vi.mock("../api/api-users", () => ({ readAccount: vi.fn() }));

const ADMIN: Capability[] = ["accounts.list", "exports.personal.delete.own", "exports.personal.delete.other", "exports.default.delete"];
const MEMBER: Capability[] = ["exports.personal.delete.own"];
const review = { id: "review-1", name: "Doomed", revision: 4, updated_at: "2026-10-27T10:00:00Z" } as SavedReview;
const memberEdl = testPublication({ id: 1, target_user_id: 4 });
const memberMcf = testPublication({ id: 2, format: "mcf", target_user_id: 4 });
const sharedMcf = testPublication({ id: 3, format: "mcf", scope: "default", target: "item_default", target_user_id: null });
const preview = (files: ReviewDeletionPreview["files"], confirmation = "a".repeat(64)): ReviewDeletionPreview => ({
  review_id: review.id, revision: 4, confirmation, files, legacy_formats: ["edl"], scan_ids: [],
});

function signIn(userId: number, capabilities: Capability[]) {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ userId, role: userId === 1 ? "owner" : "user", capabilities }))));
}

beforeEach(() => {
  vi.mocked(readAccount).mockImplementation(async (id) => ({ id, display_name: id === 4 ? "Mia" : `User ${id}` }) as never);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

it("submits one explicit choice per listed file and keeps files by default", async () => {
  signIn(4, MEMBER);
  vi.mocked(reviews.fetchReviewDeletion).mockResolvedValue(preview([
    { publication: memberEdl, choices: ["retain", "remove"] },
    { publication: memberMcf, choices: ["retain", "remove"] },
  ]));
  vi.mocked(reviews.deleteReviewWithChoices).mockResolvedValue({ review_id: review.id, retained: [1], removed: [2] });
  const onDeleted = vi.fn();
  render(<AuthenticationGate><ReviewDeletionDialog review={review} onDeleted={onDeleted} onClose={vi.fn()} /></AuthenticationGate>);
  const dialog = await screen.findByRole("dialog", { name: "Delete review" });
  const mcf = await within(dialog).findByRole("group", { name: "MCF · Personal · You" });
  expect(within(within(dialog).getByRole("group", { name: "EDL · Personal · You" })).getByRole("radio", { name: "Keep file" })).toBeChecked();
  fireEvent.click(within(mcf).getByRole("radio", { name: "Remove file" }));
  expect(dialog).toHaveTextContent("Legacy EDL sidecars beside the media are always kept.");
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete review" }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(reviews.deleteReviewWithChoices).toHaveBeenCalledWith("review-1", {
    revision: 4, confirmation: "a".repeat(64), files: [{ export_id: 1, choice: "retain" }, { export_id: 2, choice: "remove" }],
  });
});

it("offers only retention where the caller cannot remove a file", async () => {
  signIn(1, ADMIN);
  vi.mocked(reviews.fetchReviewDeletion).mockResolvedValue(preview([{ publication: sharedMcf, choices: ["retain"] }]));
  render(<AuthenticationGate><ReviewDeletionDialog review={review} onDeleted={vi.fn()} onClose={vi.fn()} /></AuthenticationGate>);
  const file = await screen.findByRole("group", { name: "MCF · Item default" });
  expect(within(file).queryByRole("radio", { name: "Remove file" })).not.toBeInTheDocument();
  expect(file).toHaveTextContent("You can't remove this file, so it is kept.");
});

it("reloads a stale preview, keeps still-valid choices, and deletes only after a fresh confirmation", async () => {
  signIn(4, MEMBER);
  vi.mocked(reviews.fetchReviewDeletion)
    .mockResolvedValueOnce(preview([{ publication: memberEdl, choices: ["retain", "remove"] }]))
    .mockResolvedValueOnce(preview([
      { publication: { ...memberEdl, publication_revision: 2 }, choices: ["retain", "remove"] },
      { publication: memberMcf, choices: ["retain", "remove"] },
    ], "b".repeat(64)));
  vi.mocked(reviews.deleteReviewWithChoices)
    .mockRejectedValueOnce(new ApiError("Changed", 409, "stale_review_deletion"))
    .mockResolvedValueOnce({ review_id: review.id, retained: [2], removed: [1] });
  const onDeleted = vi.fn();
  render(<AuthenticationGate><ReviewDeletionDialog review={review} onDeleted={onDeleted} onClose={vi.fn()} /></AuthenticationGate>);
  const dialog = await screen.findByRole("dialog", { name: "Delete review" });
  fireEvent.click(within(await within(dialog).findByRole("group", { name: "EDL · Personal · You" })).getByRole("radio", { name: "Remove file" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete review" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("changed since this list was shown");
  const mcf = await within(dialog).findByRole("group", { name: "MCF · Personal · You" });
  expect(within(within(dialog).getByRole("group", { name: "EDL · Personal · You" })).getByRole("radio", { name: "Remove file" })).toBeChecked();
  expect(within(mcf).getByRole("radio", { name: "Keep file" })).toBeChecked();
  expect(onDeleted).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete review" }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(vi.mocked(reviews.deleteReviewWithChoices).mock.calls[1][1]).toEqual({
    revision: 4, confirmation: "b".repeat(64), files: [{ export_id: 1, choice: "remove" }, { export_id: 2, choice: "retain" }],
  });
});

it("ignores an older preview that arrives after the user chose, keeping the choice and the newest confirmation", async () => {
  signIn(4, MEMBER);
  const pending: Array<(value: ReviewDeletionPreview) => void> = [];
  vi.mocked(reviews.fetchReviewDeletion).mockImplementation(() => new Promise((resolve) => { pending.push(resolve); }));
  vi.mocked(reviews.deleteReviewWithChoices).mockResolvedValue({ review_id: review.id, retained: [1], removed: [2] });
  const onDeleted = vi.fn();
  render(<StrictMode><AuthenticationGate><ReviewDeletionDialog review={review} onDeleted={onDeleted} onClose={vi.fn()} /></AuthenticationGate></StrictMode>);
  const dialog = await screen.findByRole("dialog", { name: "Delete review" });
  await waitFor(() => expect(pending).toHaveLength(2));
  const files: ReviewDeletionPreview["files"] = [
    { publication: memberEdl, choices: ["retain", "remove"] },
    { publication: memberMcf, choices: ["retain", "remove"] },
  ];
  pending[1](preview(files, "b".repeat(64)));
  const mcf = await within(dialog).findByRole("group", { name: "MCF · Personal · You" });
  fireEvent.click(within(mcf).getByRole("radio", { name: "Remove file" }));
  await act(async () => { pending[0](preview(files, "a".repeat(64))); });
  expect(within(within(dialog).getByRole("group", { name: "MCF · Personal · You" })).getByRole("radio", { name: "Remove file" })).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete review" }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(reviews.deleteReviewWithChoices).toHaveBeenCalledWith("review-1", {
    revision: 4, confirmation: "b".repeat(64), files: [{ export_id: 1, choice: "retain" }, { export_id: 2, choice: "remove" }],
  });
});

it("ignores an older preview that fails after the newer one arrived", async () => {
  signIn(4, MEMBER);
  const pending: Array<{ resolve: (value: ReviewDeletionPreview) => void; reject: (reason: Error) => void }> = [];
  vi.mocked(reviews.fetchReviewDeletion).mockImplementation(() => new Promise((resolve, reject) => { pending.push({ resolve, reject }); }));
  vi.mocked(reviews.deleteReviewWithChoices).mockResolvedValue({ review_id: review.id, retained: [], removed: [2] });
  const onDeleted = vi.fn();
  render(<StrictMode><AuthenticationGate><ReviewDeletionDialog review={review} onDeleted={onDeleted} onClose={vi.fn()} /></AuthenticationGate></StrictMode>);
  const dialog = await screen.findByRole("dialog", { name: "Delete review" });
  await waitFor(() => expect(pending).toHaveLength(2));
  pending[1].resolve(preview([{ publication: memberMcf, choices: ["retain", "remove"] }], "b".repeat(64)));
  const mcf = await within(dialog).findByRole("group", { name: "MCF · Personal · You" });
  fireEvent.click(within(mcf).getByRole("radio", { name: "Remove file" }));
  await act(async () => { pending[0].reject(new Error("Preview failed")); });
  expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  expect(within(within(dialog).getByRole("group", { name: "MCF · Personal · You" })).getByRole("radio", { name: "Remove file" })).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete review" }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(reviews.deleteReviewWithChoices).toHaveBeenCalledWith("review-1", {
    revision: 4, confirmation: "b".repeat(64), files: [{ export_id: 2, choice: "remove" }],
  });
});

function versionPublications(rows: ExportPublication[]): VersionPublications {
  return { publications: rows, notice: null, setNotice: vi.fn(), refresh: vi.fn(async () => rows) };
}

it("keeps outdated personal and item-default files in Published files without mutating them", async () => {
  signIn(4, MEMBER);
  const exports = versionPublications([memberEdl, sharedMcf].map((row) => ({ ...row, current: false, outdated: true })));
  render(<AuthenticationGate><PublishedFiles exports={exports} reviews={[]} selectedId="review-1" allowed={(capability) => MEMBER.includes(capability)} /></AuthenticationGate>);
  const files = await screen.findByRole("region", { name: "Published files" });
  for (const name of ["EDL · Personal · You", "MCF · Item default"]) {
    const row = within(files).getByRole("listitem", { name });
    expect(within(row).getByText("Outdated")).toBeVisible();
    expect(row).toHaveTextContent("Publish again to apply the current saved review and enforced filters.");
    expect(row).toHaveTextContent("Publication revision 1.");
  }
  expect(exportsApi.publishReviewExport).not.toHaveBeenCalled();
  expect(exportsApi.deletePublication).not.toHaveBeenCalled();
});

it("shows members delete only for their own files and admins for every file they hold rights to", async () => {
  const rows = [memberEdl, testPublication({ id: 5, target_user_id: 9, review_id: null }), sharedMcf];
  const reviewsList = [{ id: "review-1", name: "Doomed" }] as SavedReviewSummary[];
  signIn(4, MEMBER);
  const allowedMember = (capability: Capability) => MEMBER.includes(capability);
  const view = render(<AuthenticationGate><PublishedFiles exports={versionPublications(rows)} reviews={reviewsList} selectedId={null} allowed={allowedMember} /></AuthenticationGate>);
  expect(await screen.findByRole("button", { name: "Delete EDL · Personal · You" })).toBeVisible();
  expect(screen.getAllByRole("button", { name: /^Delete / })).toHaveLength(1);
  expect(screen.getByRole("listitem", { name: "EDL · Personal · Another account" })).toHaveTextContent("From a deleted or unavailable review.");
  view.unmount();
  signIn(1, ADMIN);
  const allowedAdmin = (capability: Capability) => ADMIN.includes(capability);
  render(<AuthenticationGate><PublishedFiles exports={versionPublications(rows)} reviews={reviewsList} selectedId={null} allowed={allowedAdmin} /></AuthenticationGate>);
  expect(await screen.findByRole("listitem", { name: "EDL · Personal · Mia" })).toHaveTextContent("From “Doomed”.");
  expect(screen.getAllByRole("button", { name: /^Delete / })).toHaveLength(3);
});

it("deletes with the listed revision and explains a stale file instead of deleting it", async () => {
  signIn(4, MEMBER);
  const exports = versionPublications([memberEdl]);
  vi.mocked(exportsApi.deletePublication).mockRejectedValueOnce(new ApiError("Changed", 409, "stale_export_revision")).mockResolvedValueOnce();
  render(<AuthenticationGate><PublishedFiles exports={exports} reviews={[]} selectedId="review-1" allowed={(capability) => MEMBER.includes(capability)} /></AuthenticationGate>);
  fireEvent.click(await screen.findByRole("button", { name: "Delete EDL · Personal · You" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete EDL" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That file changed after you opened this list.");
  expect(exports.refresh).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Delete EDL · Personal · You" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete EDL" }));
  await waitFor(() => expect(exports.setNotice).toHaveBeenLastCalledWith("Deleted EDL (Personal · You). Saved reviews are unchanged."));
  expect(exportsApi.deletePublication).toHaveBeenLastCalledWith(memberEdl);
});
