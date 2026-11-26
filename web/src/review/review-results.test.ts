import { expect, it } from "vitest";

import type { SavedReview, Scan } from "../api";
import { buildReviewHierarchy, isNoObjectionableContentFilter, mergeSavedReviewFilters, savedReviewFilterDescription, visualFilterListLabel } from "./review-results";

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

it("keeps labels on user-authored visual filters but not on model findings", () => {
  const manual = { ...visual("m", null), label: "Bar fight" };
  expect(savedReviewFilterDescription(manual, "manual")).toBe("Bar fight");
  expect(savedReviewFilterDescription(manual, "imported")).toBe("Bar fight");
  expect(savedReviewFilterDescription({ ...manual, label: null }, "manual")).toBe("violence");
  expect(savedReviewFilterDescription(visual("g", null), "generated")).toBe("");
  expect(savedReviewFilterDescription(visual("g", null), null)).toBe("");

  const scanById = new Map([
    ["scan-1", { id: "scan-1", scan_id: 1, origin: "generated" } as Scan],
    ["scan-manual", { id: "scan-manual", scan_id: 2, origin: "manual" } as Scan],
  ]);
  const [group] = mergeSavedReviewFilters([visual("g", null, 10), { ...manual, source_scan_id: "scan-manual", start_seconds: 11 }], scanById);
  expect(group.filters).toHaveLength(2);
  expect(group.descriptions).toEqual(["Bar fight"]);

  expect(visualFilterListLabel(manual, 0, "manual")).toBe("Bar fight");
  expect(visualFilterListLabel(visual("g", null), 1, "generated")).toBe("Video filter 2");
  expect(visualFilterListLabel(visual("g", "A fistfight"), 1, "generated")).toBe("A fistfight");

  const relabelled = { ...visual("r", null), label: "Bar fight", label_edited: true };
  expect(savedReviewFilterDescription(relabelled, "generated")).toBe("Bar fight");
  expect(visualFilterListLabel(relabelled, 0, "generated")).toBe("Bar fight");
  expect(savedReviewFilterDescription({ ...visual("u", null), label_edited: false }, "generated")).toBe("");
  expect(visualFilterListLabel({ ...visual("u", null), label_edited: false }, 0, "generated")).toBe("Video filter 1");
});

it("uses an existing MCF comment as the visual description", () => {
  const filter = { ...visual("legacy", "Source evidence"), mcf_comment: "Edited comment" };
  expect(savedReviewFilterDescription(filter, "generated")).toBe("Edited comment");
  expect(visualFilterListLabel(filter, 0, "generated")).toBe("Edited comment");
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

/** The previous incremental merge, kept as a behavioural reference. */
function previousMerge(filters: Filter[], scanById: Map<string, Scan>) {
  const merged: { key: string; filters: Filter[]; startSeconds: number; endSeconds: number; descriptions: string[]; scanIds: string[] }[] = [];
  const describe = (items: Filter[]) => [...new Set(items.map((item) => savedReviewFilterDescription(item, scanById.get(item.source_scan_id!)?.origin)).filter(Boolean))];
  for (const filter of [...filters].sort((left, right) => left.start_seconds - right.start_seconds)) {
    const startSeconds = filter.start_seconds;
    const endSeconds = Math.max(startSeconds, filter.end_seconds ?? startSeconds + 1);
    const current = merged[merged.length - 1];
    if (!current || startSeconds > current.endSeconds) {
      merged.push({ key: filter.id, filters: [filter], startSeconds, endSeconds, descriptions: describe([filter]),
        scanIds: scanById.has(filter.source_scan_id!) ? [filter.source_scan_id!] : [] });
    } else {
      current.filters.push(filter);
      current.endSeconds = Math.max(current.endSeconds, endSeconds);
      current.key = `${current.key}:${filter.id}`;
      current.descriptions = describe(current.filters);
      if (scanById.has(filter.source_scan_id!)) {
        current.scanIds = [...new Set([...current.scanIds, filter.source_scan_id!])].sort((left, right) => (
          (scanById.get(left)?.scan_id ?? 0) - (scanById.get(right)?.scan_id ?? 0)));
      }
    }
  }
  return merged;
}

function syntheticReview(count: number) {
  // Scan numbers repeat so ties exercise stable ordering; "scan-missing" is not a known scan.
  const scanById = new Map(["scan-c", "scan-a", "scan-b", "scan-d"].map((id, index) => [id, { id, scan_id: [3, 1, 2, 1][index], origin: id === "scan-b" ? "manual" : "generated" } as Scan]));
  const scanIds = ["scan-c", "scan-missing", "scan-a", "scan-d", "scan-b"];
  const filters = Array.from({ length: count }, (_, index): Filter => ({
    ...visual(`f${index}`, index % 3 === 0 ? null : `Scene ${index % 97}`, index * 0.001),
    source_scan_id: scanIds[index % scanIds.length],
  }));
  filters.push(visual("late-a", null, count), visual("late-b", "Late scene", count + 0.5));
  return { filters, scanById };
}

it("merges a large group identically to the previous implementation", () => {
  const small = syntheticReview(300);
  expect(mergeSavedReviewFilters(small.filters, small.scanById)).toEqual(previousMerge(small.filters, small.scanById));

  const { filters, scanById } = syntheticReview(5_000);
  const started = performance.now();
  const results = mergeSavedReviewFilters(filters, scanById);
  const elapsed = performance.now() - started;
  expect(results).toHaveLength(2);
  const [large, late] = results;
  expect(large.filters).toHaveLength(5_000);
  expect(large.key).toBe(filters.slice(0, 5_000).map((filter) => filter.id).join(":"));
  expect(large.descriptions).toEqual([...new Set(filters.slice(0, 5_000).map((filter) => (
    filter.source_scan_id === "scan-b" ? filter.details || filter.label : filter.details
  )).filter(Boolean))]);
  expect(large.scanIds).toEqual(["scan-a", "scan-d", "scan-b", "scan-c"]);
  expect(late.descriptions).toEqual(["Late scene"]);
  expect(elapsed).toBeLessThan(2_000);
});
