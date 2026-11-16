import { useEffect, useRef, useState } from "react";
import type { McfCategoryFamily, McfRegistry } from "../api/api-mcf-types";
import type { Scan } from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import { categoryLabel, familyLabel } from "./review-mcf";

export type SavedReviewSort = "alphabetical" | "chronological";
export type ReviewTableSortKey = "time" | "description" | "severity";
export type ReviewTableSortDirection = "asc" | "desc";

export interface SavedReviewResult {
  key: string;
  filters: SavedReview["filters"];
  startSeconds: number;
  endSeconds: number;
  descriptions: string[];
  scanIds: string[];
}

export interface TieredReviewResult {
  result: SavedReviewResult;
  pathFilters: SavedReview["filters"];
  linked: boolean;
}

export interface TieredReviewGroup {
  name: string;
  results: TieredReviewResult[];
}

export interface TieredReviewSubcategory {
  id: string;
  name: string;
  groups: TieredReviewGroup[];
}

export interface TieredReviewFamily {
  id: string;
  name: string;
  subcategories: TieredReviewSubcategory[];
}

interface SavedReviewTimelineRange {
  start: number;
  end: number;
  action: "mute" | "skip";
  selected: boolean;
}

/** Merge overlapping second-based ranges after sorting by start time. */
export function mergeReviewRanges(ranges: Array<[number, number]>): Array<[number, number]> {
  const merged: Array<[number, number]> = [];
  for (const [start, end] of [...ranges].sort(([left], [right]) => left - right)) {
    const current = merged[merged.length - 1];
    if (current && start <= current[1]) current[1] = Math.max(current[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

/** Remove exclusion intervals from each input range. */
function subtractReviewRanges(
  ranges: Array<[number, number]>,
  exclusions: Array<[number, number]>,
): Array<[number, number]> {
  return ranges.flatMap(([start, end]) => {
    let pieces: Array<[number, number]> = [[start, end]];
    for (const [excludedStart, excludedEnd] of exclusions) {
      pieces = pieces.flatMap(([pieceStart, pieceEnd]) => {
        if (excludedEnd <= pieceStart || excludedStart >= pieceEnd) return [[pieceStart, pieceEnd]];
        const remaining: Array<[number, number]> = [];
        if (pieceStart < excludedStart) remaining.push([pieceStart, excludedStart]);
        if (excludedEnd < pieceEnd) remaining.push([excludedEnd, pieceEnd]);
        return remaining;
      });
    }
    return pieces;
  });
}

/** Build selected and unselected skip/mute ranges for the review timeline. */
export function savedReviewTimelineRanges(filters: SavedReview["filters"]): SavedReviewTimelineRange[] {
  return [true, false].flatMap((selected) => {
    const applicable = filters.filter((filter) => filter.selected === selected);
    const skip = mergeReviewRanges(applicable
      .filter((filter) => filter.edit_action === "skip")
      .map((filter) => [filter.start_seconds, Math.max(filter.start_seconds, filter.end_seconds ?? filter.start_seconds + 1)]));
    const mute = mergeReviewRanges(subtractReviewRanges(applicable
      .filter((filter) => filter.edit_action === "mute")
      .map((filter) => [filter.start_seconds, Math.max(filter.start_seconds, filter.end_seconds ?? filter.start_seconds + 1)]), skip));
    return [
      ...skip.map(([start, end]) => ({ start, end, action: "skip" as const, selected })),
      ...mute.map(([start, end]) => ({ start, end, action: "mute" as const, selected })),
    ];
  });
}

export function censorAudioWord(value: string): string {
  return value.replace(/[aeiou]/gi, "*");
}

/** Normalize letters and digits while retaining offsets into the original text. */
function normalizedMatchParts(value: string): { normalized: string; indexMap: number[] } {
  let normalized = "";
  const indexMap: number[] = [];
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (/[\p{L}\p{N}]/u.test(character)) {
      normalized += character.toLocaleLowerCase();
      indexMap.push(index);
    }
  }
  return { normalized, indexMap };
}

/** Highlight the matched word inside the optional spoken context. */
function tooltipWithHighlightedWord(word: string, context?: string | null) {
  const phrase = (context || "").trim();
  if (!phrase) return <strong>{word}</strong>;

  const loweredPhrase = phrase.toLocaleLowerCase();
  const loweredWord = word.toLocaleLowerCase();
  const matchIndex = loweredPhrase.indexOf(loweredWord);
  if (matchIndex >= 0) {
    const matchEnd = matchIndex + word.length;
    return <>{phrase.slice(0, matchIndex)}<strong>{phrase.slice(matchIndex, matchEnd)}</strong>{phrase.slice(matchEnd)}</>;
  }

  const normalizedPhrase = normalizedMatchParts(phrase);
  const normalizedWord = normalizedMatchParts(word).normalized;
  if (!normalizedWord) return <>{phrase}</>;
  const normalizedMatchIndex = normalizedPhrase.normalized.indexOf(normalizedWord);
  if (normalizedMatchIndex < 0) return <>{phrase}</>;
  const startIndex = normalizedPhrase.indexMap[normalizedMatchIndex];
  const endIndex = normalizedPhrase.indexMap[normalizedMatchIndex + normalizedWord.length - 1] + 1;
  return <>{phrase.slice(0, startIndex)}<strong>{phrase.slice(startIndex, endIndex)}</strong>{phrase.slice(endIndex)}</>;
}

/** Reveal the spoken word on focus or delayed hover while masking its inline label. */
export function CensoredAudioWord({ word, context }: { word: string; context?: string | null }) {
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const hoverTimer = useRef<number | null>(null);
  const clearHoverTimer = () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };
  const showAfterHoverDelay = () => {
    clearHoverTimer();
    hoverTimer.current = window.setTimeout(() => setTooltipVisible(true), 2_000);
  };
  useEffect(() => clearHoverTimer, []);
  return <span className="censored-audio-word" tabIndex={0} onMouseEnter={showAfterHoverDelay} onMouseLeave={() => { clearHoverTimer(); setTooltipVisible(false); }} onFocus={() => setTooltipVisible(true)} onBlur={() => setTooltipVisible(false)}><strong>{censorAudioWord(word)}</strong>{tooltipVisible && <span className="audio-word-tooltip" role="tooltip">{tooltipWithHighlightedWord(word, context)}</span>}</span>;
}

export function savedReviewFilterDescription(filter: SavedReview["filters"][number]): string {
  return filter.source === "visual"
    ? filter.details || filter.label || filter.subcategory || filter.category || "Unclassified"
    : filter.label || filter.details || filter.subcategory || filter.category || "Unclassified";
}

function isNoObjectionableContent(value: string | null | undefined): boolean {
  const normalized = value?.trim().toLocaleLowerCase().replace(/[.!]+$/, "");
  return normalized === "no objectionable content" || normalized === "no objectionable content detected";
}

export function isNoObjectionableContentFilter(filter: SavedReview["filters"][number]): boolean {
  return isNoObjectionableContent(savedReviewFilterDescription(filter))
    || isNoObjectionableContent(filter.category);
}

/** Split a video filter group into its visible category labels. */
export function reviewVideoCategories(
  filter: SavedReview["filters"][number],
  includeNoObjectionableContent = false,
): string[] {
  const categories = [...new Set((filter.group_key || filter.subcategory || filter.category || "Unclassified")
    .split("|")
    .map((category) => category.trim())
    .filter((category) => category && (
      includeNoObjectionableContent || !isNoObjectionableContent(category)
    )))];
  return categories.length ? categories : [filter.subcategory || filter.category || "Unclassified"];
}

/** Choose a stable grouping key for an audio word or fallback label. */
export function reviewAudioRoot(label: string | null, fallback: string): string {
  const normalized = (label || fallback).trim().toLocaleLowerCase().replace(/[^a-z0-9]/g, "");
  if (!normalized || normalized === "segment") return normalized || "audio";
  for (const suffix of ["ingly", "ing", "ers", "ed", "es", "s"]) {
    if (suffix === "s" && /(?:us|ss|is)$/.test(normalized)) continue;
    if (normalized.endsWith(suffix) && normalized.length - suffix.length >= 3) return normalized.slice(0, -suffix.length);
  }
  return normalized;
}

/** Merge equivalent review filters across scans into display results. */
export function mergeSavedReviewFilters(
  filters: SavedReview["filters"],
  scanById: Map<string, Scan>,
): SavedReviewResult[] {
  const merged: SavedReviewResult[] = [];
  for (const filter of [...filters].sort((left, right) => left.start_seconds - right.start_seconds)) {
    const startSeconds = filter.start_seconds;
    const endSeconds = Math.max(startSeconds, filter.end_seconds ?? startSeconds + 1);
    const current = merged[merged.length - 1];
    if (!current || startSeconds > current.endSeconds) {
      merged.push({
        key: filter.id,
        filters: [filter],
        startSeconds,
        endSeconds,
        descriptions: [savedReviewFilterDescription(filter)],
        scanIds: scanById.has(filter.source_scan_id) ? [filter.source_scan_id] : [],
      });
    } else {
      current.filters.push(filter);
      current.endSeconds = Math.max(current.endSeconds, endSeconds);
      current.key = `${current.key}:${filter.id}`;
      current.descriptions = [...new Set([...current.descriptions, savedReviewFilterDescription(filter)])];
      if (scanById.has(filter.source_scan_id)) {
        current.scanIds = [...new Set([...current.scanIds, filter.source_scan_id])].sort((left, right) => (
          (scanById.get(left)?.scan_id ?? 0) - (scanById.get(right)?.scan_id ?? 0)
        ));
      }
    }
  }
  return merged;
}

/** Order display results by the selected review sort mode. */
export function sortSavedReviewResults(results: SavedReviewResult[], sort: SavedReviewSort): SavedReviewResult[] {
  return [...results].sort((left, right) => sort === "chronological"
    ? left.startSeconds - right.startSeconds || left.endSeconds - right.endSeconds
    : left.descriptions.join(" ").localeCompare(right.descriptions.join(" ")) || left.startSeconds - right.startSeconds);
}

/** Group display results by stable family and subcategory tokens for one source. */
export function buildReviewHierarchy(
  results: SavedReviewResult[],
  source: "visual" | "audio",
  registry: McfRegistry | null,
  includeNoObjectionableContent: boolean,
): TieredReviewFamily[] {
  const families = new Map<McfCategoryFamily | "", Map<string, Map<string, TieredReviewResult[]>>>();
  for (const result of results) {
    const paths = new Map<string, { family: McfCategoryFamily | ""; subcategory: string; group: string; filters: SavedReview["filters"] }>();
    for (const filter of result.filters) {
      const family: McfCategoryFamily | "" = filter.category || "";
      const subcategory = filter.subcategory || "";
      const groups = source === "visual"
        ? reviewVideoCategories(filter, includeNoObjectionableContent)
        : [reviewAudioRoot(filter.label, filter.subcategory || filter.category || "audio")];
      for (const group of groups) {
        const pathKey = JSON.stringify([family, subcategory, group]);
        const path = paths.get(pathKey);
        if (path) path.filters.push(filter);
        else paths.set(pathKey, { family, subcategory, group, filters: [filter] });
      }
    }
    for (const path of paths.values()) {
      const subcategories = families.get(path.family) || new Map<string, Map<string, TieredReviewResult[]>>();
      families.set(path.family, subcategories);
      const groups = subcategories.get(path.subcategory) || new Map<string, TieredReviewResult[]>();
      subcategories.set(path.subcategory, groups);
      groups.set(path.group, [...(groups.get(path.group) || []), {
        result,
        pathFilters: path.filters,
        linked: paths.size > 1,
      }]);
    }
  }
  return [...families.entries()]
    .sort(([left], [right]) => familyLabel(registry, left || null).localeCompare(familyLabel(registry, right || null)))
    .map(([id, subcategories]) => ({
      id,
      name: familyLabel(registry, id || null),
      subcategories: [...subcategories.entries()]
        .sort(([left], [right]) => categoryLabel(registry, left || null).localeCompare(categoryLabel(registry, right || null)))
        .map(([subcategoryId, groups]) => ({
          id: subcategoryId,
          name: subcategoryId ? categoryLabel(registry, subcategoryId) : "Unclassified",
          groups: [...groups.entries()]
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([groupName, groupResults]) => ({ name: groupName, results: groupResults })),
        })),
    }));
}

function severityRank(filters: SavedReview["filters"]): number {
  const ranks = { low: 1, medium: 2, high: 3 } as const;
  return Math.max(0, ...filters.map((filter) => filter.severity ? ranks[filter.severity] : 0));
}

/** Sort result rows within a finding group by the active table column. */
export function sortTieredReviewResults(
  items: TieredReviewResult[],
  sort: { key: ReviewTableSortKey; direction: ReviewTableSortDirection },
): TieredReviewResult[] {
  const direction = sort.direction === "asc" ? 1 : -1;
  return [...items].sort((left, right) => {
    const primary = sort.key === "time"
      ? left.result.startSeconds - right.result.startSeconds
      : sort.key === "description"
        ? left.result.descriptions.join(" ").localeCompare(right.result.descriptions.join(" "))
        : severityRank(left.pathFilters) - severityRank(right.pathFilters);
    return primary * direction
      || left.result.startSeconds - right.result.startSeconds
      || left.result.descriptions.join(" ").localeCompare(right.result.descriptions.join(" "))
      || left.result.key.localeCompare(right.result.key);
  });
}

/** Order finding groups by their first result or displayed name. */
export function sortTieredReviewGroups(
  groups: TieredReviewGroup[],
  sort: { key: ReviewTableSortKey; direction: ReviewTableSortDirection },
): TieredReviewGroup[] {
  const direction = sort.direction === "asc" ? 1 : -1;
  return [...groups].sort((left, right) => {
    if (sort.key === "time") {
      const leftFirst = sortTieredReviewResults(left.results, sort)[0]?.result.startSeconds ?? 0;
      const rightFirst = sortTieredReviewResults(right.results, sort)[0]?.result.startSeconds ?? 0;
      return (leftFirst - rightFirst) * direction || left.name.localeCompare(right.name);
    }
    if (sort.key === "description") return left.name.localeCompare(right.name) * direction;
    return left.name.localeCompare(right.name);
  });
}

/** Return each underlying filter once across linked result rows. */
export function uniqueTieredFilters(items: TieredReviewResult[]): SavedReview["filters"] {
  return [...new Map(items.flatMap((item) => item.result.filters).map((filter) => [filter.id, filter])).values()];
}

export function severityText(filters: SavedReview["filters"]): string {
  const labels = [null, "low", "medium", "high"] as const;
  const present = new Set(filters.map((filter) => filter.severity));
  const values = labels.filter((severity) => present.has(severity));
  return values.map((severity) => severity ? `${severity[0].toUpperCase()}${severity.slice(1)}` : "None").join(", ");
}

export function channelText(filters: SavedReview["filters"]): string {
  const channels = [...new Set(filters.map((filter) => filter.mcf_channel || "both"))].sort();
  return channels.map((channel) => `${channel[0].toUpperCase()}${channel.slice(1)}`).join(", ");
}
