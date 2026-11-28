/** Community reads: home, activity, leaderboard, contributor profile, moderation queue and labelled audit. */
import { OperatorAccessError } from "../settings/community-operator/runtime";
import { operatorRequest } from "./api-community-operator";
import { array, boolean, fields, finite, fraction, identifier, integer, literal, nullable, number, oneOf, scalars, text,
  type Check } from "./api-community-operator-checks";
import { contributorRef, envelope, filterLabel, itemRef, listShape, moderationToken, revisionCause, revisionDelta, series, versionRef,
  type ContributorRef, type FilterLabel, type ItemRef, type ModerationToken, type OperatorList, type RevisionCause, type RevisionDelta,
  type SeriesPoint, type VersionRef } from "./api-community-operator-labels";
import { healthFields, type OperatorHealth } from "./api-community-operator-system";

export type HomePeriod = "24h" | "7d" | "30d";
export interface Kpi { value: number; previous: number; series: SeriesPoint[] }
export type QueueKind = "all" | "reports" | "aliases" | "versions" | "quarantined" | "disputed";
export const queueKinds: QueueKind[] = ["all", "reports", "aliases", "versions", "quarantined", "disputed"];
export type QueueCounts = Record<QueueKind, number>;
/** One open decision; `resource`/`revision` are the moderation target and its expected revision. */
export interface QueueItem { kind: "report" | "alias" | "version" | "quarantined" | "disputed"; id: string; at: number;
  resource: string; revision: number; target: { type: string; id: string }; title: { targetType: string; text: string };
  item: ItemRef | null; version: VersionRef | null; filter: FilterLabel | null; reporter: ContributorRef | null;
  reason: string | null; quote: string | null; suggestion: { text: string; normalized: string } | null;
  impact: Record<string, number | null> | null; latestAction: { actor: string; command: string; reason: string; at: number } | null }
export interface QueuePage extends OperatorList<QueueItem> { kind: QueueKind; counts: QueueCounts }
export interface Update extends RevisionDelta { at: number; item: ItemRef | null; version: VersionRef | null; filter: FilterLabel | null;
  revision: number; generatedAt: number; cause: RevisionCause | null }
export interface LeaderRow { contributor: ContributorRef; rank: number; accepted: number; uploads: number; votesReceived: number;
  votesCast: number; reportsAgainst: number; joinedAt: number; state: string }
export type Actor = { type: "contributor"; contributorId: string; displayName: string } | { type: "operator"; subject: string };
export interface ActivityEvent { id: string; kind: "uploads" | "votes" | "reports" | "moderation"; at: number; actor: Actor | null; verb: string;
  item: ItemRef | null; version: VersionRef | null; filter: FilterLabel | null; subject: ContributorRef | null;
  detail: Record<string, string | number | boolean | null> }
export interface OperatorHome { version: 1; observedAt: number; period: HomePeriod;
  kpis: { activeContributors: Kpi; uploads: Kpi & { items: number }; votes: { total: Kpi; upShare: number | null };
    openReports: { count: number; oldestAt: number | null; versionLevel: number };
    lastBackup: { status: "running" | "verified" | "failed" | "pruned" | null; at: number | null; bytes: number | null; nextAt: number | null } | null };
  queueCounts: QueueCounts; attention: QueueItem[]; latestUpdates: Update[]; topContributors: LeaderRow[]; activity: ActivityEvent[];
  health: Omit<OperatorHealth, "version" | "observedAt"> }
