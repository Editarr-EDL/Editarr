import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useMcfRegistry } from "../shared/mcf-registry";
import type { Scan } from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import { formatTime } from "../shared/display";
import { communityResultCount } from "./review-community-badges";
import { ReviewFilterRow, type ReviewFilterRowContext } from "./review-filter-row";
import { mergeReviewRanges, savedReviewTimelineRanges, censorAudioWord, filterSourceOrigin, isNoObjectionableContentFilter, reviewVideoCategories, reviewAudioRoot, mergeSavedReviewFilters, sortSavedReviewResults, buildReviewHierarchy, sortTieredReviewResults, sortTieredReviewGroups, uniqueTieredFilters, type ReviewTableSortKey, type ReviewTableSortDirection, type SavedReviewResult, type TieredReviewResult } from "./review-results";

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
  defaultSort = "category",
  renderFilterVote,
  sources,
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
  defaultSort?: "time" | "category";
  /** Renders a community filter's vote control beside its Preview; `name` describes the filter. */
  renderFilterVote?: (filter: SavedReview["filters"][number], name: string) => ReactNode;
  /** The Filters toolbar's included-sources control, shown after its heading. */
  sources?: ReactNode;
}) {
  const { registry } = useMcfRegistry();
  const [sort, setSort] = useState(defaultSort);
  const [show, setShow] = useState<"all" | "audio" | "visual">("all");
  const resultsRef = useRef<HTMLDivElement>(null);
  const scrollToResultsStart = () => {
    if (resultsRef.current) resultsRef.current.scrollTop = 0;
  };
  const [tableSorts, setTableSorts] = useState<Record<"visual" | "audio", { key: ReviewTableSortKey; direction: ReviewTableSortDirection }>>({
    visual: { key: "time", direction: "asc" },
    audio: { key: "time", direction: "asc" },
  });
  const [collapsedFamilies, setCollapsedFamilies] = useState<Set<string>>(new Set());
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [showNoObjectionableContent, setShowNoObjectionableContent] = useState(false);
  const [editingFilterId, setEditingFilterId] = useState<string | null>(null);
  const scanById = new Map(scans.map((scan) => [scan.id, scan]));
  const requiredIds = new Set(requiredFilterIds);
  const videoResults = sortSavedReviewResults(mergeSavedReviewFilters(filters.filter((filter) => filter.source === "visual"), scanById), "alphabetical");
  const audioResults = sortSavedReviewResults(mergeSavedReviewFilters(filters.filter((filter) => filter.source === "audio"), scanById), "alphabetical");
  const visibleVideoResults = showNoObjectionableContent
    ? videoResults
    : videoResults.filter((result) => !result.filters.every((filter) => isNoObjectionableContentFilter(filter, filterSourceOrigin(filter, scanById))));
  const visibleResults = [...visibleVideoResults, ...audioResults];
  const showVideo = show !== "audio";
  const showAudio = show !== "visual";
  useEffect(() => {
    if (!focusFilterId) return;
    const filter = filters.find((candidate) => candidate.id === focusFilterId);
    if (!filter) return;
    const source = filter.source === "visual" ? "visual" : "audio";
    setShow("all");
    if (isNoObjectionableContentFilter(filter)) setShowNoObjectionableContent(true);
    const group = filter.source === "visual"
      ? reviewVideoCategories(filter, true)[0]
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
  }, [filters, focusFilterId, onFocusFilterComplete, registry]);
  const timelineFilters = filters;
  const selectedResultCount = visibleResults.filter((result) => result.filters.every((filter) => filter.selected)).length;
  const resultCount = visibleResults.length;
  const communityCount = communityResultCount(filters);
  const skipped = mergeReviewRanges(timelineFilters
    .filter((filter) => filter.selected && filter.edit_action === "skip")
    .map((filter) => [filter.start_seconds, Math.max(filter.start_seconds, filter.end_seconds ?? filter.start_seconds + 1)]))
    .reduce((total, [start, end]) => total + end - start, 0);
  const timelineEnd = Math.max(1, ...scans.map((scan) => scan.duration_seconds || 0), ...timelineFilters.map((filter) => filter.end_seconds ?? filter.start_seconds + 1));
  const timelineRanges = savedReviewTimelineRanges(timelineFilters);

  const rowContext: ReviewFilterRowContext = {
    scanById, requiredIds, lockedSeverities, busy, filtersEditable, cuesEditable, metadataEditable, previewDisabled, preview,
    categoryFallback: sort === "time", editingFilterId, setEditingFilterId, onToggleFilters, onChangeFilter,
    onDeleteFilter: (filterId) => {
      onDeleteFilter(filterId);
      // The deleted row's menu button goes with it, so focus stays in the filter list.
      requestAnimationFrame(() => { if (!resultsRef.current?.contains(document.activeElement)) resultsRef.current?.focus(); });
    },
    onPreviewFilter, renderFilterVote,
  };
  const renderResult = (item: TieredReviewResult, source: "audio" | "visual", key: string, categoryName: string) =>
    <ReviewFilterRow key={`${key}:${item.result.key}`} item={item} source={source} categoryName={categoryName} context={rowContext} />;
  const tableHead = <thead><tr><th className="review-filter-use">Use</th><th>Filter</th><th className="review-filter-actions-cell">Actions</th></tr></thead>;

  const renderTieredSection = (title: "Video" | "Audio", results: SavedReviewResult[], source: "visual" | "audio") => {
    const hierarchy = buildReviewHierarchy(results, source, registry, showNoObjectionableContent);
    const tableSort = tableSorts[source];
    const toggleSort = (key: ReviewTableSortKey) => setTableSorts((current) => ({
      ...current,
      [source]: current[source].key === key
        ? { key, direction: current[source].direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    }));
    const sortButton = (key: ReviewTableSortKey, label: string) => {
      const active = tableSort.key === key;
      const direction = tableSort.direction === "asc" ? "ascending" : "descending";
      return <button type="button" className="sort-button" aria-pressed={active} aria-label={active ? `${label}, ${direction}` : undefined}
        onClick={() => toggleSort(key)}>{label}{active && <span aria-hidden="true"> {tableSort.direction === "asc" ? "▲" : "▼"}</span>}</button>;
    };
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
      return <tr className={`combined-result-hierarchy-header hierarchy-${level}`} key={key}><th className="combined-result-group-use"><input type="checkbox" aria-label={`Use all ${title.toLowerCase()} filters in ${name} ${levelLabel}`} aria-checked={mixed ? "mixed" : selected} ref={(node) => { if (node) node.indeterminate = mixed; }} checked={selected} disabled={busy || !filtersEditable || nodeFilters.every(filter => requiredIds.has(filter.id))} onChange={() => onToggleFilters(nodeFilters.filter(filter => !requiredIds.has(filter.id)).map((filter) => filter.id), !selected)} /></th><th colSpan={2}><button type="button" onClick={onToggle} aria-expanded={open} aria-label={`${name} ${levelLabel}, ${items.length} ${items.length === 1 ? "result" : "results"}, ${selectedCount} selected`}><span>{displayName}</span><small>{items.length} {items.length === 1 ? "result" : "results"} · {selectedCount} selected</small></button></th></tr>;
    };
    return <section className={`finding-group combined-${source}-findings`} aria-label={`${title} review filters`}><div className="section-heading"><div><h3>{title}</h3><p>{results.length} {results.length === 1 ? "result" : "results"} in {hierarchy.length} {hierarchy.length === 1 ? "family" : "families"}</p></div>
      <div className="review-table-sort" role="group" aria-label={`Order ${title.toLowerCase()} filters`}><span aria-hidden="true">Order</span>{sortButton("time", "Time")}{sortButton("description", "Description")}{sortButton("severity", "Severity")}</div></div>
      <div className="review-filter-table-wrap"><table className="review-filter-table" aria-label={`${title} review filters table`}>{tableHead}{hierarchy.map((family) => {
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
            return renderResult(item, source, groupKey, group.name);
          })}</Fragment>;
        })}</Fragment>;
      })}</tbody>;
    })}</table>{!results.length && <p className="muted-copy">This review has no {title.toLowerCase()} filters.</p>}</div></section>;
  };

  const chronological = sort === "time" ? sortSavedReviewResults([
    ...(showVideo ? visibleVideoResults : []), ...(showAudio ? audioResults : []),
  ], "chronological") : [];

  return <div className="scan-review saved-review-overview">
    <section className="review-timeline-summary" aria-label="Timeline summary">
      <div className="scan-review-heading">
        <div>
          <h3>Timeline summary</h3>
          <p>Combined results from {memberships.length} selected {memberships.length === 1 ? "scan" : "scans"}{communityCount > 0 && ` and ${communityCount} community ${communityCount === 1 ? "filter" : "filters"}`}.</p>
        </div>
        <div className="scan-review-summary">
          <strong>{selectedResultCount}/{resultCount} results</strong>
          <span>Skipped Time {formatTime(skipped)}</span>
        </div>
      </div>
      <div className="scan-timeline" role="img" aria-label={`${selectedResultCount} selected results across ${memberships.length} scans`}>
        {timelineRanges.map((range, index) => {
          const start = (range.start / timelineEnd) * 100;
          const end = (range.end / timelineEnd) * 100;
          const state = range.selected ? "selected" : "unselected";
          return <span className={`timeline-filter ${state} ${range.action}`} key={`${state}:${range.action}:${range.start}:${range.end}:${index}`}
            role="img" aria-label={`${state} ${range.action} from ${formatTime(range.start)} to ${formatTime(range.end)}`}
            style={{ left: `${start}%`, width: `${Math.max(0.8, end - start)}%` }} />;
        })}
      </div>
    </section>
    <section className="review-filter-list" aria-labelledby="review-filters-heading">
      <div className="review-filter-toolbar">
        <h3 id="review-filters-heading">Filters</h3>
        {sources}
        <div className="review-filter-controls">
          <label>Show <select aria-label="Show" value={show} onChange={(event) => { setShow(event.target.value as typeof show); scrollToResultsStart(); }}>
            <option value="all">All Filters</option>
            <option value="audio">Audio</option>
            <option value="visual">Video</option>
          </select></label>
          <label>Group by <select aria-label="Group by" value={sort} onChange={(event) => { setSort(event.target.value as typeof sort); scrollToResultsStart(); }}>
            <option value="time">Time</option>
            <option value="category">Category</option>
          </select></label>
        </div>
      </div>
      <div className="saved-review-filter-results finding-groups" ref={resultsRef} role="region" aria-label="Filter results" tabIndex={0}>
        {sort === "category" ? <>
          {showVideo && renderTieredSection("Video", visibleVideoResults, "visual")}
          {showAudio && renderTieredSection("Audio", audioResults, "audio")}
        </> : chronological.length ? <div className="review-filter-table-wrap">
          <table className="review-filter-table" aria-label="Review filters by time">
            {tableHead}
            <tbody>{chronological.map((result) => {
              const filter = result.filters[0];
              const category = filter.source === "visual" ? reviewVideoCategories(filter, true).join(", ") : "Audio";
              return renderResult({ result, pathFilters: result.filters, linked: false }, filter.source, filter.source, category);
            })}</tbody>
          </table>
        </div> : <p className="muted-copy">No filters in this view.</p>}
      </div>
      <div className="button-row review-filter-actions">
        <button type="button" className="btn-secondary compact-button" onClick={() => setShowNoObjectionableContent((value) => !value)} aria-pressed={showNoObjectionableContent}>
          {showNoObjectionableContent ? "Hide No Objectionable Content" : "Show No Objectionable Content"}
        </button>
        <button type="button" className="btn-secondary compact-button" onClick={onApplyDefaults} disabled={busy || !filters.length}>Apply Filter Defaults</button>
      </div>
    </section>
  </div>;
}
