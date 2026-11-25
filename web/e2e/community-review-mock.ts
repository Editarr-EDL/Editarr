/**
 * A saved review whose PUT keeps community filters the way the backend does
 * (scanner/persistence/repository/review_community_filters.py): including a master set adds its served
 * filters as normal review filters (below-threshold ones disabled), and Refresh keeps edited filters,
 * takes new master values for the others, adds new filters and flags filters no longer served.
 */
import type { Page } from "@playwright/test";
import { json, mockReview } from "./app-fixtures";
import type { CommunityMastersMock, ServedFilter } from "./community-masters-mock";
import type { ReviewCommunityMembership, ReviewFilter } from "../src/api/api-review-types";

type Source = { id: string; entries: ServedFilter[]; removed: Set<string>; revision: number };
type Save = { community_memberships?: ReviewCommunityMembership[]; filters: ReviewFilter[] };

const material = (entries: ServedFilter[]) => JSON.stringify(entries.map((entry) =>
  [entry.filterId, entry.category, entry.subcategory, entry.severity, entry.action, entry.startMs, entry.endMs]));

function defaults(sourceId: string, entry: ServedFilter, id = `row-${sourceId}-${entry.filterId}`): ReviewFilter {
  const mute = entry.action === "mute";
  return { id, source_scan_id: null, source_finding_id: null, community_source_id: sourceId, community_filter_id: entry.filterId,
    source: mute ? "audio" : "visual", category: entry.category as ReviewFilter["category"],
    subcategory: entry.subcategory as ReviewFilter["subcategory"], severity: entry.severity as ReviewFilter["severity"],
    mcf_channel: mute ? "audio" : "both", mcf_comment: null, label: null, details: null, group_key: null,
    start_seconds: entry.startMs / 1000, end_seconds: entry.endMs / 1000, confidence: entry.confidence,
    edit_action: mute ? "mute" : "skip", selected: entry.startsEnabled, community_edited: false };
}

const edited = (row: ReviewFilter) => !!row.community_edited;

/** Serve `review-c` with community snapshots; returns the PUT bodies. */
export async function mockCommunityReview(page: Page, community: CommunityMastersMock, name = "Community review") {
  const sources = new Map<string, Source>();
  let snapshots = 0;
  let review = { ...mockReview("review-c", name, "source-1"), community_sources: [] as unknown[] };
  const saves: Save[] = [];
  const present = () => {
    const served = community.servedFilters();
    const link = community.state.link!;
    return {
      ...review,
      community_sources: [...sources.values()].map((source) => ({ source_id: source.id, link_id: link.linkId,
        community_version_id: link.communityVersionId, label: link.label, tier: link.tier, match_percent: link.matchPercent,
        master_revision: source.revision, alignment: null, captured_at: "2026-11-24T00:00:00",
        state: material(source.entries.filter((entry) => !source.removed.has(entry.filterId))) === material(served)
          ? "current" : "changed", link_state: "active",
        filter_count: source.entries.length - source.removed.size })),
      filters: review.filters.map((row: ReviewFilter) => {
        const source = row.community_source_id ? sources.get(row.community_source_id) : null;
        if (!source) return row;
        const entry = source.entries.find((item) => item.filterId === row.community_filter_id);
        return { ...row, community: { source_id: source.id, filter_id: row.community_filter_id, label: community.state.link!.label,
          master_revision: source.revision, confidence: entry?.confidence ?? null, contributor_count: entry?.contributorCount ?? null,
          low_confidence: !!entry?.lowConfidence, removed: source.removed.has(row.community_filter_id!),
          locally_verified: false } };
      }),
    };
  };
  const refresh = (source: Source, current: ReviewFilter[]) => {
    const served = community.servedFilters();
    const rows: ReviewFilter[] = [];
    for (const row of current) {
      const entry = served.find((item) => item.filterId === row.community_filter_id);
      if (entry) { rows.push(edited(row) ? row : defaults(source.id, entry, row.id)); source.removed.delete(entry.filterId); continue; }
      source.removed.add(row.community_filter_id!);
      rows.push(edited(row) ? row : { ...row, selected: false });
    }
    for (const entry of served) if (!rows.some((row) => row.community_filter_id === entry.filterId)) rows.push(defaults(source.id, entry));
    const kept = [...source.removed].flatMap((id) => source.entries.filter((item) => item.filterId === id)
      .map((item) => ({ ...item, startsEnabled: false })));
    source.entries = [...served, ...kept];
    source.revision = community.state.link!.appliedRevision;
    return rows;
  };
  await page.route("**/api/library-items/movie-1/reviews*", (route) => route.request().method() === "GET"
    ? json(route, [present()]) : route.fallback());
  await page.route("**/api/reviews/review-c", async (route) => {
    if (route.request().method() === "GET") return json(route, present());
    const body = route.request().postDataJSON() as Save;
    saves.push(body);
    const scanFilters = body.filters.filter((row) => !row.community_source_id);
    const rows: ReviewFilter[] = [];
    const kept = new Set<string>();
    for (const membership of body.community_memberships ?? [...sources.keys()].map((id) => ({ source_id: id }))) {
      if (membership.link_id) {
        const source = { id: `snap-${++snapshots}`, entries: community.servedFilters(), removed: new Set<string>(),
          revision: community.state.link!.appliedRevision };
        sources.set(source.id, source);
        kept.add(source.id);
        rows.push(...source.entries.map((entry) => defaults(source.id, entry)));
        continue;
      }
      const source = sources.get(membership.source_id!)!;
      kept.add(source.id);
      const current = body.filters.filter((row) => row.community_source_id === source.id).map(({ community: _c, ...row }) => row);
      rows.push(...(membership.refresh ? refresh(source, current) : current));
    }
    for (const id of [...sources.keys()]) if (!kept.has(id)) sources.delete(id);
    review = { ...review, revision: review.revision + 1, content_revision: review.content_revision + 1,
      edl_content_revision: review.edl_content_revision + 1, filters: [...scanFilters, ...rows] as never };
    return json(route, present());
  });
  return saves;
}
