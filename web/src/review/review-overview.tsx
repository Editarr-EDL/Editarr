import { Fragment, useEffect, useState } from "react";
import { useMcfRegistry } from "../shared/mcf-registry";
import type { Scan } from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import { formatTime, scanLabel, scanOriginLabel } from "../shared/display";
import { McfClassificationFields, McfRegistryStatus } from "./review-mcf";
import { mergeReviewRanges, savedReviewTimelineRanges, CensoredAudioWord, censorAudioWord, savedReviewFilterDescription, isNoObjectionableContentFilter, reviewVideoCategories, reviewAudioRoot, mergeSavedReviewFilters, sortSavedReviewResults, buildReviewHierarchy, sortTieredReviewResults, sortTieredReviewGroups, uniqueTieredFilters, severityText, channelText, type SavedReviewSort, type ReviewTableSortKey, type ReviewTableSortDirection, type SavedReviewResult, type TieredReviewResult } from "./review-results";

/** Edit one review filter's classification and timing.
 * @param props Filter values, registry, and change callbacks.
 */
function ReviewFilterEditor({
  filter,
  onChange,
  onDone,
  required = false,
  severityLocked = false,
  filtersEditable,
  cuesEditable,
  metadataEditable,
}: {
  filter: SavedReview["filters"][number];
  onChange: (patch: Partial<SavedReview["filters"][number]>) => void;
  onDone: () => void;
  required?: boolean;
  severityLocked?: boolean;
  filtersEditable: boolean;
  cuesEditable: boolean;
  metadataEditable: boolean;
}) {
  const { registry, error: registryError } = useMcfRegistry();
  return <fieldset className="saved-review-filter">
    <legend>{filter.source === "audio" ? "Audio" : "Video"} filter</legend>
    <label><input type="checkbox" checked={filter.selected} disabled={required || !filtersEditable} onChange={(event) => onChange({ selected: event.target.checked })} /> Selected{required ? " - required by instance policy" : ""}</label>
    <fieldset className="review-fieldset" disabled={!metadataEditable}>
    {!filter.category && <p className="mcf-needs-classification">Needs classification</p>}
    {registry ? <McfClassificationFields controlId={`Filter ${filter.id}`} registry={registry} value={filter} onChange={onChange} allowClear severityLocked={severityLocked} /> : <McfRegistryStatus error={registryError} />}
    {filter.source === "audio" && !metadataEditable ? <>
      <div className="field" aria-label="Audio filter label"><span>Label</span><CensoredAudioWord word={filter.label || ""} context={filter.details} /></div>
      <div className="field" aria-label="Audio filter description"><span>Description</span><CensoredAudioWord word={filter.details || ""} /></div>
      <div className="field" aria-label="Audio filter group"><span>Group</span><CensoredAudioWord word={filter.group_key || ""} /></div>
    </> : <>
      <label className="field"><span>Label</span><input aria-label={`Filter ${filter.id} label`} value={filter.label || ""} onChange={(event) => onChange({ label: event.target.value || null })} /></label>
      <label className="field"><span>Description</span><textarea aria-label={`Filter ${filter.id} description`} value={filter.details || ""} onChange={(event) => onChange({ details: event.target.value || null })} rows={2} /></label>
      <label className="field"><span>Group</span><input aria-label={`Filter ${filter.id} group`} value={filter.group_key || ""} onChange={(event) => onChange({ group_key: event.target.value || null })} /></label>
    </>}
    </fieldset>
    <fieldset className="review-fieldset" disabled={!cuesEditable}>
    <div className="finding-timing-fields"><label className="field"><span>Start seconds</span><input aria-label={`Filter ${filter.id} start seconds`} type="number" min="0" step="0.1" value={filter.start_seconds} onChange={(event) => onChange({ start_seconds: Number(event.target.value) })} /></label><label className="field"><span>End seconds</span><input aria-label={`Filter ${filter.id} end seconds`} type="number" min="0" step="0.1" value={filter.end_seconds ?? ""} onChange={(event) => onChange({ end_seconds: event.target.value === "" ? null : Number(event.target.value) })} /></label></div>
    <label className="field"><span>EDL action</span><select aria-label={`Filter ${filter.id} action`} value={filter.edit_action} onChange={(event) => onChange({ edit_action: event.target.value as typeof filter.edit_action })}><option value="mute">Mute</option><option value="skip">Skip</option></select></label>
    </fieldset>
    <div className="button-row"><button type="button" className="btn-secondary compact-button" onClick={onDone}>Done</button></div>
  </fieldset>;
}

