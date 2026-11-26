import { Fragment, type ReactNode } from "react";
import type { Scan } from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import { formatTime } from "../shared/display";
import { MoreMenu } from "../ui/MoreMenu";
import { CommunityFilterFlags, CommunityFilterSource } from "./review-community-badges";
import { isCommunityFilter } from "./review-community";
import { previewsAudio } from "./review-preview";
import { ReviewFilterEditor } from "./review-filter-editor";
import { CensoredAudioWord, censorAudioWord, filterSourceOrigin, savedReviewFilterDescription, severityText, type TieredReviewResult } from "./review-results";
import { ReviewScanInfo, reviewScanSourceLabel } from "./review-scan-source";

type Filter = SavedReview["filters"][number];

/** What every filter row of a review shares: scans, policy, permissions, preview and the edit callbacks. */
export interface ReviewFilterRowContext {
  scanById: Map<string, Scan>;
  requiredIds: Set<string>;
  lockedSeverities: NonNullable<SavedReview["policy_locked_severities"]>;
  busy: boolean;
  filtersEditable: boolean;
  cuesEditable: boolean;
  metadataEditable: boolean;
  previewDisabled: boolean;
  preview: { filterId: string; url: string } | null;
  /** Show the category name in place of a missing description (the time-sorted list has no category headers). */
  categoryFallback: boolean;
  editingFilterId: string | null;
  setEditingFilterId: (update: (current: string | null) => string | null) => void;
  onToggleFilters: (filterIds: string[], selected: boolean) => void;
  onChangeFilter: (filterId: string, patch: Partial<Filter>) => void;
  onDeleteFilter: (filterId: string) => void;
  onPreviewFilter: (filter: Filter) => void;
  renderFilterVote?: (filter: Filter, name: string) => ReactNode;
}

const severityLevels = ["high", "medium", "low"] as const;

/**
 * One review result as a row of Use | Filter | Actions. The Filter cell's first line holds the time range,
 * description, severity and action chips; its muted second line the source, community confidence,
 * contributors and flags. Each filter's actions stay on one line: Preview, its vote (community filters) and a
 * "⋯" menu with Edit and Delete. An open editor follows in its own row.
 */
