import { useCategorySort } from "./review-workflow-test-helpers";
import { cleanup, fireEvent, render as renderComponent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { json, installProductionApi, completeScan, savedReview, reviewFilter, openFirstReviewFindingGroup } from "./app-test-fixtures";

describe("filter previews follow the filter's action", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("plays a skipped audio filter as a video clip from the video preview", async () => {
    const scan = completeScan();
    const skipped = { ...reviewFilter("audio-filter", "audio"), edit_action: "skip" as const };
    const review = savedReview({
      memberships: [{ scan_id: scan.id, role: "both" }],
      scans: [{ scan, role: "both" }],
      filters: [skipped],
    });
    const previewPaths: string[] = [];
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:skip-preview");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1") return json(review);
      if (url.pathname.endsWith("preview")) {
        previewPaths.push(url.pathname);
        return new Response(new Blob(["preview"]));
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await useCategorySort();
    await openFirstReviewFindingGroup(await screen.findByRole("region", { name: "Audio review filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview Sp*k*n w*rd at 0:05" }));

    const player = await screen.findByLabelText("Video preview for Spoken word at 0:05");
    expect(player.tagName).toBe("VIDEO");
    expect(player).toHaveAttribute("src", "blob:skip-preview");
    expect(previewPaths).toEqual(["/api/reviews/review-1/filters/audio-filter/video-preview"]);
  });
});
