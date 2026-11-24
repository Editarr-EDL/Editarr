import { OperatorAccessError } from "../settings/community-operator/runtime";
import { aliasIdentifier, array, attestationIdentifier, fields, flag, fraction, identifier, integer, literal, nullable, number, oneOf,
  text, type Check } from "./api-community-operator-checks";

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
const version = literal(1), no = literal(false), source = literal("service_database");
const contributor = fields({ contributorId: identifier, displayName: text, state: oneOf("active", "suspended"), createdAt: number });
const key = fields({ keyId: identifier, installationId: identifier, state: oneOf("active", "retired", "revoked"), createdAt: number, retiredAt: nullable(number) });
const operation = fields({ contributorId: identifier, kind: text, operationId: identifier, state: oneOf("pending", "completed", "failed"), createdAt: number, updatedAt: number });
const auditFields = { actionId: identifier, actor: text, command: text, reason: text, outcome: oneOf("applied", "denied", "conflict"), expectedRevision: number, createdAt: number };
const audit = fields({ ...auditFields, resource: text });
const reportFields = { reportId: identifier, contributorId: identifier, targetType: oneOf("filter", "version"), targetId: identifier,
  reason: oneOf("spam", "inaccurate", "privacy", "rights", "other"), note: nullable(text), createdAt: number };
const versionStatus = oneOf("confirmed", "candidate", "merged_into"), suggestionState = oneOf("pending", "approved", "rejected");
const versionFields = { versionId: identifier, workId: identifier, status: versionStatus, aliasId: nullable(aliasIdentifier),
  labelText: nullable(text), mergedInto: nullable(identifier), runtimeMs: nullable(number), trust: fraction, revision: number,
  quarantined: flag, createdAt: number };
const suggestion = fields({ suggestionId: identifier, normalized: text, text, submissionId: identifier, state: suggestionState,
  aliasId: nullable(aliasIdentifier), createdAt: number });
const filterRow = fields({ filterId: identifier, state: oneOf("active", "retired", "quarantined"), quarantined: flag,
  category: nullable(text), subcategory: nullable(text), severity: nullable(oneOf("low", "medium", "high")), action: nullable(oneOf("skip", "mute")),
  startMs: nullable(integer), endMs: nullable(integer), confidence: nullable(fraction), contributorCount: nullable(number),
  attestations: nullable(number), upvotes: nullable(number), downvotes: nullable(number), disputes: nullable(number), served: nullable(flag) });
const submissionState = oneOf("counted", "superseded", "quarantined");
const submissionRow = fields({ submissionId: identifier, contributorId: identifier, state: submissionState, tier: nullable(number),
  quarantined: flag, createdAt: number });
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

