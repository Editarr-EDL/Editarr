/** Shared label shapes for the operator insight endpoints (contract "Labels"); every check is exact. */
import { array, boolean, fields, finite, fraction, identifier, integer, literal, nullable, number, oneOf, text,
  type Check } from "./api-community-operator-checks";

export interface ContributorRef { contributorId: string; displayName: string }
export interface ItemRef { workId: string; title: string; year: number | null; kind: "movie" | "episode" | null;
  episode: { season: number; episode: number } | null }
export interface VersionRef { versionId: string; label: string }
export interface FilterLabel { filterId: string; category: string | null; subcategory: string | null; action: string | null;
  startMs: number | null; endMs: number | null; expression?: string | null }
export interface SeriesPoint { t: number; v: number }
/** The moderation resource name and its optimistic revision (the expected revision for an action). */
export interface ModerationToken { resource: string; revision: number }
export interface OperatorList<T> { version: 1; observedAt: number; items: T[]; nextCursor: string | null }

export const envelope = { version: literal(1), observedAt: number };
export const contributorRef = fields({ contributorId: identifier, displayName: text });
export const itemRef = fields({ workId: identifier, title: text, year: nullable(number), kind: nullable(oneOf("movie", "episode")),
  episode: nullable(fields({ season: number, episode: number })) });
export const versionRef = fields({ versionId: identifier, label: text });
export const filterLabelFields = { filterId: identifier, category: nullable(text), subcategory: nullable(text),
  action: nullable(text), startMs: nullable(integer), endMs: nullable(integer) };
export const filterLabelOptional = { expression: nullable(text) };
export const filterLabel = fields(filterLabelFields, filterLabelOptional);
export const moderationToken = fields({ resource: text, revision: number });
export const series = array(fields({ t: number, v: finite }), 400);
export const cursor = nullable(text);
/** A keyset page `{ version, observedAt, items, nextCursor }` of at most `max` rows plus endpoint `extra` fields. */
export const listShape = (row: Check, max = 100, extra: Record<string, Check> = {}, optional: Record<string, Check> = {}) =>
  fields({ ...envelope, ...extra, items: array(row, max), nextCursor: cursor }, optional);

/** Revision cause: an upload or vote change by a contributor, or an operator moderation command. */
export type RevisionCause = { kind: "upload" | "votes"; contributor: ContributorRef } | { kind: "moderation"; actor: string; command: string };
export const revisionCause = (value: unknown) => fields({ kind: oneOf("upload", "votes"), contributor: contributorRef })(value) ||
  fields({ kind: literal("moderation"), actor: text, command: text })(value);
/** Served-set changes against the previous revision; null when the earlier snapshot is unavailable. */
export interface RevisionDelta { filters: number | null; added: number | null; removed: number | null; retimed: number | null; recategorised: number | null }
export const revisionDelta = { filters: nullable(number), added: nullable(number), removed: nullable(number), retimed: nullable(number),
  recategorised: nullable(number) };

/** A registry filter with master stats (null for registry-only filters) and its moderation token. */
export interface FilterStats extends FilterLabel { state: "active" | "retired" | "quarantined"; mergedInto: string | null; quarantined: boolean;
  severity: "low" | "medium" | "high" | null; confidence: number | null; spread: { startMs: number; endMs: number } | null;
  contributorCount: number | null; attestations: number | null; upvotes: number | null; downvotes: number | null; disputes: number | null;
  coverageNegatives: number | null; lowConfidence: boolean | null; served: boolean | null; openReports: number; moderation: ModerationToken }
export const filterStats = fields({ ...filterLabelFields, state: oneOf("active", "retired", "quarantined"), mergedInto: nullable(identifier),
  quarantined: boolean, severity: nullable(oneOf("low", "medium", "high")), confidence: nullable(fraction),
  spread: nullable(fields({ startMs: integer, endMs: integer })), contributorCount: nullable(number), attestations: nullable(number),
  upvotes: nullable(number), downvotes: nullable(number), disputes: nullable(number), coverageNegatives: nullable(number),
  lowConfidence: nullable(boolean), served: nullable(boolean), openReports: number, moderation: moderationToken }, filterLabelOptional);
