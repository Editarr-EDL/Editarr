import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthenticationGate } from "../auth/auth-gate";
import { MediaDetail } from "../catalog/media-detail";
import { completeScan, installProductionApi, json, mediaItem, reviewFilter, savedReview } from "./app-test-fixtures";
import { openWorkflowStep } from "./review-workflow-test-helpers";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("chooses a saved review in either release step and protects unsaved edits", async () => {
  const scan = completeScan();
  const first = savedReview({
    id: "review-a", name: "First Review",
    scans: [{ scan, role: "audio" }],
    memberships: [{ scan_id: scan.id, role: "audio" }],
    filters: [reviewFilter("spoken", "audio")],
  });
  const second = savedReview({ id: "review-b", name: "Second Review" });
  const fallback = installProductionApi();
  const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const { pathname } = new URL(String(input), "http://editarr.test");
    if (pathname === "/api/library-items/movie-1/reviews") return json([first, second]);
    if (pathname === "/api/reviews/review-a" || pathname === "/api/reviews/review-b") {
      reads.push(pathname);
      return json(pathname.endsWith("review-a") ? first : second);
    }
    if (pathname === "/api/scans/history") return json([scan]);
    return fallback(input, init);
  }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

  render(<AuthenticationGate><MediaDetail item={mediaItem} breadcrumbs={[]} onNavigate={vi.fn()} onDirtyChange={vi.fn()} /></AuthenticationGate>);
  const filters = await screen.findByRole("table", { name: "Review filters by time" });
  fireEvent.click(within(filters).getByRole("checkbox", { name: /^Include/ }));
  expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled();

  await openWorkflowStep("Subtitles");
  const subtitlePicker = screen.getByLabelText("Saved review");
  expect(subtitlePicker).toHaveValue(first.id);
  fireEvent.change(subtitlePicker, { target: { value: second.id } });
  expect(confirm).toHaveBeenCalledWith("Discard unsaved review changes?");
  expect(subtitlePicker).toHaveValue(first.id);
  expect(reads).not.toContain("/api/reviews/review-b");

  confirm.mockReturnValue(true);
  fireEvent.change(subtitlePicker, { target: { value: second.id } });
  await waitFor(() => expect(subtitlePicker).toHaveValue(second.id));

  const subtitleTab = screen.getByRole("tab", { name: "Subtitles" });
  subtitleTab.focus();
  fireEvent.keyDown(subtitleTab, { key: "ArrowRight" });
  const publishPicker = screen.getByLabelText("Saved review");
  expect(screen.getByRole("tab", { name: "Publish" })).toHaveFocus();
  expect(publishPicker).toHaveValue(second.id);
  fireEvent.change(publishPicker, { target: { value: first.id } });
  await waitFor(() => expect(publishPicker).toHaveValue(first.id));

  await openWorkflowStep("Review");
  expect(screen.getByRole("heading", { name: first.name, level: 3 })).toBeVisible();
  expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
  const published = screen.getByRole("region", { name: "Published files" });
  expect(within(published).queryByRole("button", { name: /manage exports/i })).not.toBeInTheDocument();
});