/** Fetch one bounded operator JSON response and validate its exact shape; `intent` makes it a CSRF-guarded POST. */
export async function operatorRequest<T>(path: string, check: Check, signal: AbortSignal, intent?: object): Promise<T> {
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

export class OperatorMutationError extends OperatorAccessError {
  constructor(status: number, public readonly code: string) { super(status); }
}
const read = operatorRequest;
const query = (values: Record<string, string | null | undefined>) => {
  const params = new URLSearchParams(Object.entries(values).filter((entry): entry is [string, string] => !!entry[1]));
  return params.size ? `?${params}` : "";
};

export const readOperatorOverview = (signal: AbortSignal) => read<OperatorOverview>("/admin/v1/dashboard/overview", overview, signal);
export type OperatorList = "versions" | "aliases" | "reports" | "contributors" | "operations" | "audit" | "keys" | "filters" | "submissions";
const catalogLists: Partial<Record<OperatorList, { path: string; key: string; row: Check; filter: string; values: Check }>> = {
  versions: { path: "/admin/v1/versions", key: "versions", filter: "status", values: versionStatus,
    row: fields({ ...versionFields, contributorCount: number, filterCount: number, servedFilterCount: number }) },
  aliases: { path: "/admin/v1/alias-suggestions", key: "suggestions", filter: "state", values: suggestionState, row: suggestion },
  reports: { path: "/admin/v1/reports", key: "reports", filter: "", values: () => false, row: fields(reportFields) },
};
/**
 * Read one bounded list page. `scope` is the contributor ID for `keys` and the version ID for
 * `filters` and `submissions`; `filter` is the version status or suggestion state.
 */
export function readOperatorPage(view: OperatorList, after: string | null, signal: AbortSignal, scope?: string, filter?: string | null): Promise<OperatorPage> {
  const catalog = catalogLists[view];
  if (catalog) {
    if (filter && !catalog.values(filter)) throw new OperatorAccessError(400);
    return read<Record<string, unknown> & { nextCursor: string | null }>(
      `${catalog.path}${query({ [catalog.filter]: filter, after })}`,
      fields({ version, [catalog.key]: array(catalog.row), nextCursor: nullable(identifier) }), signal)
      .then(value => ({ items: value[catalog.key] as OperatorRow[], nextCursor: value.nextCursor, observedAt: Date.now(), source: "service_database_received" }));
  }
  const row = { contributors: contributor, operations: operation, audit, keys: key, filters: filterRow, submissions: submissionRow }[view as string];
  if (!row || (["keys", "filters", "submissions"].includes(view) && !identifier(scope))) throw new OperatorAccessError(400);
  const route = view === "keys" ? `contributors/${scope}/keys` : view === "filters" || view === "submissions" ? `versions/${scope}/${view}` : view;
  return read<OperatorPage>(`/admin/v1/dashboard/${route}${query({ after })}`, pageShape(row), signal);
}

export type OperatorResource = "filter" | "attestation" | "submission" | "contributor" | "version" | "vote" | "report" | "alias_suggestion";
const inspectionStates: Record<OperatorResource, Check> = {
  contributor: fields({ contributorId: identifier, displayName: text, state: oneOf("active", "suspended"), quarantined: flag }),
  report: fields({ ...reportFields, state: oneOf("open", "resolved") }),
  vote: fields({ filterId: identifier, contributorId: identifier, vote: oneOf("up", "down"), excluded: flag, updatedAt: number }),
  version: fields({ ...versionFields, referenceSubmissionId: nullable(identifier) }),
  alias_suggestion: suggestion,
  filter: fields({ filterId: identifier, versionId: identifier, state: oneOf("active", "retired", "quarantined"), mergedInto: nullable(identifier),
    quarantined: flag, served: nullable(flag), confidence: nullable(fraction), contributorCount: nullable(number) }),
  attestation: fields({ submissionId: identifier, entryId: identifier, kind: oneOf("support", "dispute", "coverage_negative"), category: text,
    startMs: number, endMs: number, weight: flag, ignoredReason: nullable(text), quarantined: flag, filterId: nullable(identifier) }),
  submission: fields({ submissionId: identifier, contributorId: identifier, versionId: identifier, digest: text, state: submissionState,
    tier: nullable(number), quarantined: flag, createdAt: number }),
};
export const operatorResources = Object.keys(inspectionStates) as OperatorResource[];
/** The moderation resource name: `vote:<filterId>:<contributorId>` for votes, otherwise `<kind>:<id>`. */
export const operatorResourceName = (kind: OperatorResource, id: string, contributorId?: string) =>
  kind === "vote" ? `vote:${id}:${contributorId}` : `${kind}:${id}`;
/** Inspect one resource; votes are addressed by filter ID plus `contributorId`, attestations by `submissionId:entryId`. */
export function readOperatorInspection(kind: OperatorResource, id: string, signal: AbortSignal, contributorId?: string) {
  const state = inspectionStates[kind];
  if (!state || !(kind === "attestation" ? attestationIdentifier : identifier)(id) ||
    (kind === "vote" ? !identifier(contributorId) : contributorId !== undefined)) throw new OperatorAccessError(400);
  return read<OperatorInspection>(`/admin/v1/moderation/${kind}/${encodeURIComponent(id)}${contributorId ? `/${contributorId}` : ""}`,
    fields({ version, resource: literal(operatorResourceName(kind, id, contributorId)), revision: number, state,
      audit: array(fields(auditFields, { errorCode: nullable(text) })) }), signal);
}
export function readOperatorUsage(id: string, signal: AbortSignal) {
  if (!identifier(id)) throw new OperatorAccessError(400);
  return read<OperatorUsage>(`/admin/v1/dashboard/contributors/${id}/usage`, usage, signal);
}