/** Render editable audio and video review results with stable category tiers. */
export function SavedReviewOverview({
  filters,
  memberships,
  scans,
  busy,
  onToggleFilters,
  onChangeFilter,
  onDeleteFilter,
  onApplyDefaults,
  onPreviewFilter,
  preview,
  previewDisabled,
  focusFilterId,
  onFocusFilterComplete,
  requiredFilterIds = [],
  lockedSeverities = {},
  filtersEditable = true,
  cuesEditable = true,
  metadataEditable = true,
}: {
  filters: SavedReview["filters"];
  memberships: SavedReview["memberships"];
  scans: Scan[];
  busy: boolean;
  onToggleFilters: (filterIds: string[], selected: boolean) => void;
  onChangeFilter: (filterId: string, patch: Partial<SavedReview["filters"][number]>) => void;
  onDeleteFilter: (filterId: string) => void;
  onApplyDefaults: () => void;
  onPreviewFilter: (filter: SavedReview["filters"][number]) => void;
  preview: { filterId: string; url: string } | null;
  previewDisabled: boolean;
  focusFilterId?: string | null;
  onFocusFilterComplete?: () => void;
  requiredFilterIds?: string[];
  lockedSeverities?: SavedReview["policy_locked_severities"];
  filtersEditable?: boolean;
  cuesEditable?: boolean;
  metadataEditable?: boolean;
}) {
  const { registry } = useMcfRegistry();
  const [sort] = useState<SavedReviewSort>("alphabetical");
  const [tableSorts, setTableSorts] = useState<Record<"visual" | "audio", { key: ReviewTableSortKey; direction: ReviewTableSortDirection }>>({
    visual: { key: "time", direction: "asc" },
    audio: { key: "time", direction: "asc" },
  });
  const [collapsedFamilies, setCollapsedFamilies] = useState<Set<string>>(new Set());
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [showNoObjectionableContent, setShowNoObjectionableContent] = useState(false);
  const [editingFilterId, setEditingFilterId] = useState<string | null>(null);
  const scanById = new Map(scans.map((scan) => [scan.id, scan]));
  const videoResults = sortSavedReviewResults(mergeSavedReviewFilters(filters.filter((filter) => filter.source === "visual"), scanById), sort);
  const audioResults = sortSavedReviewResults(mergeSavedReviewFilters(filters.filter((filter) => filter.source === "audio"), scanById), sort);
  const visibleVideoResults = showNoObjectionableContent
    ? videoResults
    : videoResults.filter((result) => !result.filters.every(isNoObjectionableContentFilter));
  const visibleResults = [...visibleVideoResults, ...audioResults];
  useEffect(() => {
    if (!focusFilterId) return;
    const filter = filters.find((candidate) => candidate.id === focusFilterId);
    if (!filter) return;
    const source = filter.source === "visual" ? "visual" : "audio";
    const group = filter.source === "visual"
      ? reviewVideoCategories(filter, showNoObjectionableContent)[0]
      : reviewAudioRoot(filter.label, filter.subcategory || filter.category || "audio");
    const family = filter.category || "";
    const subcategory = filter.subcategory || "";
    const familyKey = `${source}:family:${family}`;
    setCollapsedFamilies((current) => { const next = new Set(current); next.delete(familyKey); return next; });
    setExpandedGroups((current) => new Set([
      ...current,
      `${source}:subcategory:${family}:${subcategory}`,
      `${source}:group:${family}:${subcategory}:${group}`,
    ]));
    setEditingFilterId(focusFilterId);
    requestAnimationFrame(() => {
      Array.from(document.querySelectorAll<HTMLElement>("[aria-label]"))
        .find((element) => element.getAttribute("aria-label")?.startsWith(`Filter ${focusFilterId} `))?.focus();
      onFocusFilterComplete?.();
    });
  }, [filters, focusFilterId, onFocusFilterComplete, registry, showNoObjectionableContent]);
  const selectedResultCount = visibleResults.filter((result) => result.filters.every((filter) => filter.selected)).length;
  const skipped = mergeReviewRanges(filters
    .filter((filter) => filter.selected && filter.edit_action === "skip")
    .map((filter) => [filter.start_seconds, Math.max(filter.start_seconds, filter.end_seconds ?? filter.start_seconds + 1)]))
    .reduce((total, [start, end]) => total + end - start, 0);
  const timelineEnd = Math.max(1, ...scans.map((scan) => scan.duration_seconds || 0), ...filters.map((filter) => filter.end_seconds ?? filter.start_seconds + 1));
  const timelineRanges = savedReviewTimelineRanges(filters);

  const renderTieredSection = (title: "Video" | "Audio", results: SavedReviewResult[], source: "visual" | "audio") => {
    const hierarchy = buildReviewHierarchy(results, source, registry, showNoObjectionableContent);
    const tableSort = tableSorts[source];
    const hasVideoPreview = source === "visual" && results.some((result) => result.filters.some((filter) => preview?.filterId === filter.id));
    const toggleSort = (key: ReviewTableSortKey) => setTableSorts((current) => ({
      ...current,
      [source]: current[source].key === key
        ? { key, direction: current[source].direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    }));
    const sortHeader = (key: ReviewTableSortKey, label: string) => <th aria-sort={tableSort.key === key ? tableSort.direction === "asc" ? "ascending" : "descending" : "none"}><button type="button" className="sort-button" onClick={() => toggleSort(key)}>{label}{tableSort.key === key && <span aria-hidden="true"> {tableSort.direction === "asc" ? "▲" : "▼"}</span>}</button></th>;
    const hierarchyHeader = (
      level: "family" | "subcategory" | "group",
      name: string,
      key: string,
      items: TieredReviewResult[],
      open: boolean,
      onToggle: () => void,
    ) => {
      const nodeFilters = uniqueTieredFilters(items);
      const selectedCount = items.filter((item) => item.result.filters.every((filter) => filter.selected)).length;
      const selected = nodeFilters.every((filter) => filter.selected);
      const mixed = !selected && nodeFilters.some((filter) => filter.selected);
      const displayName = source === "audio" && level === "group" ? censorAudioWord(name) : name;
      const levelLabel = level === "group" ? "finding group" : level;
      return <tr className={`combined-result-hierarchy-header hierarchy-${level}`} key={key}><th className="combined-result-group-use"><input type="checkbox" aria-label={`Use all ${title.toLowerCase()} filters in ${name} ${levelLabel}`} aria-checked={mixed ? "mixed" : selected} ref={(node) => { if (node) node.indeterminate = mixed; }} checked={selected} disabled={busy || !filtersEditable || nodeFilters.every(filter => requiredFilterIds.includes(filter.id))} onChange={() => onToggleFilters(nodeFilters.filter(filter => !requiredFilterIds.includes(filter.id)).map((filter) => filter.id), !selected)} /></th><th colSpan={6}><button type="button" onClick={onToggle} aria-expanded={open} aria-label={`${name} ${levelLabel}, ${items.length} ${items.length === 1 ? "result" : "results"}, ${selectedCount} selected`}><span>{displayName}</span><small>{items.length} {items.length === 1 ? "result" : "results"} · {selectedCount} selected</small></button></th></tr>;
    };
    return <section className={`finding-group combined-${source}-findings`} aria-label={`${title} review filters`}><div className="section-heading"><div><h3>{title}</h3><p>{results.length} {results.length === 1 ? "result" : "results"} in {hierarchy.length} {hierarchy.length === 1 ? "family" : "families"}</p></div></div><div className="library-table-wrap tiered-review-table-wrap" role="region" tabIndex={0} aria-label={`${title} review filters table`}><table className={`scan-filter-table combined-scan-filter-table tiered-review-table${hasVideoPreview ? " has-video-preview" : ""}`}><thead><tr><th>Use</th>{sortHeader("time", "Time")}{sortHeader("description", "Description")}{sortHeader("severity", "Severity")}<th>Channel</th><th>Scan</th><th><span className="visually-hidden">Filter controls</span></th></tr></thead>{hierarchy.map((family) => {
      const familyItems = family.subcategories.flatMap((subcategory) => subcategory.groups.flatMap((group) => group.results));
      const familyKey = `${source}:family:${family.id}`;
      const familyOpen = !collapsedFamilies.has(familyKey);
      return <tbody className="combined-result-family" key={familyKey}>{hierarchyHeader("family", family.name, familyKey, familyItems, familyOpen, () => setCollapsedFamilies((current) => { const next = new Set(current); if (familyOpen) next.add(familyKey); else next.delete(familyKey); return next; }))}{familyOpen && family.subcategories.map((subcategory) => {
        const subcategoryItems = subcategory.groups.flatMap((group) => group.results);
        const subcategoryKey = `${source}:subcategory:${family.id}:${subcategory.id}`;
        const subcategoryOpen = expandedGroups.has(subcategoryKey);
        return <Fragment key={subcategoryKey}>{hierarchyHeader("subcategory", subcategory.name, subcategoryKey, subcategoryItems, subcategoryOpen, () => setExpandedGroups((current) => { const next = new Set(current); if (subcategoryOpen) next.delete(subcategoryKey); else next.add(subcategoryKey); return next; }))}{subcategoryOpen && sortTieredReviewGroups(subcategory.groups, tableSort).map((group) => {
          const groupKey = `${source}:group:${family.id}:${subcategory.id}:${group.name}`;
          const groupOpen = expandedGroups.has(groupKey);
          return <Fragment key={groupKey}>{hierarchyHeader("group", group.name, groupKey, group.results, groupOpen, () => setExpandedGroups((current) => { const next = new Set(current); if (groupOpen) next.delete(groupKey); else next.add(groupKey); return next; }))}{groupOpen && sortTieredReviewResults(group.results, tableSort).map((item) => {
            const result = item.result;
            const rowSelected = result.filters.every((filter) => filter.selected);
            const rowMixed = !rowSelected && result.filters.some((filter) => filter.selected);
            const editingFilter = result.filters.find((filter) => filter.id === editingFilterId);
            const rowName = result.descriptions.join("; ") || group.name;
            const linkedIndicator = item.linked && <span className="linked-result-indicator" role="img" aria-label="Linked result repeated in another category"> 🔗</span>;
            return <Fragment key={`${groupKey}:${result.key}`}>
              <tr><td><input type="checkbox" aria-label={`Include ${rowName} at ${formatTime(result.startSeconds)}`} aria-checked={rowMixed ? "mixed" : rowSelected} ref={node => { if (node) node.indeterminate = rowMixed; }} checked={rowSelected} disabled={busy || !filtersEditable || result.filters.every(filter => requiredFilterIds.includes(filter.id))} onChange={() => onToggleFilters(result.filters.filter(filter => !requiredFilterIds.includes(filter.id)).map(filter => filter.id), !rowSelected)} /></td>
                <td>{formatTime(result.startSeconds)}–{formatTime(result.endSeconds)}</td>
                <td>{result.descriptions.length ? <span className="merged-descriptions">{result.descriptions.map(description => { const filter = result.filters.find(candidate => savedReviewFilterDescription(candidate) === description); return <span className="merged-description-item" key={description}>{source === "audio" ? <CensoredAudioWord word={description} context={filter?.details} /> : description}{linkedIndicator}</span>; })}</span> : linkedIndicator}</td>
                <td>{severityText(item.pathFilters)}{item.pathFilters.some(filter => lockedSeverities[filter.id]) && <small>Locked by instance policy</small>}</td><td>{channelText(item.pathFilters)}</td>
                <td className="finding-scan-attribution">{result.scanIds.length ? result.scanIds.map(scanId => { const scan = scanById.get(scanId); return scan ? `${scanLabel(scan)} · ${scanOriginLabel(scan)}` : null; }).filter(Boolean).join(", ") : "Source unavailable"}</td>
                <td><span className="finding-preview"><span className="saved-review-filter-actions">{result.filters.map(filter => <span className="saved-review-filter-action" key={filter.id}>
                  {requiredFilterIds.includes(filter.id) && <small>Required by instance policy</small>}
                  <button type="button" className="btn-secondary compact-button" aria-label={`Preview filter ${filter.id}`} onClick={() => onPreviewFilter(filter)} disabled={busy || previewDisabled}>Preview</button>
                  <button type="button" className="btn-secondary compact-button" aria-label={`Edit filter ${filter.id}`} aria-expanded={editingFilterId === filter.id} disabled={busy} onClick={() => setEditingFilterId(current => current === filter.id ? null : filter.id)}>Edit</button>
                  <button type="button" className="danger-button compact-button" aria-label={`Delete filter ${filter.id}`} disabled={busy || !cuesEditable || requiredFilterIds.includes(filter.id)} onClick={() => { if (editingFilterId === filter.id) setEditingFilterId(null); onDeleteFilter(filter.id); }}>Delete</button>
                </span>)}</span>{preview && result.filters.some(filter => filter.id === preview.filterId) && (source === "audio" ? <audio aria-label={`Mute preview for ${rowName} at ${formatTime(result.startSeconds)}`} controls src={preview.url}>Your browser does not support audio previews.</audio> : <video aria-label={`Video preview for ${rowName} at ${formatTime(result.startSeconds)}`} controls src={preview.url}>Your browser does not support video previews.</video>)}</span></td>
              </tr>
              {editingFilter && <tr className="saved-review-filter-editor-row"><td colSpan={7}><fieldset className="review-fieldset" disabled={busy}><ReviewFilterEditor filter={editingFilter} required={requiredFilterIds.includes(editingFilter.id)} severityLocked={Boolean(lockedSeverities[editingFilter.id])} filtersEditable={filtersEditable} cuesEditable={cuesEditable} metadataEditable={metadataEditable} onChange={patch => onChangeFilter(editingFilter.id, patch)} onDone={() => setEditingFilterId(null)} /></fieldset></td></tr>}
            </Fragment>;
          })}</Fragment>;
        })}</Fragment>;
      })}</tbody>;
    })}</table>{!results.length && <p className="muted-copy">This review has no {title.toLowerCase()} filters.</p>}</div></section>;
  };

  return <div className="scan-review saved-review-overview"><div className="scan-review-heading"><div><h3>Review results</h3><p>Combined results from {memberships.length} selected {memberships.length === 1 ? "scan" : "scans"}.</p></div><div className="scan-review-summary"><strong>{selectedResultCount}/{visibleResults.length} results</strong><span>Skipped Time {formatTime(skipped)}</span></div></div><div className="scan-timeline" role="img" aria-label={`${selectedResultCount} selected results across ${memberships.length} scans`}>{timelineRanges.map((range, index) => { const start = (range.start / timelineEnd) * 100; const end = (range.end / timelineEnd) * 100; const state = range.selected ? "selected" : "unselected"; return <span className={`timeline-filter ${state} ${range.action}`} key={`${state}:${range.action}:${range.start}:${range.end}:${index}`} role="img" aria-label={`${state} ${range.action} from ${formatTime(range.start)} to ${formatTime(range.end)}`} style={{ left: `${start}%`, width: `${Math.max(0.8, end - start)}%` }} />; })}</div><div className="scan-review-sort-row"><button type="button" className="btn-secondary compact-button" onClick={() => setShowNoObjectionableContent((value) => !value)} aria-pressed={showNoObjectionableContent}>{showNoObjectionableContent ? "Hide No Objectionable Content" : "Show No Objectionable Content"}</button><button type="button" className="btn-secondary compact-button" onClick={onApplyDefaults} disabled={busy || !filters.length}>Apply Filter Defaults</button></div><div className="finding-groups">{renderTieredSection("Video", visibleVideoResults, "visual")}{renderTieredSection("Audio", audioResults, "audio")}</div></div>;
}
