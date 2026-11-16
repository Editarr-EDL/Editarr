import { OperatorAccessError } from "../settings/community-operator/runtime";

export type OperatorRow = Record<string, string | number | null>;
export interface OperatorPage { items: OperatorRow[]; nextCursor: string | null; observedAt: number; source: string }
export interface OperatorInspection { resource: string; revision: number; state: OperatorRow; audit: OperatorRow[] }
export interface OperatorUsage { contributorId: string; observedAt: number; period: string;
  storage: { source: string; observedAt: number; bytes: number };
  observations: { resource: string; outcome: string; occurrences: number; units: number; observedAt: number }[] }
export interface OperatorOverview { observedAt: number; period: string;
  storage: { source: string; observedAt: number; providerVerified: false; globalBytes: number };
  databaseSize: Unavailable; providerUsage: Unavailable; backup: Unavailable;
  activity: { source: string; providerVerified: false; restoreProvenance: string;
    resources: Record<string, { observed: number | null; observedAt: number | null }> };
  observations: { handler: string; source: string; providerVerified: false; occurrences: number | null;
    reads: number | null; unknownReads: number | null; writes: number | null; unknownWrites: number | null; observedAt: number | null }[] }
interface Unavailable { source: "unavailable"; observedAt: null; value: null; providerVerified: false }
type Check = (value: unknown) => boolean;
const text: Check = value => typeof value === "string" && value.length <= 1000;
const identifier: Check = value => typeof value === "string" && /^[A-Za-z0-9_.-]{1,128}$/.test(value);
const number: Check = value => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const nullable = (check: Check): Check => value => value === null || check(value);
const literal = (expected: unknown): Check => value => value === expected;
const oneOf = (...values: string[]): Check => value => typeof value === "string" && values.includes(value);
const fields = (shape: Record<string, Check>): Check => value => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).length === Object.keys(shape).length && Object.entries(shape).every(([key, check]) => check(record[key]));
};
const array = (check: Check, max = 20): Check => value => Array.isArray(value) && value.length <= max && value.every(check);
const version = literal(1), no = literal(false), source = literal("service_database");
const ranking = fields({ policyVersion: literal("community-ranking-wilson-1"), qualityScore: value => typeof value === "number" && value >= 0 && value <= 1,
  voteCount: number, uploaderBoost: literal(0), score: value => typeof value === "number" && value >= 0 && value <= 1 });
const contribution = fields({ revisionId: identifier, contributionId: identifier, contributorId: identifier, displayName: text,
  authorState: oneOf("active", "suspended"), kind: oneOf("scan", "manual", "derived"),
  state: oneOf("reserved", "object_stored", "published", "failed", "expired", "withdrawn", "quarantined"),
  createdAt: number, totalEvents: number, payloadBytes: number, imports: number, likes: number, dislikes: number, ranking });
const contributor = fields({ contributorId: identifier, displayName: text, state: oneOf("active", "suspended"), createdAt: number });
const key = fields({ keyId: identifier, installationId: identifier, state: oneOf("active", "retired", "revoked"), createdAt: number, retiredAt: nullable(number) });
const operation = fields({ contributorId: identifier, kind: text, operationId: identifier, state: oneOf("pending", "completed", "failed"), createdAt: number, updatedAt: number });
const auditFields = { actionId: identifier, actor: text, command: text, reason: text, outcome: oneOf("applied", "denied", "conflict"), expectedRevision: number, createdAt: number };
const audit = fields({ ...auditFields, resource: text });
const reportFields = { reportId: identifier, contributorId: identifier, revisionId: identifier,
  reason: oneOf("spam", "inaccurate", "privacy", "rights", "other"), note: nullable(text), createdAt: number };
const report = fields(reportFields);
const pageShape = (row: Check) => fields({ version, observedAt: number, source, items: array(row), nextCursor: nullable(text) });
const unavailable = fields({ source: literal("unavailable"), observedAt: literal(null), value: literal(null), providerVerified: no });
const activityResource = fields({ observed: nullable(number), observedAt: nullable(number) });
const overview = fields({ version, observedAt: number, period: text,
  storage: fields({ source: literal("artifact_storage_usage"), observedAt: number, providerVerified: no,
    globalBytes: number }),
  databaseSize: unavailable, providerUsage: unavailable, backup: unavailable,
  activity: fields({ source: literal("partial_application_observed"), providerVerified: no, restoreProvenance: literal("unknown"),
    resources: fields({ worker_requests: activityResource, d1_writes: activityResource }) }),
  observations: array(fields({ handler: oneOf("download", "import", "vote", "report"), source: literal("partial_handler_observed"), providerVerified: no,
    occurrences: nullable(number), reads: nullable(number), unknownReads: nullable(number), writes: nullable(number), unknownWrites: nullable(number), observedAt: nullable(number) }), 4) });
const usage = fields({ version, contributorId: identifier, observedAt: number, period: text,
  storage: fields({ source: literal("artifact_storage_usage"), observedAt: number, bytes: number }),
  observations: array(fields({ resource: oneOf("worker_requests", "d1_writes"), outcome: oneOf("allowed", "denied", "retried"),
    occurrences: number, units: number, observedAt: number }), 6) });

