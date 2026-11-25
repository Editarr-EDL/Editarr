import { describe, expect, it } from "vitest";

import {
  addReviewMembership,
  applyCreditsAcceptance,
  applyReviewRetimeResult,
  createReviewDraft,
  deleteReviewFilter,
  discardReviewDraft,
  duplicateReviewDraft,
  isReviewDraftDirty,
  isReviewDraftContentDirty,
  removeReviewMembership,
  replaceReviewDraftAfterSave,
  restoreReviewMembership,
  snapshotReviewFilters,
  stageManualReviewFilter,
  updateReviewFilter,
  applyReviewFilterResult,
} from "./review-draft";
import type { ReviewDraft } from "./review-draft";
import type { SavedReview } from "../api";
import { savedReviewFilterDescription } from "./review-results";

const review: SavedReview = {
  id: "review-1",
  repair: null,
  media_item_id: "media-1",
  target: { source: "jellyfin", item_id: "item-1", media_source_id: null },
  name: "Review",
  revision: 2,
  content_revision: 1,
  edl_content_revision: 1,
  mcf_content_revision: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  exports: {
    exports: {
      edl: { format: "edl", present: false, exported_at: null, review_id: null, exported_format_revision: null, current: false, outdated: false, exported_scans: [], exported_review: null },
      mcf: { format: "mcf", present: false, exported_at: null, review_id: null, exported_format_revision: null, current: false, outdated: false, exported_scans: [], exported_review: null },
    },
  },
  mcf_document: { title: "Review", year: null, work_type: "movie", season: null, episode: null, source_url: null, imdb_url: null, release: null, comment: null, material_start_seconds: 0, material_end_seconds: 100 },
  synchronization: { start_seconds: 0, end_seconds: 100, end_mode: "before_credits" },
  accepted_credits: null,
  memberships: [{ scan_id: "scan-1", role: "audio" }],
  scans: [],
  filters: [{
    id: "filter-1", source_scan_id: "scan-1", source_finding_id: 10, source: "audio",
    category: "language", subcategory: "swearing", severity: "medium", mcf_channel: "audio", mcf_comment: null,
    label: "word", details: null, group_key: null,
    start_seconds: 1, end_seconds: 2, confidence: 1, edit_action: "mute", selected: true,
  }],
};

