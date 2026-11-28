/** Catalog reads: items, item detail, version filters and revisions, filter context and global search. */
import { OperatorAccessError } from "../settings/community-operator/runtime";
import { operatorRequest } from "./api-community-operator";
import { array, boolean, fields, fraction, identifier, integer, nullable, number, oneOf, text } from "./api-community-operator-checks";
import { contributorRef, cursor, envelope, filterStats, itemRef, listShape, moderationToken, revisionCause, revisionDelta, versionRef,
  type ContributorRef, type FilterStats, type ItemRef, type ModerationToken, type OperatorList, type RevisionCause,
  type RevisionDelta, type VersionRef } from "./api-community-operator-labels";

export type VersionStatus = "confirmed" | "candidate" | "merged_into";
export interface ItemVersion { versionId: string; label: string; status: VersionStatus; quarantined: boolean; runtimeMs: number | null; revision: number }
export interface ItemRow extends ItemRef { versions: ItemVersion[]; lastChangeAt: number | null; filters: number; netVotes: number;
  openReports: number; uploads: number; contributors: ContributorRef[] }
export type ItemSort = "updated" | "uploads" | "votes" | "reports" | "title";
export const itemSorts: ItemSort[] = ["updated", "uploads", "votes", "reports", "title"];
export interface MatchKey { type: string; algorithm: string; sizeBytes: number | null; isReference: boolean; observations: number }
export interface ItemDetailVersion extends ItemVersion { aliasId: string | null; labelText: string | null; mergedInto: string | null; trust: number;
  createdAt: number | null; revisedAt: number | null; downloads: number; uploads: number; filters: number; servedFilters: number; openReports: number;
  matchKeys: MatchKey[]; contributors: (ContributorRef & { tier: number | null; founder: boolean })[]; moderation: ModerationToken }
export interface WorkIdentifier { provider: string; kind: string; value: string; season: number | null; episode: number | null; lastEpisode: number | null }
export interface ItemDetail { version: 1; observedAt: number; work: ItemRef & { createdAt: number | null;
  aliases: { title: string; year: number | null; observations: number }[]; identifiers: WorkIdentifier[] }; versions: ItemDetailVersion[] }
export interface Revision extends RevisionDelta { revision: number; generatedAt: number; cause: RevisionCause | null; current: boolean }
export interface VersionDetail { version: 1; observedAt: number; item: ItemRef; versionInfo: VersionRef & { status: VersionStatus;
  mergedInto: string | null; quarantined: boolean; runtimeMs: number | null; trust: number; revision: number };
  filters: FilterStats[]; truncated: boolean; revisions: Revision[]; revisionsNextCursor: string | null }
export interface FilterVote { contributor: ContributorRef; vote: "up" | "down"; excluded: boolean; revision: number | null; at: number | null; moderation: ModerationToken }
export interface FilterAttestation { contributor: ContributorRef; submissionId: string; entryId: string; kind: string; implicit: boolean; counted: boolean;
  tier: number | null; at: number | null; startMs: number | null; endMs: number | null; alignedStartMs: number | null; alignedEndMs: number | null;
  origin: string | null; manualReview: boolean | null; locallyVerified: boolean | null; modelVersion: string | null; ignoredReason: string | null; quarantined: boolean }
export interface FilterReport { reportId: string; contributor: ContributorRef; reason: string; note: string | null; state: "open" | "resolved";
  at: number | null; moderation: ModerationToken }
export interface FilterModeration { actionId: string; actor: string; resource: string; command: string; reason: string; outcome: string; at: number;
  voter: ContributorRef | null }
export interface FilterContext { version: 1; observedAt: number; filter: FilterStats; item: ItemRef | null; versionLabel: VersionRef | null;
  votes: FilterVote[]; attestations: FilterAttestation[]; reports: FilterReport[]; moderation: FilterModeration[] }
export interface SearchId { kind: string; id: string; label: string }
export interface SearchResult { version: 1; observedAt: number; q: string; items: ItemRef[]; contributors: ContributorRef[]; ids: SearchId[] }

const big = { maxBytes: 4194304 };
const status = oneOf("confirmed", "candidate", "merged_into"), at = nullable(number);
const versionFields = { versionId: identifier, label: text, status, quarantined: boolean, runtimeMs: nullable(number), revision: number };
const { workId, title, year, kind, episode } = { workId: identifier, title: text, year: nullable(number), kind: nullable(oneOf("movie", "episode")),
  episode: nullable(fields({ season: number, episode: number })) };
const itemFields = { workId, title, year, kind, episode };
const itemRow = fields({ ...itemFields, versions: array(fields(versionFields), 100), lastChangeAt: at, filters: number, netVotes: integer,
  openReports: number, uploads: number, contributors: array(contributorRef, 5) });