export type ActivityKind = "all" | "uploads" | "votes" | "reports" | "moderation";
export type LeaderPeriod = "7d" | "30d" | "all";
export type LeaderMetric = "accepted" | "uploads" | "votes_received" | "votes_cast";
interface Target { item: ItemRef | null; version: VersionRef | null; filter: FilterLabel | null }
type Usage = Partial<Record<"allowed" | "denied" | "retried", { occurrences: number; units: number; observedAt: number }>>;
export interface ContributorProfile { version: 1; observedAt: number;
  identity: { contributorId: string; displayName: string; state: "active" | "suspended"; quarantined: boolean; joinedAt: number | null;
    profileUpdatedAt: number | null; membership: { authority: string; state: string; checkedAt: number | null }[]; moderation: ModerationToken };
  totals: { uploads: number; items: number; accepted: number; votesCast: number; votesUpShare: number | null; reportsFiled: number };
  installations: { installationId: string; createdAt: number | null; lastSyncAt: number | null; keys: { active: number; retired: number; revoked: number };
    newestKey: { keyId: string; state: "active" | "retired" | "revoked"; createdAt: number | null; retiredAt: number | null } | null }[];
  uploads: (Target & { submissionId: string; at: number | null; tier: number | null; quarantined: boolean; state: string | null; entries: number })[];
  votes: (Target & { eventId: number; kind: "cast" | "changed" | "removed" | "excluded" | "restored"; vote: "up" | "down"; at: number | null })[];
  reportsFiled: (Target & { reportId: string; reason: string; state: "open" | "resolved"; at: number | null; targetType: string })[];
  reportsAgainst: (Target & { reportId: string; reason: string; state: "open" | "resolved"; at: number | null; reporter: ContributorRef | null })[];
  usage: { source: "application_observed"; providerVerified: false; period: string; requests: Usage; writes: Usage;
    storage: { bytes: number; quotaBytes: number | null } } }
export interface ResourceLabel extends Target { type: string; id: string | null; text: string; contributor: ContributorRef | null }
export interface AuditRow { actionId: string; actor: string; resource: string; targetId: string | null; command: string; reason: string;
  outcome: "applied" | "denied" | "conflict"; errorCode: string | null; expectedRevision: number; createdAt: number; resourceLabel: ResourceLabel }

const at = nullable(number);
const counts = fields({ all: number, reports: number, aliases: number, versions: number, quarantined: number, disputed: number });
const kpi = { value: number, previous: number, series };
export const queueItem = fields({ kind: oneOf("report", "alias", "version", "quarantined", "disputed"), id: text, at: number,
  resource: text, revision: number, target: fields({ type: text, id: text }), title: fields({ targetType: text, text }),
  item: nullable(itemRef), version: nullable(versionRef), filter: nullable(filterLabel), reporter: nullable(contributorRef),
  reason: nullable(text), quote: nullable(text), suggestion: nullable(fields({ text, normalized: text })),
  impact: nullable(scalars()), latestAction: nullable(fields({ actor: text, command: text, reason: text, at: number })) });
const target = { item: nullable(itemRef), version: nullable(versionRef), filter: nullable(filterLabel) };
const update = fields({ at: number, ...target, revision: number, generatedAt: number, ...revisionDelta, cause: nullable(revisionCause) });
const leaderRow = fields({ contributor: contributorRef, rank: number, accepted: number, uploads: number, votesReceived: integer,
  votesCast: number, reportsAgainst: number, joinedAt: number, state: text });
const actor: Check = value => fields({ type: literal("contributor"), contributorId: identifier, displayName: text })(value) ||
  fields({ type: literal("operator"), subject: text })(value);
const activityEvent = fields({ id: text, kind: oneOf("uploads", "votes", "reports", "moderation"), at: number, actor: nullable(actor), verb: text,
  ...target, subject: nullable(contributorRef), detail: scalars() });
const home = fields({ ...envelope, period: oneOf("24h", "7d", "30d"),
  kpis: fields({ activeContributors: fields(kpi), uploads: fields({ ...kpi, items: number }), votes: fields({ total: fields(kpi), upShare: nullable(fraction) }),
    openReports: fields({ count: number, oldestAt: at, versionLevel: number }),
    lastBackup: nullable(fields({ status: nullable(oneOf("running", "verified", "failed", "pruned")), at, bytes: nullable(number), nextAt: at })) }),
  queueCounts: counts, attention: array(queueItem, 5), latestUpdates: array(update, 5), topContributors: array(leaderRow, 5),
  activity: array(activityEvent, 8), health: fields(healthFields) });
const usage = fields({}, { allowed: fields({ occurrences: number, units: number, observedAt: number }),
  denied: fields({ occurrences: number, units: number, observedAt: number }), retried: fields({ occurrences: number, units: number, observedAt: number }) });