async function read<T>(path: string, check: Check, signal: AbortSignal, intent?: OperatorIntent): Promise<T> {
  const response = await fetch(path, { method: intent ? "POST" : "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: intent ? { Accept: "application/json", "Content-Type": "application/json", "X-Community-Operator": "1" } : { Accept: "application/json" },
    body: intent ? JSON.stringify(intent) : undefined, signal });
  if (!response.ok && (!intent || response.status === 401)) throw new OperatorAccessError(response.status);
  if (!response.headers.get("Content-Type")?.startsWith("application/json") || !response.body)
    throw new OperatorAccessError(response.status === 403 ? 403 : 503);
  const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0, body = "";
  let value: unknown;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.length;
      if (size > 131072) throw new OperatorAccessError(503);
      body += decoder.decode(chunk.value, { stream: true });
    }
    body += decoder.decode();
    value = JSON.parse(body);
  } catch { throw new OperatorAccessError(response.status === 403 ? 403 : 503); }
  finally { await reader.cancel(); reader.releaseLock(); }
  if (!response.ok) {
    if (fields({ error: fields({ code: text }) })(value))
      throw new OperatorMutationError(response.status, (value as { error: { code: string } }).error.code);
    throw new OperatorAccessError(response.status);
  }
  if (!check(value)) throw new OperatorAccessError(503);
  return value as T;
}

export const readOperatorOverview = (signal: AbortSignal) => read<OperatorOverview>("/admin/v1/dashboard/overview", overview, signal);
export function readOperatorPage(view: string, after: string | null, signal: AbortSignal, contributorId?: string): Promise<OperatorPage> {
  const row = { contributions: contribution, contributors: contributor, operations: operation, audit, keys: key }[view];
  if (view === "reports") return read<{ version: 1; reports: OperatorRow[]; nextCursor: string | null }>(
    `/admin/v1/reports${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    fields({ version, reports: array(report), nextCursor: nullable(identifier) }), signal)
    .then(value => ({ items: value.reports, nextCursor: value.nextCursor, observedAt: Date.now(), source: "service_database_received" }));
  if (!row || (view === "keys" && !identifier(contributorId))) throw new OperatorAccessError(400);
  const route = view === "keys" ? `contributors/${contributorId}/keys` : view;
  return read<OperatorPage>(`/admin/v1/dashboard/${route}${after ? `?after=${encodeURIComponent(after)}` : ""}`, pageShape(row), signal)
    .then(value => ({ ...value, items: value.items.map(item => {
      if (view !== "contributions") return item;
      const { ranking: score, ...safe } = item as unknown as Record<string, unknown>;
      return { ...safe, qualityScore: (score as { qualityScore: number }).qualityScore } as OperatorRow;
    }) }));
}
export function readOperatorInspection(kind: string, id: string, signal: AbortSignal, revisionId?: string) {
  if (!identifier(id)) throw new OperatorAccessError(400);
  const state = { contributor, report: fields({ ...reportFields, state: oneOf("open", "resolved") }),
    vote: fields({ contributorId: identifier, revisionId: identifier, value: oneOf("like", "dislike", "neutral"), excluded: value => value === 0 || value === 1 }),
    revision: fields({ revisionId: identifier, contributorId: identifier, state: text, authorState: oneOf("active", "suspended"), imports: number, likes: number, dislikes: number }) }[kind];
  if (!state || (kind === "vote" ? !identifier(revisionId) : revisionId !== undefined)) throw new OperatorAccessError(400);
  const inspectionContributor = fields({ contributorId: identifier, displayName: text, state: oneOf("active", "suspended") });
  return read<OperatorInspection>(`/admin/v1/moderation/${kind}/${id}${revisionId ? `/${revisionId}` : ""}`, fields({ version, resource: literal(`${kind}:${id}${revisionId ? `:${revisionId}` : ""}`),
    revision: number, state: kind === "contributor" ? inspectionContributor : state, audit: array(fields(auditFields)) }), signal);
}
export function readOperatorUsage(id: string, signal: AbortSignal) {
  if (!identifier(id)) throw new OperatorAccessError(400);
  return read<OperatorUsage>(`/admin/v1/dashboard/contributors/${id}/usage`, usage, signal);
}

export type OperatorCommand = "quarantine" | "restore" | "suspend" | "unsuspend" | "exclude_vote" | "restore_vote" | "resolve_report";
export interface OperatorIntent { version: 1; actionId: string; command: OperatorCommand; targetId: string;
  revisionId?: string; expectedRevision: number; reason: string }
export interface OperatorActionResult { version: 1; actionId: string; resource: string; revision: number; outcome: "applied" }
export class OperatorMutationError extends OperatorAccessError {
  constructor(status: number, public readonly code: string) { super(status); }
}
export function operatorActionResource(intent: OperatorIntent) {
  const kind = intent.command === "quarantine" || intent.command === "restore" ? "revision" :
    intent.command === "suspend" || intent.command === "unsuspend" ? "contributor" : intent.command === "resolve_report" ? "report" : "vote";
  return `${kind}:${intent.targetId}${kind === "vote" ? `:${intent.revisionId}` : ""}`;
}
export function writeOperatorAction(intent: OperatorIntent, signal: AbortSignal) {
  const voteCommand = intent.command === "exclude_vote" || intent.command === "restore_vote";
  if (intent.version !== 1 || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(intent.actionId) ||
    !oneOf("quarantine", "restore", "suspend", "unsuspend", "exclude_vote", "restore_vote", "resolve_report")(intent.command) ||
    !identifier(intent.targetId) || !number(intent.expectedRevision) || intent.expectedRevision === Number.MAX_SAFE_INTEGER ||
    !text(intent.reason) || !intent.reason.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(intent.reason) ||
    (voteCommand ? !identifier(intent.revisionId) : intent.revisionId !== undefined)) throw new OperatorAccessError(400);
  return read<OperatorActionResult>("/admin/v1/moderation", fields({ version, actionId: literal(intent.actionId),
    resource: literal(operatorActionResource(intent)), revision: literal(intent.expectedRevision + 1), outcome: literal("applied") }), signal, intent);
}