import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SavedReviewSummary } from "../api/api-review-types";
import type { Principal } from "../auth/session";
import { knownCapabilities } from "../auth/capabilities";
import * as api from "../api/api-reviews";
import { ReviewRepairDialog } from "./review-repair-dialog";

const access = vi.hoisted(() => ({ principal: null as Principal | null }));
vi.mock("../auth/auth-gate", () => ({ usePrincipal: () => access.principal }));
vi.mock("../api/api-reviews", () => ({ recoverSavedReview: vi.fn() }));

const review = {
  id: "broken", name: "Broken review", revision: 7,
  repair: {
    message: "This review uses a scan that no longer exists.", missing_scan_ids: ["missing"], detected_at: "2026-10-25T00:00:00Z",
    recover: { kept_cues: 3, removed_memberships: 2, removed_accepted_credits: 1 },
  },
} as SavedReviewSummary;
const grantAll = () => { access.principal = { capabilities: knownCapabilities(["reviews.delete.own", "reviews.save.own"]) } as Principal; };
afterEach(() => { cleanup(); vi.resetAllMocks(); access.principal = null; });

it("offers native radio options, recovery counts, and explanations for missing permissions", () => {
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={vi.fn()} onDelete={vi.fn()} />);
  expect(screen.getAllByRole("radio")).toHaveLength(2);
  for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  expect(screen.getByText(/Keep 3 copied cues.*Remove 2 scan memberships and 1 accepted credits/)).toBeVisible();
  expect(screen.getByText("You do not have permission to delete this review.")).toBeVisible();
  expect(screen.getByText("You do not have permission to save or edit this review.")).toBeVisible();
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

it("submits recovery once and reports success", async () => {
  grantAll();
  const onResolved = vi.fn();
  render(<ReviewRepairDialog review={review} onClose={vi.fn()} onResolved={onResolved} onDelete={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: "Recover the review" }));
  fireEvent.click(screen.getByRole("button", { name: "Recover review" }));
  await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
  expect(api.recoverSavedReview).toHaveBeenCalledOnce();
  expect(api.recoverSavedReview).toHaveBeenCalledWith("broken");
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