export function ReviewFilterRow({ item, source, categoryName, context }: {
  item: TieredReviewResult; source: "audio" | "visual"; categoryName: string; context: ReviewFilterRowContext;
}) {
  const {
    scanById, requiredIds, lockedSeverities, busy, filtersEditable, cuesEditable, metadataEditable, previewDisabled,
    preview, categoryFallback, editingFilterId, setEditingFilterId, onToggleFilters, onChangeFilter, onDeleteFilter,
    onPreviewFilter, renderFilterVote,
  } = context;
  const result = item.result;
  const time = formatTime(result.startSeconds);
  const sourceScans = result.scanIds.map((id) => scanById.get(id)).filter((scan): scan is Scan => !!scan);
  const rowSelected = result.filters.every((filter) => filter.selected);
  const rowMixed = !rowSelected && result.filters.some((filter) => filter.selected);
  const editingFilter = result.filters.find((filter) => filter.id === editingFilterId);
  const rowName = result.descriptions.join("; ") || categoryName;
  const descriptionFilters = new Map<string, Filter>();
  for (const filter of result.filters) {
    const description = savedReviewFilterDescription(filter, filterSourceOrigin(filter, scanById));
    if (!descriptionFilters.has(description)) descriptionFilters.set(description, filter);
  }
  const filterName = (filter: Filter) => {
    const description = savedReviewFilterDescription(filter, filterSourceOrigin(filter, scanById)) || categoryName;
    return `${source === "audio" ? censorAudioWord(description) : description} at ${formatTime(filter.start_seconds)}`;
  };
  const severity = severityLevels.find((level) => item.pathFilters.some((filter) => filter.severity === level)) ?? "none";
  const editActions = [...new Set(result.filters.map((filter) => filter.edit_action))];
  const showScanSource = sourceScans.length > 0 || !result.filters.some(isCommunityFilter);
  const linkedIndicator = item.linked && <span className="linked-result-indicator" role="img" aria-label="Linked result repeated in another category"> 🔗</span>;
  const word = (description: string) => <>“<CensoredAudioWord word={description} context={descriptionFilters.get(description)?.details} />”</>;
  const previewed = preview && result.filters.find((filter) => filter.id === preview.filterId);

  return <Fragment>
    <tr className={`review-filter-row${rowSelected || rowMixed ? "" : " is-off"}`}>
      <td className="review-filter-use"><input type="checkbox" aria-label={`Include ${rowName} at ${time}`}
        aria-checked={rowMixed ? "mixed" : rowSelected} ref={(node) => { if (node) node.indeterminate = rowMixed; }}
        checked={rowSelected} disabled={busy || !filtersEditable || result.filters.every((filter) => requiredIds.has(filter.id))}
        onChange={() => onToggleFilters(result.filters.filter((filter) => !requiredIds.has(filter.id)).map((filter) => filter.id), !rowSelected)} /></td>
      <td className="review-filter-main">
        <div className="review-filter-line">
          <span className="review-filter-time">{time}–{formatTime(result.endSeconds)}</span>
          {result.descriptions.length ? <span className="merged-descriptions">{result.descriptions.map((description) => <span className="merged-description-item" key={description}>
            {source === "audio" ? word(description) : description}{linkedIndicator}</span>)}</span>
            : (categoryFallback || linkedIndicator) && <span className="merged-description-fallback">{categoryFallback && categoryName}{linkedIndicator}</span>}
          <span className={`review-chip review-chip-severity severity-${severity}`}>{severityText(item.pathFilters)}</span>
          {editActions.map((action) => <span className="review-chip review-chip-action" key={action}>{action}</span>)}
          {item.pathFilters.some((filter) => lockedSeverities[filter.id]) && <small>Locked by instance policy</small>}
        </div>
        <div className="review-filter-meta">
          {showScanSource && <span className="review-scan-source">
            {sourceScans.length ? `Scan · ${reviewScanSourceLabel(sourceScans)}` : reviewScanSourceLabel(sourceScans)}
            <ReviewScanInfo scans={sourceScans} /></span>}
          <CommunityFilterSource filters={result.filters} />
          <CommunityFilterFlags filters={result.filters} />
          {result.filters.some((filter) => requiredIds.has(filter.id)) && <small>Required by instance policy</small>}
        </div>
        {preview && previewed && (previewsAudio(previewed)
          ? <audio aria-label={`Mute preview for ${rowName} at ${time}`} controls src={preview.url}>Your browser does not support audio previews.</audio>
          : <video aria-label={`Video preview for ${rowName} at ${time}`} controls src={preview.url}>Your browser does not support video previews.</video>)}
      </td>
      <td className="review-filter-actions-cell">{result.filters.map((filter) => <div className="review-filter-row-actions" key={filter.id}>
        <button type="button" className="btn-secondary compact-button" aria-label={`Preview ${filterName(filter)}`}
          onClick={() => onPreviewFilter(filter)} disabled={busy || previewDisabled}><span aria-hidden="true">▶ </span>Preview</button>
        {renderFilterVote && isCommunityFilter(filter) && renderFilterVote(filter, filterName(filter))}
        <MoreMenu label={`More actions for ${filterName(filter)}`} items={[
          { key: "edit", label: "Edit", accessibleName: `Edit ${filterName(filter)}`,
            disabled: busy, onSelect: () => setEditingFilterId(() => filter.id) },
          { key: "delete", label: "Delete", accessibleName: `Delete ${filterName(filter)}`, danger: true,
            disabled: busy || !cuesEditable || requiredIds.has(filter.id),
            onSelect: () => { setEditingFilterId((current) => current === filter.id ? null : current); onDeleteFilter(filter.id); } },
        ]} />
      </div>)}</td>
    </tr>
    {editingFilter && <tr className="saved-review-filter-editor-row"><td colSpan={3}><fieldset className="review-fieldset" disabled={busy}>
      <ReviewFilterEditor filter={editingFilter} required={requiredIds.has(editingFilter.id)} severityLocked={Boolean(lockedSeverities[editingFilter.id])}
        filtersEditable={filtersEditable} cuesEditable={cuesEditable} metadataEditable={metadataEditable}
        onChange={(patch) => onChangeFilter(editingFilter.id, patch)} onDone={() => setEditingFilterId(() => null)} />
    </fieldset></td></tr>}
  </Fragment>;
}
