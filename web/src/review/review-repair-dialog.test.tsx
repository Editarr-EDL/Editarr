import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SavedReviewSummary } from "../api/api-review-types";
import type { Principal } from "../auth/session";
import { knownCapabilities } from "../auth/capabilities";
import * as api from "../api/api-reviews";
import { responseError } from "../api/api-transport";
import { ReviewRepairDialog } from "./review-repair-dialog";

const access = vi.hoisted(() => ({ principal: null as Principal | null }));
vi.mock("../auth/auth-gate", () => ({ usePrincipal: () => access.principal }));
vi.mock("../api/api-reviews", () => ({ recoverSavedReview: vi.fn(), redownloadReviewCommunityScan: vi.fn() }));

const review = {
  id: "broken", name: "Broken review", revision: 7,
  repair: {
    message: "This review uses a scan that no longer exists.", missing_scan_ids: ["missing"], detected_at: "2026-10-25T00:00:00Z",
    recover: { kept_cues: 3, removed_memberships: 2, removed_accepted_credits: 1 },
    community: { available: true, unavailable_reason: null },
  },
} as SavedReviewSummary;
const grantAll = () => { access.principal = { capabilities: knownCapabilities(["reviews.delete.own", "reviews.save.own", "community.download"]) } as Principal; };
afterEach(() => { cleanup(); vi.resetAllMocks(); access.principal = null; });

it("offers native radio options, recovery counts, and explanations for missing permissions", () => {
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  expect(screen.getAllByRole("radio")).toHaveLength(3);
  for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  expect(screen.getByText(/Keep 3 copied cues.*Remove 2 scan memberships and 1 accepted credits/)).toBeVisible();
  expect(screen.getByText("You do not have permission to delete this review.")).toBeVisible();
  expect(screen.getAllByText("You do not have permission to save or edit this review.")).toHaveLength(2);
});

it("hides re-download without community identity and explains a missing download grant", () => {
  access.principal = { capabilities: knownCapabilities(["reviews.save.own"]) } as Principal;
  const { rerender } = render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  expect(screen.getByRole("radio", { name: "Re-download from the community" })).toBeDisabled();
  expect(screen.getByText("You do not have permission to download community scans.")).toBeVisible();
  rerender(<ReviewRepairDialog review={{ ...review, repair: { ...review.repair!, community: null } }} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  expect(screen.queryByRole("radio", { name: "Re-download from the community" })).not.toBeInTheDocument();
});

it("continues a deletion in the file-choice dialog without deleting anything itself", () => {
  grantAll();
  const onDelete = vi.fn();
  const onResolved = vi.fn();
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={onResolved} onDelete={onDelete} />);
  expect(screen.getByText("Next, choose whether to keep or remove each file published from it.")).toBeVisible();
  fireEvent.click(screen.getByRole("radio", { name: "Delete the review" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(onDelete).toHaveBeenCalledOnce();
  expect(onResolved).not.toHaveBeenCalled();
  expect(api.recoverSavedReview).not.toHaveBeenCalled();
});

it.each([
  { label: "Recover the review", submit: "Recover review", request: api.recoverSavedReview },
  { label: "Re-download from the community", submit: "Re-download", request: api.redownloadReviewCommunityScan },
])("submits $label once and reports success", async ({ label, submit, request }) => {
  grantAll();
  const onResolved = vi.fn();
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={onResolved} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: label }));
  fireEvent.click(screen.getByRole("button", { name: submit }));
  await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledOnce();
  expect(request).toHaveBeenCalledWith("broken");
});

it("returns to choosing after re-download failure, disables it, and retains other choices", async () => {
  grantAll();
  vi.mocked(api.redownloadReviewCommunityScan).mockRejectedValue(new Error("Revision was revoked."));
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: "Re-download from the community" }));
  fireEvent.click(screen.getByRole("button", { name: "Re-download" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: "Re-download from the community" })).toBeDisabled());
  expect(screen.getByText("The missing scan could not be retrieved from the community. Choose one of the remaining options. Revision was revoked.")).toBeVisible();
  expect(screen.getByRole("radio", { name: "Recover the review" })).toBeEnabled();
  expect(screen.getByRole("radio", { name: "Delete the review" })).toBeEnabled();
});