const itemDetail = fields({ ...envelope,
  work: fields({ ...itemFields, createdAt: at, aliases: array(fields({ title: text, year: nullable(number), observations: number }), 20),
    identifiers: array(fields({ provider: text, kind: text, value: text, season: nullable(number), episode: nullable(number), lastEpisode: nullable(number) }), 20) }),
  versions: array(fields({ ...versionFields, aliasId: nullable(text), labelText: nullable(text), mergedInto: nullable(identifier), trust: fraction,
    createdAt: at, revisedAt: at, downloads: number, uploads: number, filters: number, servedFilters: number, openReports: number,
    matchKeys: array(fields({ type: text, algorithm: text, sizeBytes: nullable(number), isReference: boolean, observations: number }), 1000),
    contributors: array(fields({ contributorId: identifier, displayName: text, tier: nullable(number), founder: boolean }), 1000),
    moderation: moderationToken }), 100) });
const versionDetail = fields({ ...envelope, item: itemRef,
  versionInfo: fields({ versionId: identifier, label: text, status, mergedInto: nullable(identifier), quarantined: boolean,
    runtimeMs: nullable(number), trust: fraction, revision: number }),
  filters: array(filterStats, 2000), truncated: boolean,
  revisions: array(fields({ revision: number, generatedAt: number, ...revisionDelta, cause: nullable(revisionCause), current: boolean }), 10),
  revisionsNextCursor: cursor });
const filterContext = fields({ ...envelope, filter: filterStats, item: nullable(itemRef), versionLabel: nullable(versionRef),
  votes: array(fields({ contributor: contributorRef, vote: oneOf("up", "down"), excluded: boolean, revision: nullable(number), at,
    moderation: moderationToken }), 200),
  attestations: array(fields({ contributor: contributorRef, submissionId: identifier, entryId: identifier, kind: text, implicit: boolean,
    counted: boolean, tier: nullable(number), at, startMs: nullable(integer), endMs: nullable(integer), alignedStartMs: nullable(integer),
    alignedEndMs: nullable(integer), origin: nullable(text), manualReview: nullable(boolean), locallyVerified: nullable(boolean),
    modelVersion: nullable(text), ignoredReason: nullable(text), quarantined: boolean }), 200),
  reports: array(fields({ reportId: identifier, contributor: contributorRef, reason: text, note: nullable(text), state: oneOf("open", "resolved"),
    at, moderation: moderationToken }), 200),
  moderation: array(fields({ actionId: identifier, actor: text, resource: text, command: text, reason: text, outcome: text, at: number,
    voter: nullable(contributorRef) }), 20) });
const search = fields({ ...envelope, q: text, items: array(itemRef, 20), contributors: array(contributorRef, 20),
  ids: array(fields({ kind: text, id: text, label: text }), 20) });

const id = (value: string) => { if (!identifier(value)) throw new OperatorAccessError(400); return value; };
const query = (values: Record<string, string | null | undefined>) => {
  const params = new URLSearchParams(Object.entries(values).filter((entry): entry is [string, string] => !!entry[1]));
  return params.size ? `?${params}` : "";
};

/** One page of catalog items in `sort` order, optionally filtered by title or year text `q`. */
export function readOperatorItems(sort: ItemSort, q: string, after: string | null, signal: AbortSignal) {
  if (!itemSorts.includes(sort) || q.length > 100) throw new OperatorAccessError(400);
  return operatorRequest<OperatorList<ItemRow> & { sort: string; q: string | null }>(`/admin/v1/items${query({ sort, q: q.trim(), after })}`,
    listShape(itemRow, 100, { sort: text, q: nullable(text) }), signal, undefined, big);
}
export const readOperatorItem = (workId: string, signal: AbortSignal) =>
  operatorRequest<ItemDetail>(`/admin/v1/items/${id(workId)}`, itemDetail, signal, undefined, big);
/** The version's filters (up to 2000) and a page of revisions before the `before` cursor. */
export const readOperatorVersionDetail = (workId: string, versionId: string, signal: AbortSignal, before: string | null = null) =>
  operatorRequest<VersionDetail>(`/admin/v1/items/${id(workId)}/versions/${id(versionId)}${query({ before })}`, versionDetail, signal, undefined, big);
export const readOperatorFilterContext = (filterId: string, signal: AbortSignal) =>
  operatorRequest<FilterContext>(`/admin/v1/filters/${id(filterId)}/context`, filterContext, signal, undefined, big);
/** Search titles, display names and exact IDs; at most 20 results. */
export function readOperatorSearch(q: string, signal: AbortSignal) {
  const value = q.trim();
  if (!value || value.length > 100) throw new OperatorAccessError(400);
  return operatorRequest<SearchResult>(`/admin/v1/search${query({ q: value })}`, search, signal);
}
