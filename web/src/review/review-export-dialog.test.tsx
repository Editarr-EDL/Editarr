import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as exportsApi from "../api/api-exports";
import type { ExportPublication } from "../api/api-mcf-types";
import type { SavedReview, SavedReviewSummary } from "../api/api-review-types";
import * as reviews from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { readAccount } from "../api/api-users";
import { AuthenticationGate } from "../auth/auth-gate";
import type { Capability } from "../auth/capabilities";
import { json, signedInStatus } from "../test/app-test-fixtures";
import { testPublication } from "./export-test-fixtures";
import type { VersionPublications } from "./export-publications";
import { ExportDialog } from "./review-export-dialog";

vi.mock("../api/api-exports", () => ({ publishReviewExport: vi.fn(), downloadReviewExport: vi.fn(), deletePublication: vi.fn() }));
vi.mock("../api/api-reviews", async (importOriginal) => ({ ...await importOriginal<typeof import("../api/api-reviews")>(), fetchMcfPreflight: vi.fn() }));
vi.mock("../api/api-users", () => ({ readAccount: vi.fn() }));

const ADMIN: Capability[] = ["accounts.list", "exports.personal.download.own", "exports.personal.publish.own",
  "exports.personal.delete.own", "exports.personal.delete.other", "exports.default.publish", "exports.default.delete", "reviews.export.other"];
const MEMBER: Capability[] = ["exports.personal.download.own", "exports.personal.publish.own", "exports.personal.delete.own"];
const memberReview = {
  id: "review-b", name: "Member final", revision: 5, policy_owner_id: 9, policy_revision: 3, owner_id: 9,
  original_author_id: 9, media_item_id: "media-1", memberships: [], exports: { exports: {} },
} as unknown as SavedReview;
const reviewList = [{ id: "review-a", name: "Member draft" }, { id: "review-b", name: "Member final" }] as SavedReviewSummary[];

function versionPublications(rows: ExportPublication[]): VersionPublications {
  const state = { publications: rows, notice: null, setNotice: vi.fn(), refresh: vi.fn(async () => state.publications) };
  return state;
}

async function open({ userId, capabilities, review = memberReview, rows = [] as ExportPublication[], exportAvailable = true }: { userId: number; capabilities: Capability[]; review?: SavedReview; rows?: ExportPublication[]; exportAvailable?: boolean }) {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ userId, role: userId === 1 ? "owner" : "user", capabilities }))));
  const exports = versionPublications(rows);
  const handlers = { onMcfIssues: vi.fn(), onConflict: vi.fn(async () => {}), onPublished: vi.fn(), onClose: vi.fn() };
  const allowed = (capability: Capability) => capabilities.includes(capability);
  const view = render(<AuthenticationGate><ExportDialog review={review} reviews={reviewList} exports={exports} allowed={allowed} exportAvailable={exportAvailable} {...handlers} /></AuthenticationGate>);
  const dialog = await screen.findByRole("dialog", { name: "Export" });
  return { dialog, exports, handlers, view };
}

