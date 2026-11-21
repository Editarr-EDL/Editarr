import { expect, it } from "vitest";

import type { SavedReview } from "../api";
import { buildReviewHierarchy, isNoObjectionableContentFilter, mergeSavedReviewFilters, savedReviewFilterDescription } from "./review-results";

type Filter = SavedReview["filters"][number];
const visual = (id: string, details: string | null, start = 10): Filter => ({
  id, source_scan_id: "scan-1", source_finding_id: 1, source: "visual", category: "violence", subcategory: "violence",
  severity: "medium", mcf_channel: "video", mcf_comment: null, label: "d1_violence", details, group_key: "violence",
  start_seconds: start, end_seconds: start + 2, confidence: 0.9, edit_action: "skip", selected: true,
});
const audio = (overrides: Partial<Filter>): Filter => ({
  ...visual("audio-1", null), source: "audio", category: "language", subcategory: "swearing", mcf_channel: "audio",
  label: null, group_key: null, edit_action: "mute", ...overrides,
});

it("leaves a visual description empty without a Severity and Description reply", () => {
  expect(savedReviewFilterDescription(visual("v", null))).toBe("");
  expect(savedReviewFilterDescription(visual("v", "A fistfight"))).toBe("A fistfight");
  expect(isNoObjectionableContentFilter(visual("v", null))).toBe(false);
  expect(isNoObjectionableContentFilter({ ...visual("v", null), group_key: "violence|No objectionable content" })).toBe(false);
  expect(isNoObjectionableContentFilter({ ...visual("v", null), group_key: "No objectionable content" })).toBe(true);
  expect(isNoObjectionableContentFilter({ ...visual("v", null), group_key: "No objectionable content|No objectionable content" })).toBe(true);
});

it("keeps the audio description fallbacks", () => {
  expect(savedReviewFilterDescription(audio({ label: "fudge", details: "oh fudge" }))).toBe("fudge");
  expect(savedReviewFilterDescription(audio({ details: "oh fudge" }))).toBe("oh fudge");
  expect(savedReviewFilterDescription(audio({}))).toBe("swearing");
  expect(savedReviewFilterDescription(audio({ subcategory: null, category: null }))).toBe("Unclassified");
});

it("drops empty visual descriptions from merged results", () => {
  const [alone] = mergeSavedReviewFilters([visual("a", null)], new Map());
  expect(alone.descriptions).toEqual([]);
  const [merged] = mergeSavedReviewFilters([visual("b", null, 10), visual("c", "A fistfight", 11), visual("d", null, 11.5)], new Map());
  expect(merged.filters).toHaveLength(3);
  expect(merged.descriptions).toEqual(["A fistfight"]);
});

it("groups a shown clean verdict under No objectionable content without a description", () => {
  const clean = { ...visual("clean", null), group_key: "No objectionable content" };
  const results = mergeSavedReviewFilters([clean], new Map());
  expect(results[0].descriptions).toEqual([]);
  const [family] = buildReviewHierarchy(results, "visual", null, true);
  expect(family.subcategories[0].groups.map((group) => group.name)).toEqual(["No objectionable content"]);
});
