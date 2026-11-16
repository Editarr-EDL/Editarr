import { expect, it } from "vitest";
import type { Scan } from "../api/api-scan-types";
import { communityReviewScans } from "./community-review-scans";

const target = { source: "jellyfin" as const, item_id: "movie", media_source_id: "version-2" };
const scan = { id: "source", scan_audio: true, scan_video: true, media_source_id: "original-version",
  community_provenance: { author_id: "original-author", contribution_id: "root", revision_id: "revision", digest: "a".repeat(64) },
  community_approvals: [{ source: "jellyfin", target_item_id: "movie", media_source_id: "version-2", channels: ["video"] }] } as Scan;

it("offers only approved target channels while retaining immutable source metadata", () => {
  const result = communityReviewScans([scan], target);
  expect(result).toHaveLength(1);
  expect(result[0].scan_audio).toBe(false);
  expect(result[0].scan_video).toBe(true);
  expect(result[0].community_provenance?.author_id).toBe("original-author");
  expect(result[0].media_source_id).toBe("original-version");
  expect(scan.scan_audio).toBe(true);
});

it("does not expose verified evidence to an unapproved item or version", () => {
  expect(communityReviewScans([scan], { ...target, item_id: "different" })).toEqual([]);
  expect(communityReviewScans([scan], { ...target, media_source_id: "different" })).toEqual([]);
  expect(communityReviewScans([{ ...scan, community_approvals: [] }], target)).toEqual([]);
});

it("preserves ordinary local and historical imported scans without community evidence", () => {
  const local = { ...scan, community_provenance: null, community_approvals: [] };
  expect(communityReviewScans([local], target)).toEqual([local]);
});