const reportState = oneOf("open", "resolved");
const profile = fields({ ...envelope,
  identity: fields({ contributorId: identifier, displayName: text, state: oneOf("active", "suspended"), quarantined: boolean, joinedAt: at,
    profileUpdatedAt: at, membership: array(fields({ authority: text, state: text, checkedAt: at }), 10), moderation: moderationToken }),
  totals: fields({ uploads: number, items: number, accepted: number, votesCast: number, votesUpShare: nullable(fraction), reportsFiled: number }),
  installations: array(fields({ installationId: identifier, createdAt: at, lastSyncAt: at, keys: fields({ active: number, retired: number, revoked: number }),
    newestKey: nullable(fields({ keyId: text, state: oneOf("active", "retired", "revoked"), createdAt: at, retiredAt: at })) }), 20),
  uploads: array(fields({ submissionId: identifier, at, tier: nullable(number), quarantined: boolean, state: nullable(text), entries: number, ...target }), 10),
  votes: array(fields({ eventId: number, kind: oneOf("cast", "changed", "removed", "excluded", "restored"), vote: oneOf("up", "down"), at, ...target }), 10),
  reportsFiled: array(fields({ reportId: identifier, reason: text, state: reportState, at, targetType: text, ...target }), 10),
  reportsAgainst: array(fields({ reportId: identifier, reason: text, state: reportState, at, reporter: nullable(contributorRef), ...target }), 10),
  usage: fields({ source: literal("application_observed"), providerVerified: literal(false), period: text, requests: usage, writes: usage,
    storage: fields({ bytes: finite, quotaBytes: nullable(number) }) }) });
const resourceLabel = fields({ type: text, id: nullable(text), text, ...target, contributor: nullable(contributorRef) });
const auditRow = fields({ actionId: identifier, actor: text, resource: text, targetId: nullable(text), command: text, reason: text,
  outcome: oneOf("applied", "denied", "conflict"), errorCode: nullable(text), expectedRevision: number, createdAt: number, resourceLabel });

const big = { maxBytes: 1048576 };
const query = (values: Record<string, string | null | undefined>) => {
  const params = new URLSearchParams(Object.entries(values).filter((entry): entry is [string, string] => !!entry[1]));
  return params.size ? `?${params}` : "";
};
function allowed<T extends string>(value: T, values: readonly string[]) {
  if (!values.includes(value)) throw new OperatorAccessError(400);
  return value;
}

export const readOperatorHome = (period: HomePeriod, signal: AbortSignal) =>
  operatorRequest<OperatorHome>(`/admin/v1/home${query({ period: allowed(period, ["24h", "7d", "30d"]) })}`, home, signal, undefined, big);
export const readOperatorActivity = (kind: ActivityKind, before: string | null, signal: AbortSignal) =>
  operatorRequest<OperatorList<ActivityEvent> & { kind: ActivityKind }>(`/admin/v1/activity${query({
    kind: allowed(kind, ["all", "uploads", "votes", "reports", "moderation"]), before })}`, listShape(activityEvent, 100, { kind: text }), signal, undefined, big);
export const readOperatorLeaderboard = (period: LeaderPeriod, metric: LeaderMetric, signal: AbortSignal, after: string | null = null) =>
  operatorRequest<OperatorList<LeaderRow> & { period: string; metric: string; since: number | null; total: number }>(
    `/admin/v1/contributors/leaderboard${query({ period: allowed(period, ["7d", "30d", "all"]),
      metric: allowed(metric, ["accepted", "uploads", "votes_received", "votes_cast"]), after })}`,
    listShape(leaderRow, 100, { period: text, metric: text, since: at, total: number }), signal, undefined, big);
export function readOperatorProfile(contributorId: string, signal: AbortSignal) {
  if (!identifier(contributorId)) throw new OperatorAccessError(400);
  return operatorRequest<ContributorProfile>(`/admin/v1/contributors/${contributorId}/profile`, profile, signal, undefined, big);
}
export const readOperatorQueue = (kind: QueueKind, after: string | null, signal: AbortSignal) =>
  operatorRequest<QueuePage>(`/admin/v1/queue${query({ kind: kind === "all" ? null : allowed(kind, queueKinds), after })}`,
    listShape(queueItem, 100, { kind: text, counts }), signal, undefined, big);
export const readOperatorAudit = (after: string | null, signal: AbortSignal) =>
  operatorRequest<OperatorList<AuditRow> & { source: string }>(`/admin/v1/audit${query({ after })}`,
    listShape(auditRow, 100, { source: text }), signal, undefined, big);
