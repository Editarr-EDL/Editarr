import type { ExportPublication } from "../api/api-mcf-types";
import type { SavedReviewSummary } from "../api/api-review-types";
import { EXPORT_CONDITIONS, exportCondition } from "./export-model";

/** Name the review a publication came from without revealing hidden or deleted reviews. */
export function publicationSource(publication: ExportPublication, reviewId: string | null, reviews: readonly SavedReviewSummary[]): string {
  if (publication.review_id === null) return "a deleted or unavailable review";
  if (publication.review_id === reviewId) return "this review";
  const name = reviews.find((review) => review.id === publication.review_id)?.name;
  return name ? `“${name}”` : "another saved review";
}

/** Condition badge plus its consequence, so regeneration and withdrawal read as sentences. */
export function ExportConditionText({ publication, source }: { publication: ExportPublication; source: string }) {
  const condition = exportCondition(publication);
  const { label, explanation } = EXPORT_CONDITIONS[condition];
  return <span className="export-condition">
    <span className={`export-badge export-${condition}`}>{label}</span>
    <small>From {source}. {explanation}</small>
  </span>;
}
