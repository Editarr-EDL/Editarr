import type { MatchBand, RemoteFilterCandidate } from "../api/api-community-lookup";

export type ResultChannel = "video" | "audio";
export type ChannelFilter = "all" | ResultChannel | "both";
export type ResultSort = { column: "match" | "name" | "filters"; direction: "ascending" | "descending" };
export type RemoteSelection = {
  revisionId: string; artifactDigest: string; targetKey: string;
  channels: ResultChannel[]; reviewRequired: ResultChannel[];
};
export const bandLabels: Record<MatchBand, string> = {
  exact_file: "Exact file", strong_fingerprint: "Strong fingerprint", release_match: "Release match",
  possible_cut: "Possible cut", title_only: "Title only", incompatible: "Incompatible",
};
export const matchBands = Object.keys(bandLabels) as MatchBand[];

export function resultChannels(candidate: RemoteFilterCandidate): ResultChannel[] {
  return (["video", "audio"] as const).filter((channel) => channel in candidate.summary.channels
    || candidate.summary.coverage.some((coverage) => coverage.channel === channel || coverage.channel === "both"));
}

export function scopedChannels(candidate: RemoteFilterCandidate, filter: ChannelFilter): ResultChannel[] {
  const channels = resultChannels(candidate);
  if (filter === "all") return channels;
  if (filter === "both") return channels.length === 2 ? channels : [];
  return channels.includes(filter) ? [filter] : [];
}

export function compatibleChannels(candidate: RemoteFilterCandidate, channels: ResultChannel[]): ResultChannel[] {
  return channels.filter((channel) => {
    const compatibility = candidate.compatibility[channel];
    return compatibility.band !== "incompatible" && !compatibility.conflicts.length;
  });
}

export function strongChannel(candidate: RemoteFilterCandidate, channel: ResultChannel): boolean {
  const compatibility = candidate.compatibility[channel];
  return compatibility.strong_eligible && ["exact_file", "strong_fingerprint"].includes(compatibility.band)
    && !compatibility.conflicts.length;
}

export function selectDownload(candidate: RemoteFilterCandidate, targetKey: string, channels: ResultChannel[]): RemoteSelection {
  return { revisionId: candidate.revisionId, artifactDigest: candidate.artifactDigest, targetKey,
    channels, reviewRequired: channels.filter((channel) => !strongChannel(candidate, channel)) };
}

export function resultName(candidate: RemoteFilterCandidate): string {
  return candidate.summary.name || candidate.revisionId;
}

export function displayedBand(candidate: RemoteFilterCandidate, channels: ResultChannel[]): MatchBand | null {
  if (!channels.length) return null;
  return matchBands[Math.max(...channels.map((channel) => matchBands.indexOf(candidate.compatibility[channel].band)))];
}

export function matchBandCounts(candidates: RemoteFilterCandidate[], channel: ChannelFilter): ReadonlyMap<MatchBand, number> {
  const counts = new Map<MatchBand, number>();
  for (const candidate of candidates) {
    const band = displayedBand(candidate, scopedChannels(candidate, channel));
    if (band) counts.set(band, (counts.get(band) ?? 0) + 1);
  }
  return counts;
}

export function categoryLabel(value: string): string {
  const spaced = value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function categoryCounts(taxonomy: Record<string, number>) {
  const categories = new Map<string, { total: number; items: { label: string; count: number }[] }>();
  for (const [path, count] of Object.entries(taxonomy)) {
    const [family, subcategory] = path.split("/");
    const entry = categories.get(family) ?? { total: 0, items: [] };
    entry.total += count;
    if (subcategory) entry.items.push({ label: subcategory === family ? "General" : categoryLabel(subcategory), count });
    categories.set(family, entry);
  }
  return [...categories].map(([family, entry]) => ({ label: categoryLabel(family), ...entry }))
    .sort((left, right) => right.total - left.total || left.label.localeCompare(right.label));
}

export function communityRows(candidates: RemoteFilterCandidate[], channel: ChannelFilter, selectedBands: ReadonlySet<MatchBand>, sort: ResultSort) {
  const rows = candidates.flatMap((candidate) => {
    const channels = scopedChannels(candidate, channel);
    const band = displayedBand(candidate, channels);
    if ((channel !== "all" && !channels.length) || (band ? !selectedBands.has(band) : selectedBands.size !== matchBands.length)) return [];
    return [{ candidate, channels, band, count: channel === "audio" || channel === "video"
      ? candidate.summary.channels[channel] ?? null : candidate.summary.total }];
  });
  return rows.sort((left, right) => {
    if (sort.column === "filters" && (left.count === null || right.count === null)) {
      return Number(left.count === null) - Number(right.count === null);
    }
    if (sort.column === "match" && (!left.band || !right.band)) return Number(!left.band) - Number(!right.band);
    const order = sort.column === "name" ? resultName(left.candidate).localeCompare(resultName(right.candidate))
      : sort.column === "filters" ? (left.count ?? 0) - (right.count ?? 0)
        : matchBands.indexOf(left.band!) - matchBands.indexOf(right.band!);
    // Stable ties preserve catalog order, including its ranking within the same compatibility grade.
    return sort.direction === "ascending" ? order : -order;
  });
}
