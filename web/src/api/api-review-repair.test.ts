import { afterEach, expect, it, vi } from "vitest";
import { recoverSavedReview, redownloadReviewCommunityScan } from "./api-reviews";
import { responseError } from "./api-transport";
import type { ReviewRepairSummary } from "./api-review-types";

const repair: ReviewRepairSummary = {
  message: "This review uses a scan that no longer exists.",
  missing_scan_ids: ["missing"], detected_at: "2026-10-25T00:00:00Z",
  recover: { kept_cues: 2, removed_memberships: 1, removed_accepted_credits: 0 },
  community: { available: true, unavailable_reason: null },
};

afterEach(() => vi.unstubAllGlobals());

it.each(["review_needs_repair", "community_scan_unavailable"])("preserves the repair in a %s envelope", async (code) => {
  const error = await responseError(new Response(JSON.stringify({ detail: { code, message: "Repair needed", repair } }), { status: 409 }), "Fallback");
  expect(error).toMatchObject({ status: 409, code, message: "Repair needed", repair });
});

it("preserves a repair beside a string detail in the shared error envelope", async () => {
  const error = await responseError(new Response(JSON.stringify({ code: "review_needs_repair", detail: repair.message, repair }), { status: 409 }), "Fallback");
  expect(error).toMatchObject({ status: 409, code: "review_needs_repair", message: repair.message, repair });
});

it.each([
  { request: recoverSavedReview, path: "recover" },
  { request: redownloadReviewCommunityScan, path: "community" },
])("posts to the $path repair endpoint and returns its summary", async ({ request, path }) => {
  const summary = { id: "review/1", repair: null };
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(summary)));
  vi.stubGlobal("fetch", fetchMock);
  expect(await request("review/1")).toEqual(summary);
  expect(fetchMock).toHaveBeenCalledWith(`/api/reviews/review%2F1/repair/${path}`, expect.objectContaining({ method: "POST", credentials: "same-origin" }));
});