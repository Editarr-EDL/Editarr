import type { NewManualReviewFilter, ReviewFilter } from "../api/api-review-types";

type EditableFields = "category" | "subcategory" | "end_seconds";
export type ManualFilterDraft = Omit<NewManualReviewFilter, EditableFields> & Pick<ReviewFilter, EditableFields>;

export function manualDraftRows(filters: ManualFilterDraft[]): ReviewFilter[] {
  return filters.map((filter) => ({
    ...filter, source_scan_id: null, source_finding_id: null,
    confidence: filter.confidence ?? null, selected: filter.selected ?? true,
  }));
}

export function validManualDraft(filter: ManualFilterDraft): filter is NewManualReviewFilter {
  return Boolean(filter.category && filter.subcategory) && Number.isFinite(filter.start_seconds)
    && filter.start_seconds >= 0 && filter.end_seconds !== null && Number.isFinite(filter.end_seconds)
    && filter.end_seconds > filter.start_seconds;
}
