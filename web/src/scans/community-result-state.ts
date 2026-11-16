import { useEffect, useState } from "react";
import type { MatchBand, RemoteFilterCandidate } from "../api/api-community-lookup";

export type ResultChannel = "video" | "audio";
export type RemoteSelection = {
  revisionId: string; artifactDigest: string; targetKey: string;
  channels: ResultChannel[]; reviewRequired: ResultChannel[];
};
export const bandLabels: Record<MatchBand, string> = {
  exact_file: "Exact file", strong_fingerprint: "Strong fingerprint", release_match: "Release match",
  possible_cut: "Possible cut", title_only: "Title only", incompatible: "Incompatible",
};
const bands = Object.keys(bandLabels) as MatchBand[];
export function resultChannels(candidate: RemoteFilterCandidate): ResultChannel[] {
  return (["video", "audio"] as const).filter((channel) => channel in candidate.summary.channels
    || candidate.summary.coverage.some((coverage) => coverage.channel === channel || coverage.channel === "both"));
}
export function strongChannel(candidate: RemoteFilterCandidate, channel: ResultChannel): boolean {
  const compatibility = candidate.compatibility[channel];
  return resultChannels(candidate).includes(channel) && compatibility.strong_eligible
    && ["exact_file", "strong_fingerprint"].includes(compatibility.band) && !compatibility.conflicts.length;
}
export function requiresReview(candidate: RemoteFilterCandidate): boolean {
  const channels = resultChannels(candidate);
  return !channels.length || channels.some((channel) => !strongChannel(candidate, channel));
}
export function candidateLanguage(candidate: RemoteFilterCandidate): string {
  return candidate.summary.analyzed_audio?.language || candidate.evidence.media.release.audio?.language || "unknown";
}
export type ResultFilters = { kind: string; language: string; coverage: string; category: string; channel: string; video: string; audio: string;
  sort: "compatibility" | "quality" | "newest" | "uploader" | "imports" };
export const defaultResultFilters: ResultFilters = { kind: "all", language: "all", coverage: "all", category: "all", channel: "all", video: "all", audio: "all", sort: "compatibility" };

/** Preserve both compatibility bands ahead of any secondary sort. */
export function filterResults(candidates: RemoteFilterCandidate[], filters: ResultFilters): RemoteFilterCandidate[] {
  const order = (candidate: RemoteFilterCandidate) => [bands.indexOf(candidate.compatibility.video.band), bands.indexOf(candidate.compatibility.audio.band)];
  return candidates.filter((candidate) =>
    (filters.kind === "all" || candidate.summary.kind === filters.kind)
    && (filters.channel === "all" || resultChannels(candidate).some((channel) => channel === filters.channel))
    && (filters.category === "all" || filters.category in candidate.summary.taxonomy)
    && (filters.language === "all" || candidateLanguage(candidate) === filters.language)
    && (filters.coverage === "all" || candidate.summary.coverage.some((coverage) => coverage.state === filters.coverage)
      || (filters.coverage === "unknown" && !candidate.summary.coverage.length))
    && (filters.video === "all" || candidate.compatibility.video.band === filters.video)
    && (filters.audio === "all" || candidate.compatibility.audio.band === filters.audio)
  ).sort((left, right) => {
    const first = order(left), second = order(right);
    const compatibility = first[0] - second[0] || first[1] - second[1];
    if (compatibility) return compatibility;
    const tie = left.revisionId < right.revisionId ? -1 : left.revisionId > right.revisionId ? 1 : 0;
    if (filters.sort === "newest") return right.catalogCreatedAt - left.catalogCreatedAt || tie;
    if (filters.sort === "imports") return right.statistics.imports - left.statistics.imports || tie;
    if (filters.sort === "uploader") return left.attribution.displayName.localeCompare(right.attribution.displayName) || tie;
    if (filters.sort === "quality" || filters.sort === "compatibility") {
      if (left.ranking === null || right.ranking === null) {
        return left.ranking === right.ranking ? tie : left.ranking === null ? 1 : -1;
      }
      return right.ranking.score - left.ranking.score || tie;
    }
    return tie;
  });
}

/** Selection is channel-specific and bound to current target and immutable revision bytes. */
export function useCommunitySelection(candidates: RemoteFilterCandidate[], targetKey: string | null, disabled: boolean) {
  const [state, setState] = useState<{ key: string | null; entries: RemoteSelection[] }>({ key: null, entries: [] });
  useEffect(() => { setState({ key: targetKey, entries: [] }); }, [targetKey, disabled]);
  const selections = !disabled && state.key === targetKey && targetKey ? state.entries.filter((entry) => candidates.some(
    (candidate) => candidate.revisionId === entry.revisionId && candidate.artifactDigest === entry.artifactDigest,
  )) : [];
  const change = (candidate: RemoteFilterCandidate, channels: ResultChannel[]) => {
    if (disabled || !targetKey || !candidates.includes(candidate)) return;
    const selected = channels.filter((channel) => resultChannels(candidate).includes(channel));
    const entry: RemoteSelection = { revisionId: candidate.revisionId, artifactDigest: candidate.artifactDigest,
      targetKey, channels: selected, reviewRequired: selected.filter((channel) => !strongChannel(candidate, channel)) };
    setState((previous) => ({ key: targetKey, entries: [
      ...(previous.key === targetKey ? previous.entries.filter((item) => item.revisionId !== candidate.revisionId) : []),
      ...(selected.length ? [entry] : []),
    ] }));
  };
  return { selections, change, selectStrong: (visible: RemoteFilterCandidate[]) => {
    if (disabled || !targetKey) return;
    setState({ key: targetKey, entries: visible.flatMap((candidate) => {
      if (!candidates.includes(candidate)) return [];
      const channels = resultChannels(candidate).filter((channel) => strongChannel(candidate, channel));
      return channels.length ? [{ revisionId: candidate.revisionId, artifactDigest: candidate.artifactDigest, targetKey,
        channels, reviewRequired: [] }] : [];
    }) });
  } };
}