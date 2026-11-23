import { openWorkflowStep } from "./review-workflow-test-helpers";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import type { SavedReview } from "../api";
import { testPublication } from "../review/export-test-fixtures";
import { emptyExports, mediaItem, json, installProductionApi, completeScan, savedReview, reviewFilter } from "./app-test-fixtures";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("does not mark a prior EDL current while saving only an MCF document edit", async () => {
  const scan = completeScan();
  const exports = emptyExports();
  exports.exports.edl = { format: "edl", present: true, exported_at: "2026-09-14T12:00:00Z", review_id: "review-1", exported_format_revision: 1, current: false, outdated: true, exported_scans: [], exported_review: null };
  let review = savedReview({
    memberships: [{ scan_id: scan.id, role: "audio" }], scans: [{ scan, role: "audio" }],
    filters: [reviewFilter("audio-filter", "audio")], owner_id: 1, policy_owner_id: 1, policy_revision: 1, exports,
  });
  const publications = [testPublication({ review_id: review.id, exported_format_revision: 1, current: false, outdated: true })];
  const activeItem = { ...mediaItem, exports, versions: mediaItem.versions.map((version) => ({ ...version, exports })) };
  const baseApi = installProductionApi();
  let saved = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    const method = init?.method || "GET";
    if (url.pathname === "/api/library-items/movie-1" && method === "GET") return json(activeItem);
    if (url.pathname === "/api/scans/history") return json([scan]);
    if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
    if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
    if (url.pathname === "/api/library-items/movie-1/exports" && method === "GET") {
      return saved ? new Promise<Response>(() => {}) : json({ ...exports, publications });
    }
    if (url.pathname === "/api/exports") {
      return saved ? new Promise<Response>(() => {}) : json({ publications });
    }
    if (url.pathname === "/api/reviews/review-1" && method === "PUT") {
      const payload = JSON.parse(String(init?.body)) as Pick<SavedReview, "mcf_document">;
      if (payload.mcf_document.comment !== "Updated MCF comment") {
        throw new Error("Expected a document-only change");
      }
      review = { ...review, ...payload, revision: 2, mcf_content_revision: 2 };
      saved = true;
      return json(review);
    }
    return baseApi(input, init);
  }));
  window.location.hash = "#/library/movies";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
  await openWorkflowStep("Review");
  const row = (await screen.findByRole("button", { name: "Open review Existing review" })).closest("tr")!;
  expect(row).toHaveTextContent("EDL · Outdated");
  const published = await screen.findByRole("listitem", { name: "EDL · Personal · You" });
  expect(published).toHaveTextContent("Outdated");

  const comment = await screen.findByLabelText("MCF document comment");
  fireEvent.change(comment, { target: { value: "Updated MCF comment" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save Changes" })));
  await waitFor(() => expect(saved).toBe(true));
  expect(row).toHaveTextContent("EDL · Outdated");
  expect(published).toHaveTextContent("Outdated");
});