it("disables a stored unavailable community scan when opened", () => {
  grantAll();
  render(<ReviewRepairDialog review={{ ...review, repair: { ...review.repair!, community: { available: true, unavailable_reason: "Digest mismatch." } } }} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  expect(screen.getByRole("radio", { name: "Re-download from the community" })).toBeDisabled();
  expect(screen.getByText(/remaining options\. Digest mismatch\./)).toBeVisible();
});

it.each([
  ["not_connected", "Your account is not connected to the community service."],
  ["incompatible_artifact", "The scan is not compatible with this media file."],
  ["future_reason", "The service is undergoing maintenance."],
])("shows a readable %s reason with unknown codes falling back to the server message", async (reason, message) => {
  grantAll();
  const failure = await responseError(new Response(JSON.stringify({ detail: {
    code: "community_scan_unavailable", message: reason === "future_reason" ? message : review.repair!.message,
    repair: review.repair, reason, transient: true,
  } }), { status: 409 }), "Unable to retrieve scan");
  vi.mocked(api.redownloadReviewCommunityScan).mockRejectedValue(failure);
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: "Re-download from the community" }));
  fireEvent.click(screen.getByRole("button", { name: "Re-download" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: "Re-download from the community" })).toBeDisabled());
  expect(screen.getByRole("status")).toHaveTextContent(`remaining options. ${message}`);
  expect(screen.getByRole("status")).not.toHaveTextContent(reason);
});

it("formats stored reason codes when reopened", () => {
  grantAll();
  render(<ReviewRepairDialog review={{ ...review, repair: { ...review.repair!, community: { available: false, unavailable_reason: "invalid_channels" } } }} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  expect(screen.getByRole("status")).toHaveTextContent("The scan does not contain the required audio or video channels.");
  expect(screen.getByRole("status")).not.toHaveTextContent("invalid_channels");
});

it("keeps a recover failure retryable with its reason", async () => {
  grantAll();
  vi.mocked(api.recoverSavedReview).mockRejectedValueOnce(new Error("The review changed. Try again."));
  const onResolved = vi.fn();
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={onResolved} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: "Recover the review" }));
  fireEvent.click(screen.getByRole("button", { name: "Recover review" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The review changed. Try again.");
  expect(screen.getByRole("radio", { name: "Recover the review" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover review" }));
  await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
});

it("requires explicit confirmation before retrying an uncertain community repair", async () => {
  grantAll();
  const failure = await responseError(new Response(JSON.stringify({ detail: {
    code: "community_scan_unavailable", message: review.repair!.message,
    repair: review.repair, reason: "explicit_review_required", transient: true,
  } }), { status: 409 }), "Unable to retrieve scan");
  vi.mocked(api.redownloadReviewCommunityScan).mockRejectedValueOnce(failure);
  const onResolved = vi.fn();
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={onResolved} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: "Re-download from the community" }));
  fireEvent.click(screen.getByRole("button", { name: "Re-download" }));
  const confirmation = await screen.findByRole("checkbox", { name: "I will review this scan's uncertain compatibility before using it." });
  expect(screen.getByRole("button", { name: "Re-download" })).toBeDisabled();
  expect(api.redownloadReviewCommunityScan).toHaveBeenCalledTimes(1);
  fireEvent.click(confirmation);
  fireEvent.click(screen.getByRole("button", { name: "Re-download" }));
  await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
  expect(api.redownloadReviewCommunityScan).toHaveBeenLastCalledWith("broken", true);
});

it("blocks repeat submissions and closing while a repair is pending", async () => {
  grantAll();
  let finish!: () => void;
  vi.mocked(api.recoverSavedReview).mockImplementation(() => new Promise((resolve) => { finish = () => resolve(review as never); }));
  const onClose = vi.fn();
  const onResolved = vi.fn();
  render(<ReviewRepairDialog review={review} onClose={onClose} onResolved={onResolved} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: "Recover the review" }));
  fireEvent.click(screen.getByRole("button", { name: "Recover review" }));
  fireEvent.click(screen.getByRole("button", { name: "Resolving..." }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(api.recoverSavedReview).toHaveBeenCalledOnce();
  expect(onClose).not.toHaveBeenCalled();
  finish();
  await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
});

it("uses modal initial focus, Tab wrapping, Escape and opener focus restoration", async () => {
  grantAll();
  const opener = document.createElement("button");
  document.body.append(opener); opener.focus();
  const onClose = vi.fn();
  const { unmount } = render(<ReviewRepairDialog review={review} onClose={onClose} onResolved={vi.fn()} onDelete={vi.fn()} />);
  const first = screen.getByRole("radio", { name: "Delete the review" });
  await waitFor(() => expect(first).toHaveFocus());
  fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
  expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
  unmount(); expect(opener).toHaveFocus(); opener.remove();
});