beforeEach(() => {
  vi.mocked(readAccount).mockImplementation(async (id) => ({ id, display_name: id === 9 ? "Mia" : `User ${id}` }) as never);
  vi.mocked(reviews.fetchMcfPreflight).mockResolvedValue({ eligible: true, issues: [] } as never);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

it("lets an admin target the review owner and names that destination and the policy source", async () => {
  const { dialog } = await open({ userId: 1, capabilities: ADMIN });
  await within(dialog).findByRole("radio", { name: "Review owner's personal files (Mia)" });
  expect(within(dialog).getAllByRole("radio").map((radio) => radio.parentElement?.textContent)).toEqual([
    "Your personal files", "Review owner's personal files (Mia)", "Item default (shared fallback)",
  ]);
  expect(dialog).toHaveTextContent("Filters follow the review owner's enforced policy (Mia, policy revision 3) for every destination.");
  expect(dialog).toHaveTextContent("Jellyfin plays these files only for you.");
  fireEvent.click(within(dialog).getByRole("radio", { name: "Review owner's personal files (Mia)" }));
  expect(dialog).toHaveTextContent("Jellyfin plays these files only for Mia.");
  fireEvent.click(within(dialog).getByRole("radio", { name: "Item default (shared fallback)" }));
  expect(dialog).toHaveTextContent("Jellyfin plays the item default for anyone without a personal file.");
});

it("shows members only their own destination and explains regeneration of the file there", async () => {
  const own = { ...memberReview, owner_id: 4, policy_owner_id: 4 };
  const regenerating = testPublication({ target_user_id: 4, review_id: "review-a", regenerating: true, regeneration_state: "queued", current: false, desired_policy_revision: 4 });
  const { dialog } = await open({ userId: 4, capabilities: MEMBER, review: own, rows: [regenerating] });
  expect(within(dialog).getAllByRole("radio")).toHaveLength(1);
  expect(within(dialog).queryByText(/Item default/)).not.toBeInTheDocument();
  expect(within(dialog).getByRole("listitem", { name: "EDL at Your personal files" })).toHaveTextContent(
    "RegeneratingFrom “Member draft”. Updating for a policy change. The previous file stays available until regeneration finishes.");
  expect(within(dialog).getByRole("listitem", { name: "MCF at Your personal files" })).toHaveTextContent("No file published here yet.");
});

it.each([
  ["edl", "self", "Your personal files"],
  ["mcf", "self", "Your personal files"],
  ["edl", "item_default", "Item default (shared fallback)"],
  ["mcf", "item_default", "Item default (shared fallback)"],
] as const)("publishes an outdated %s to %s only after choosing Publish", async (format, target, destination) => {
  const own = { ...memberReview, owner_id: 1, policy_owner_id: 1 };
  const existing = testPublication({
    format, target, scope: target === "self" ? "personal" : "default", target_user_id: target === "self" ? 1 : null,
    review_id: own.id, policy_revision: own.policy_revision, current: false, outdated: true,
  });
  vi.mocked(exportsApi.publishReviewExport).mockResolvedValue({
    kind: "published", publication: { ...existing, publication_revision: 2, current: true, outdated: false },
  });
  const { dialog, exports, handlers } = await open({ userId: 1, capabilities: ADMIN, review: own, rows: [existing] });
  fireEvent.click(within(dialog).getByRole("radio", { name: destination }));
  const label = format.toUpperCase();
  const row = within(dialog).getByRole("listitem", { name: `${label} at ${destination}` });
  expect(row).toHaveTextContent("OutdatedFrom this review. Publish again to apply the current saved review and enforced filters.");
  expect(exportsApi.publishReviewExport).not.toHaveBeenCalled();
  expect(exportsApi.deletePublication).not.toHaveBeenCalled();

  fireEvent.click(within(row).getByRole("button", { name: `Publish ${label}` }));
  expect(await within(dialog).findByRole("status")).toHaveTextContent(`Published ${label} to ${destination}.`);
  expect(handlers.onPublished).toHaveBeenCalledOnce();
  expect(exports.refresh).toHaveBeenCalledOnce();
  expect(exportsApi.publishReviewExport).toHaveBeenCalledExactlyOnceWith(own.id, format, {
    revision: own.revision, policy_owner_id: own.policy_owner_id, policy_revision: own.policy_revision, target, confirmation: undefined,
  });
  expect(exportsApi.deletePublication).not.toHaveBeenCalled();
});

it("confirms replacing another review's file with the server's confirmation for the previewed destination", async () => {
  const other = testPublication({ target_user_id: 9, target: "self", actor_id: 9, review_id: "review-a", publication_revision: 2 });
  vi.mocked(exportsApi.publishReviewExport)
    .mockResolvedValueOnce({ kind: "confirmation_required", confirmation: "c".repeat(64) })
    .mockResolvedValueOnce({ kind: "published", publication: { ...other, review_id: "review-b", publication_revision: 3 } });
  const { dialog, handlers } = await open({ userId: 1, capabilities: ADMIN, rows: [other] });
  fireEvent.click(await within(dialog).findByRole("radio", { name: "Review owner's personal files (Mia)" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Publish EDL" }));
  const confirm = await within(dialog).findByRole("alert", { name: "Confirm replacement" });
  expect(confirm).toHaveTextContent("The current file comes from “Member draft” (publication revision 2, published by Mia).");
  expect(confirm).toHaveTextContent("No saved review is deleted, and the other format is unchanged.");
  expect(within(dialog).getByRole("button", { name: "Publish MCF" })).toBeDisabled();
  fireEvent.click(within(confirm).getByRole("button", { name: "Replace EDL" }));
  await waitFor(() => expect(handlers.onPublished).toHaveBeenCalledOnce());
  expect(vi.mocked(exportsApi.publishReviewExport).mock.calls).toEqual([
    ["review-b", "edl", { revision: 5, policy_owner_id: 9, policy_revision: 3, target: "review_owner", confirmation: undefined }],
    ["review-b", "edl", { revision: 5, policy_owner_id: 9, policy_revision: 3, target: "review_owner", confirmation: "c".repeat(64) }],
  ]);
  expect(within(dialog).getByRole("status")).toHaveTextContent("Published EDL to Review owner's personal files (Mia).");
});

it("replaces nothing after a stale confirmation and asks again on retry", async () => {
  vi.mocked(exportsApi.publishReviewExport)
    .mockResolvedValueOnce({ kind: "confirmation_required", confirmation: "c".repeat(64) })
    .mockRejectedValueOnce(new ApiError("The export changed", 409, "stale_export_confirmation"))
    .mockResolvedValueOnce({ kind: "confirmation_required", confirmation: "d".repeat(64) });
  const { dialog, exports, handlers } = await open({ userId: 1, capabilities: ADMIN, rows: [testPublication({ target_user_id: 1, review_id: "review-a" })] });
  fireEvent.click(within(dialog).getByRole("button", { name: "Publish EDL" }));
  fireEvent.click(within(await within(dialog).findByRole("alert", { name: "Confirm replacement" })).getByRole("button", { name: "Replace EDL" }));
  const stale = await within(dialog).findByRole("alert", { name: "Replacement changed" });
  expect(stale).toHaveTextContent("nothing was replaced");
  expect(exports.refresh).toHaveBeenCalledTimes(2);
  fireEvent.click(within(stale).getByRole("button", { name: "Retry EDL" }));
  expect(await within(dialog).findByRole("alert", { name: "Confirm replacement" })).toBeVisible();
  expect(vi.mocked(exportsApi.publishReviewExport).mock.calls[2][2]).not.toHaveProperty("confirmation", expect.any(String));
  expect(handlers.onPublished).not.toHaveBeenCalled();
});

it("drops a pending confirmation when the destination changes", async () => {
  vi.mocked(exportsApi.publishReviewExport).mockResolvedValueOnce({ kind: "confirmation_required", confirmation: "c".repeat(64) });
  const { dialog } = await open({ userId: 1, capabilities: ADMIN });
  fireEvent.click(within(dialog).getByRole("button", { name: "Publish EDL" }));
  await within(dialog).findByRole("alert", { name: "Confirm replacement" });
  fireEvent.click(within(dialog).getByRole("radio", { name: "Item default (shared fallback)" }));
  expect(within(dialog).queryByRole("alert", { name: "Confirm replacement" })).not.toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Publish EDL" })).toBeEnabled();
});

it("downloads the caller's personal rendering without publishing", async () => {
  const createObjectURL = vi.fn(() => "blob:edl");
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  vi.mocked(exportsApi.downloadReviewExport).mockResolvedValue({ blob: new Blob(["1 2 1\n"]), filename: "Movie.user-local-1.edl" });
  const { dialog } = await open({ userId: 1, capabilities: ADMIN });
  fireEvent.click(within(dialog).getByRole("button", { name: "Download EDL" }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(exportsApi.downloadReviewExport).toHaveBeenCalledWith("review-b", "edl", { revision: 5, policy_owner_id: 9, policy_revision: 3 });
  expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe("Movie.user-local-1.edl");
  expect(exportsApi.publishReviewExport).not.toHaveBeenCalled();
});

it("hands MCF preflight issues to the workspace and closes instead of publishing", async () => {
  const issue = { code: "missing_severity", field: "severity", filter_id: "f", filter_position: 1, message: "Choose a severity." };
  vi.mocked(reviews.fetchMcfPreflight).mockResolvedValue({ eligible: false, issues: [issue] } as never);
  const { dialog, handlers } = await open({ userId: 1, capabilities: ADMIN });
  fireEvent.click(within(dialog).getByRole("button", { name: "Publish MCF" }));
  await waitFor(() => expect(handlers.onMcfIssues).toHaveBeenCalledWith([issue]));
  expect(handlers.onClose).toHaveBeenCalled();
  expect(exportsApi.publishReviewExport).not.toHaveBeenCalled();
});

it("does not publish after the dialog unmounts during MCF preflight", async () => {
  let finish!: (value: never) => void;
  vi.mocked(reviews.fetchMcfPreflight).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const { dialog, view } = await open({ userId: 1, capabilities: ADMIN });
  fireEvent.click(within(dialog).getByRole("button", { name: "Publish MCF" }));
  view.unmount();
  await act(async () => { finish({ eligible: true, issues: [] } as never); });
  expect(exportsApi.publishReviewExport).not.toHaveBeenCalled();
});

it("disables every format's download and publish together when the review has nothing to export", async () => {
  const { dialog } = await open({ userId: 1, capabilities: ADMIN, exportAvailable: false });
  const buttons = within(dialog).getAllByRole("button", { name: /^(Download|Publish) (EDL|MCF)$/ });
  expect(buttons).toHaveLength(4);
  for (const button of buttons) {
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Include at least one scan or community filter before exporting");
  }
});
