import type { Route } from "@playwright/test";
import type { ExportPublication } from "../src/api/api-mcf-types";
import { testPublication } from "../src/review/export-test-fixtures";
import type { MockReview, previousEdl, emptyExports as makeEmptyExports, json as respondJson } from "./app-fixtures";

/** Browser API fixtures for review exports, deletion, and subtitle discovery. */
export function mockReviewWorkflowRoutes({ readReviews, removeReview, legacyEdl, emptyExports, json }: {
  readReviews: () => MockReview[];
  removeReview: (reviewId: string) => void;
  legacyEdl: typeof previousEdl.exports;
  emptyExports: typeof makeEmptyExports;
  json: typeof respondJson;
}) {
  let publications: ExportPublication[] = [];
  let legacyEdlPresent = true;
  return async (route: Route, url: URL, method: string): Promise<boolean> => {
    const reviews = readReviews();
    if (url.pathname === "/api/library-items/movie-1/exports/edl" && method === "DELETE") {
      legacyEdlPresent = false;
      await route.fulfill({ status: 204 }); return true;
    }
    if (url.pathname === "/api/media/subtitles/publications") {
      await json(route, { published_subtitles: [] }); return true;
    }
    if (/^\/api\/reviews\/[^/]+\/subtitles\/sources$/.test(url.pathname)) {
      await json(route, { sources: [], audio_sources: [], audio_source_id: null, fallback: "scan_required", published_subtitles: [] }); return true;
    }
    if (url.pathname === "/api/exports" && method === "GET") {
      const mediaItemId = url.searchParams.get("media_item_id");
      const mediaSourceId = url.searchParams.get("media_source_id");
      await json(route, { publications: publications.filter((entry) => entry.media_item_id === mediaItemId && entry.media_source_id === mediaSourceId) }); return true;
    }
    if (url.pathname === "/api/library-items/movie-1/exports" && method === "GET") {
      const source = url.searchParams.get("media_source_id");
      const visible = publications.filter((entry) => entry.media_source_id === source);
      const owner = reviews.find((review) => review.id === visible.find((entry) => entry.format === "edl")?.review_id);
      const legacy = legacyEdlPresent && source === "source-1" ? legacyEdl : emptyExports().exports;
      await json(route, { exports: { ...legacy, ...(owner ? { edl: owner.exports.exports.edl } : {}) }, legacy_exports: legacy, publications: visible }); return true;
    }
    const deletionRoute = url.pathname.match(/^\/api\/reviews\/([^/]+)\/deletion$/)?.[1];
    if (deletionRoute) {
      const review = reviews.find((candidate) => candidate.id === deletionRoute)!;
      if (method === "GET") {
        await json(route, {
          review_id: review.id, revision: review.revision, confirmation: "c".repeat(64), legacy_formats: [],
          files: publications.filter((entry) => entry.review_id === review.id).map((publication) => ({ publication, choices: ["retain", "remove"] })),
        }); return true;
      }
      if (method === "POST") {
        const payload = route.request().postDataJSON() as { files: { export_id: number; choice: "retain" | "remove" }[] };
        const retained = payload.files.filter((file) => file.choice === "retain").map((file) => file.export_id);
        const removed = payload.files.filter((file) => file.choice === "remove").map((file) => file.export_id);
        removeReview(review.id);
        publications = publications.filter((entry) => !removed.includes(entry.id)).map((entry) => retained.includes(entry.id) ? { ...entry, review_id: null } : entry);
        await json(route, { review_id: review.id, retained, removed }); return true;
      }
    }
    const reviewExport = url.pathname.match(/^\/api\/reviews\/([^/]+)\/(?:exports\/)?edl$/)?.[1];
    if (reviewExport && method === "POST") {
      const review = reviews.find((candidate) => candidate.id === reviewExport)!;
      const source = review.target.media_source_id ?? "";
      const exportedScans = [{ id: "scan-history", scan_id: 2, role: "both" }];
      review.exports = { exports: { ...review.exports.exports, edl: { ...emptyExports().exports.edl, present: true, exported_at: "2026-08-20T00:00:00Z", review_id: review.id, exported_format_revision: review.edl_content_revision, current: true, exported_scans: exportedScans, exported_review: null } } };
      if (url.pathname.endsWith("/exports/edl")) {
        const prior = publications.find((entry) => entry.media_item_id === review.media_item_id && entry.format === "edl");
        const published = testPublication({
          media_item_id: review.media_item_id, media_source_id: source, review_id: review.id,
          exported_format_revision: review.edl_content_revision, publication_revision: (prior?.publication_revision || 0) + 1,
        });
        publications = [...publications.filter((entry) => entry !== prior), published];
        await json(route, published); return true;
      }
      await json(route, { format: "edl", review_id: review.id, exported_format_revision: review.edl_content_revision, exported_at: "2026-08-20T00:00:00Z", exported_scans: exportedScans, exported_review: review }); return true;
    }
    return false;
  };
}