describe("review draft state", () => {
  it("creates unique dirty sessions for named unsaved reviews", () => {
    const first = createReviewDraft(null, "First review");
    const second = createReviewDraft(null, "Second review");

    expect(first.id).not.toBe(second.id);
    expect(isReviewDraftDirty(first)).toBe(true);
    expect(isReviewDraftDirty(second)).toBe(true);
    expect(first.baselineState.name).toBe("");
    expect(isReviewDraftContentDirty(first)).toBe(false);
    const discarded = discardReviewDraft(first);
    expect(discarded.name).toBe("");
    expect(isReviewDraftDirty(discarded)).toBe(false);
  });

  it("marks only direct edits of a community filter as the user's, never bulk results", () => {
    const community = { ...review.filters[0], id: "c-1", source_scan_id: null, source_finding_id: null,
      community_source_id: "src", community_filter_id: "f_1", community_edited: false };
    const draft = createReviewDraft({ ...review, filters: [...review.filters, community] });
    expect(updateReviewFilter(draft, "c-1", { label: "word" }).filters[1].community_edited).toBe(false);
    expect(updateReviewFilter(draft, "c-1", { selected: true }).filters[1].community_edited).toBe(false);
    expect(updateReviewFilter(draft, "c-1", { selected: false }).filters[1].community_edited).toBe(true);
    expect(updateReviewFilter(draft, "c-1", { start_seconds: 1.5 }).filters[1].community_edited).toBe(true);
    expect(updateReviewFilter(draft, "filter-1", { selected: false }).filters[0].community_edited).toBeUndefined();
    const defaulted = applyReviewFilterResult(draft, { filters: [review.filters[0], { ...community, selected: false }] });
    expect(defaulted.filters[1]).toMatchObject({ selected: false, community_edited: false });
  });

  it("keeps filter identity local and isolates remove/re-add snapshots", () => {
    const initial = createReviewDraft(review);
    const removed = removeReviewMembership(initial, "scan-1");
    expect(removed.filters).toEqual([]);
    expect(isReviewDraftDirty(removed)).toBe(true);
    const restored = restoreReviewMembership(removed, { scan_id: "scan-1", role: "audio" });
    expect(restored.filters[0].id).toBe("filter-1");
    expect(isReviewDraftDirty(restored)).toBe(false);
  });

  it("supports immutable edits, staged manual filters, and duplicate identities", () => {
    const initial = createReviewDraft(review);
    const changed = updateReviewFilter(initial, "filter-1", { label: "changed" });
    expect(initial.filters[0].label).toBe("word");
    expect(changed.filters[0].label).toBe("changed");
    expect(changed.filters[0].label_edited).toBe(true);
    const reverted = updateReviewFilter(changed, "filter-1", { label: "word" });
    expect(reverted.filters[0].label_edited).toBeUndefined();
    expect(isReviewDraftDirty(reverted)).toBe(false);

    const { label_edited: _omitted, ...echoed } = changed.filters[0];
    const adjusted = applyReviewFilterResult(changed, { filters: [{ ...echoed, selected: false }] });
    expect(adjusted.filters[0]).toMatchObject({ label: "changed", label_edited: true, selected: false });
    const staged = stageManualReviewFilter(changed, {
      id: "manual-1", source: "visual", category: "violence", subcategory: "violence", severity: null, mcf_channel: null, mcf_comment: null, label: "manual", details: null,
      group_key: null, start_seconds: 3, end_seconds: 4, edit_action: "skip",
    });
    expect(staged.newManualFilters).toHaveLength(1);
    const duplicate = duplicateReviewDraft(staged, "Copy");
    expect(duplicate.name).toBe("Copy");
    expect(duplicate.filters[0].id).not.toBe("filter-1");
  });

  it("handles membership additions and filter deletion without mutation", () => {
    const initial = createReviewDraft(review);
    const added = addReviewMembership(initial, { scan_id: "scan-2", role: "video" });
    expect(added.memberships).toHaveLength(2);
    const snapshotted = snapshotReviewFilters(added, [{
      ...review.filters[0], id: "source-id", source_scan_id: "scan-2", source_finding_id: 20,
      source: "visual", category: "violence", subcategory: "violence", edit_action: "skip",
    }]);
    expect(snapshotted.filters).toHaveLength(2);
    expect(snapshotted.filters[1].id).not.toBe("source-id");
    expect(deleteReviewFilter(snapshotted, "filter-1").filters).toHaveLength(1);
    expect(initial.memberships).toHaveLength(1);
  });

  it("replaces the draft baseline after a successful save", () => {
    const draft = createReviewDraft(review);
    const saved = { ...review, revision: 3, name: "Saved" };
    const replaced = replaceReviewDraftAfterSave(draft, saved);
    expect(replaced.revision).toBe(3);
    expect(replaced.name).toBe("Saved");
    expect(isReviewDraftDirty(replaced)).toBe(false);
  });

  it("distinguishes a name-only rename from content changes", () => {
    const initial = createReviewDraft(review);
    const renamed = { ...initial, name: "Renamed" };
    expect(isReviewDraftDirty(renamed)).toBe(true);
    expect(isReviewDraftContentDirty(renamed)).toBe(false);
    expect(isReviewDraftContentDirty(updateReviewFilter(initial, "filter-1", { label: "changed" }))).toBe(true);
  });

  it.each([
    ["memberships", { memberships: [] }],
    ["filters", { filters: [] }],
    ["mcfDocument", { mcfDocument: { ...review.mcf_document, comment: "changed" } }],
    ["synchronization", { synchronization: { ...review.synchronization, end_seconds: 110 } }],
    ["acceptedCredits", { acceptedCredits: { scan_id: "credits-1", candidate_id: "candidate-1", accepted_boundary_seconds: 90 } }],
    ["mcfMetadataSeed", { mcfMetadataSeed: { kind: "catalog" } }],
    ["newManualFilters", { newManualFilters: [{
      id: "manual-1", source: "audio", category: "language", subcategory: "swearing",
      severity: null, mcf_channel: null, mcf_comment: null, label: "manual", details: null,
      group_key: null, start_seconds: 3, end_seconds: 4, edit_action: "mute",
    }] }],
  ] satisfies [string, Partial<ReviewDraft>][])("tracks %s changes independently of the name", (_field, patch) => {
    const initial = createReviewDraft(review);
    const changed = { ...initial, ...patch, name: "Renamed" };

    expect(isReviewDraftDirty(changed)).toBe(true);
    expect(isReviewDraftContentDirty(changed)).toBe(true);
    expect(discardReviewDraft(changed)).toMatchObject(initial);
    expect(isReviewDraftDirty(initial)).toBe(false);
  });

  it("keeps the saved baseline independent of mutable draft snapshots", () => {
    const initial = createReviewDraft(review);
    initial.memberships[0].role = "both";
    initial.filters[0].label = "changed";
    initial.mcfDocument.comment = "changed";
    initial.synchronization.end_seconds = 110;

    expect(initial.baselineState.memberships).toEqual(review.memberships);
    expect(initial.baselineState.filters).toEqual(review.filters);
    expect(initial.baselineState.mcfDocument).toEqual(review.mcf_document);
    expect(initial.baselineState.synchronization).toEqual(review.synchronization);
    expect(isReviewDraftContentDirty(initial)).toBe(true);
    expect(isReviewDraftDirty(discardReviewDraft(initial))).toBe(false);
  });

  it("tracks MCF metadata and source choice without mutating saved revisions or exports", () => {
    const initial = createReviewDraft(review);
    const metadataChanged = {
      ...initial,
      mcfDocument: { ...initial.mcfDocument, comment: "Draft-only MCF note" },
      mcfMetadataSeed: { kind: "catalog" as const },
    };

    expect(isReviewDraftDirty(metadataChanged)).toBe(true);
    expect(isReviewDraftContentDirty(metadataChanged)).toBe(true);
    expect(metadataChanged.edlContentRevision).toBe(review.edl_content_revision);
    expect(metadataChanged.mcfContentRevision).toBe(review.mcf_content_revision);
    expect(review.mcf_document.comment).toBeNull();
    expect(review.exports.exports.mcf.outdated).toBe(false);

    const discarded = discardReviewDraft(metadataChanged);
    expect(discarded.mcfDocument).toEqual(review.mcf_document);
    expect(discarded.mcfMetadataSeed).toBeNull();
    expect(isReviewDraftDirty(discarded)).toBe(false);
  });

  it("tracks retiming and accepted credits through dirty state and discard", () => {
    const initial = createReviewDraft(review);
    const retimed = applyReviewRetimeResult(initial, {
      synchronization: { start_seconds: 10, end_seconds: 210, end_mode: "file_end" },
      mcf_document: { ...initial.mcfDocument, material_start_seconds: 10, material_end_seconds: 190 },
      filters: [{ ...initial.filters[0], start_seconds: 12, end_seconds: 14 }],
      accepted_credits: null,
      new_manual_filters: [],
    });
    const accepted = applyCreditsAcceptance(retimed, {
      accepted_credits: { scan_id: "credits-1", candidate_id: "candidate-1", accepted_boundary_seconds: 190 },
      membership: { scan_id: "credits-1", role: "video" },
      filter: {
        id: "credits-filter", source_scan_id: "credits-1", source_finding_id: 42,
        source: "visual", category: "dispensable_scenes", subcategory: "dispensable",
        severity: "medium", mcf_channel: "video", mcf_comment: "End Credits",
        label: "End Credits", details: "End Credits", group_key: null,
        start_seconds: 190, end_seconds: 210, confidence: 0.72, edit_action: "skip", selected: true,
      },
    });

    expect(accepted.synchronization).toEqual({ start_seconds: 10, end_seconds: 210, end_mode: "file_end" });
    expect(accepted.acceptedCredits?.accepted_boundary_seconds).toBe(190);
    expect(accepted.memberships).toContainEqual({ scan_id: "credits-1", role: "video" });
    expect(accepted.filters[accepted.filters.length - 1]?.mcf_comment).toBe("End Credits");
    expect(isReviewDraftDirty(accepted)).toBe(true);

    const discarded = discardReviewDraft(accepted);
    expect(discarded.synchronization).toEqual(review.synchronization);
    expect(discarded.acceptedCredits).toBeNull();
    expect(discarded.filters).toEqual(review.filters);
    expect(isReviewDraftDirty(discarded)).toBe(false);
  });

  it("replaces an earlier accepted credits scan and snapshot", () => {
    const first = applyCreditsAcceptance(createReviewDraft(review), {
      accepted_credits: {
        scan_id: "credits-1",
        candidate_id: "candidate-1",
        accepted_boundary_seconds: 90,
      },
      membership: { scan_id: "credits-1", role: "video" },
      filter: {
        id: "credits-filter-1", source_scan_id: "credits-1", source_finding_id: 41,
        source: "visual", category: "dispensable_scenes", subcategory: "dispensable",
        severity: "medium", mcf_channel: "video", mcf_comment: "End Credits",
        label: "End Credits", details: "End Credits", group_key: null,
        start_seconds: 90, end_seconds: 100, confidence: 0.72,
        edit_action: "skip", selected: true,
      },
    });

    const replaced = applyCreditsAcceptance(first, {
      accepted_credits: {
        scan_id: "credits-2",
        candidate_id: "candidate-2",
        accepted_boundary_seconds: 92,
      },
      membership: { scan_id: "credits-2", role: "video" },
      filter: {
        id: "credits-filter-2", source_scan_id: "credits-2", source_finding_id: 42,
        source: "visual", category: "dispensable_scenes", subcategory: "dispensable",
        severity: "high", mcf_channel: "video", mcf_comment: "End Credits",
        label: "End Credits", details: "End Credits", group_key: null,
        start_seconds: 92, end_seconds: 100, confidence: 0.9,
        edit_action: "skip", selected: true,
      },
    });

    expect(replaced.acceptedCredits?.scan_id).toBe("credits-2");
    expect(replaced.memberships).toContainEqual({ scan_id: "credits-2", role: "video" });
    expect(replaced.memberships).not.toContainEqual({ scan_id: "credits-1", role: "video" });
    expect(replaced.filters.some((filter) => filter.source_scan_id === "credits-1")).toBe(false);
    expect(replaced.filters.filter((filter) => filter.source_scan_id === "credits-2")).toHaveLength(1);
  });

  it("retimes the accepted boundary without replacing immutable detector provenance", () => {
    const initial = createReviewDraft({
      ...review,
      accepted_credits: {
        scan_id: "credits-1",
        source_finding_id: 42,
        candidate_id: "candidate-1",
        detected_boundary_seconds: 90,
        accepted_boundary_seconds: 92,
        confidence: 0.8,
        method: "hybrid",
        accepted_at: "2026-01-02T00:00:00Z",
      },
    });

    const retimed = applyReviewRetimeResult(initial, {
      synchronization: { start_seconds: 0, end_seconds: 80, end_mode: "file_end" },
      mcf_document: initial.mcfDocument,
      filters: initial.filters,
      accepted_credits: {
        scan_id: "credits-1",
        candidate_id: "candidate-1",
        accepted_boundary_seconds: 73.6,
      },
      new_manual_filters: [],
    });

    expect(retimed.acceptedCredits).toMatchObject({
      source_finding_id: 42,
      detected_boundary_seconds: 90,
      accepted_boundary_seconds: 73.6,
      confidence: 0.8,
      method: "hybrid",
      accepted_at: "2026-01-02T00:00:00Z",
    });
  });

  it("retimes from the current draft result on repeated adjustment", () => {
    const initial = createReviewDraft(review);
    const first = applyReviewRetimeResult(initial, {
      synchronization: { start_seconds: 10, end_seconds: 110, end_mode: "before_credits" },
      mcf_document: { ...initial.mcfDocument, material_start_seconds: 10, material_end_seconds: 110 },
      filters: [{ ...initial.filters[0], start_seconds: 11, end_seconds: 12 }],
      accepted_credits: null,
      new_manual_filters: [],
    });
    const second = applyReviewRetimeResult(first, {
      synchronization: { start_seconds: 20, end_seconds: 220, end_mode: "before_credits" },
      mcf_document: { ...first.mcfDocument, material_start_seconds: 20, material_end_seconds: 220 },
      filters: [{ ...first.filters[0], start_seconds: 22, end_seconds: 24 }],
      accepted_credits: null,
      new_manual_filters: [],
    });

    expect(second.synchronization.start_seconds).toBe(20);
    expect(second.filters[0].start_seconds).toBe(22);
    expect(first.filters[0].start_seconds).toBe(11);
  });

  it("copies MCF metadata into duplicates and resets server revisions", () => {
    const source = createReviewDraft(review);
    const duplicate = duplicateReviewDraft({
      ...source,
      mcfDocument: { ...source.mcfDocument, title: "Metadata variant" },
      mcfMetadataSeed: { kind: "scan", scan_id: "scan-1" },
    }, "Copy");

    expect(duplicate.revision).toBe(0);
    expect(duplicate.contentRevision).toBe(0);
    expect(duplicate.edlContentRevision).toBe(0);
    expect(duplicate.mcfContentRevision).toBe(0);
    expect(duplicate.mcfDocument.title).toBe("Metadata variant");
    expect(duplicate.mcfMetadataSeed).toBeNull();
  });

  describe("label provenance from server responses", () => {
    const strip = ({ label_edited: _flag, ...filter }: SavedReview["filters"][number]) => filter;
    const relabelled = { ...review.filters[0], id: "relabelled", source: "visual" as const, category: "violence" as const, subcategory: "violence" as const,
      mcf_channel: "video" as const, edit_action: "skip" as const, label: "Bar fight", label_edited: true };
    const untouched = { ...relabelled, id: "untouched", label: "d1_violence", label_edited: false, start_seconds: 5, end_seconds: 6 };
    const saved = { ...review, filters: [relabelled, untouched], base_filters: [relabelled, untouched] };

    it("keeps a relabelled label through a retime that omits the flag", () => {
      const initial = createReviewDraft(saved);
      const retimed = applyReviewRetimeResult(initial, {
        synchronization: initial.synchronization, mcf_document: initial.mcfDocument, accepted_credits: null, new_manual_filters: [],
        filters: [{ ...strip(relabelled), start_seconds: 3, end_seconds: 4 }, strip(untouched)],
        base_filters: [{ ...strip(relabelled), start_seconds: 3, end_seconds: 4 }, strip(untouched)],
      });
      expect(retimed.filters.map((filter) => [filter.id, filter.label_edited, filter.start_seconds])).toEqual([["relabelled", true, 3], ["untouched", false, 5]]);
      expect(retimed.filterBases.relabelled.base.label_edited).toBe(true);
      expect(retimed.filters.map((filter) => savedReviewFilterDescription(filter, "generated"))).toEqual(["Bar fight", ""]);
      expect(retimed.filterBases.untouched.base.label_edited).toBe(false);
    });

    it("leaves a draft clean when defaults change nothing and dirty when they do", () => {
      const initial = createReviewDraft(saved);
      const echo = (filters: SavedReview["filters"]) => filters.map(strip);
      const unchanged = applyReviewFilterResult(initial, { filters: echo(saved.filters), base_filters: echo(saved.base_filters) });
      expect(unchanged.filters.map((filter) => filter.label_edited)).toEqual([true, false]);
      expect(Object.values(unchanged.filterBases).map((pair) => pair.base.label_edited)).toEqual([true, false]);
      expect(isReviewDraftDirty(unchanged)).toBe(false);

      const severities = echo(saved.filters).map((filter) => filter.id === "untouched" ? { ...filter, severity: "high" as const } : filter);
      const changed = applyReviewFilterResult(initial, { filters: severities, base_filters: severities });
      expect(isReviewDraftDirty(changed)).toBe(true);
    });

    it("ignores label_edited alone when comparing drafts", () => {
      const initial = createReviewDraft(saved);
      expect(isReviewDraftDirty({ ...initial, filters: initial.filters.map(strip) })).toBe(false);
    });
  });
});
