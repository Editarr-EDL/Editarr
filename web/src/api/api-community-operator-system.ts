/** System reads and mutations: health, trends, maintenance runs and online backups. */
import { OperatorAccessError } from "../settings/community-operator/runtime";
import { operatorRequest } from "./api-community-operator";
import { array, boolean, details, fields, finite, identifier, literal, nullable, number, oneOf, text } from "./api-community-operator-checks";
import { envelope, listShape, type OperatorList } from "./api-community-operator-labels";

export interface MaintenanceRun { runId: string; kind: "retention" | "recompute_all" | "metrics"; startedAt: number; durationMs: number | null;
  outcome: "ok" | "batch_limit" | "skipped" | "failed"; detail: Record<string, unknown> }
export interface HealthCheck { id: string; label: string; ok: boolean; detail: string }
export interface OperatorHealth { version: 1; observedAt: number; ready: boolean; checks: HealthCheck[];
  database: { bytes: number | null; walBytes: number | null; pageSize: number | null; lastCheckpointAt?: number | null };
  storage: { payloadBytes: number; quotaBytes: number | null }; lastRetention: MaintenanceRun | null; lastRecomputeAll: MaintenanceRun | null }
export type TrendMetric = "db" | "storage" | "denials" | "requests" | "uploads_votes" | "downloads";
export type TrendPeriod = "24h" | "7d" | "30d" | "90d";
export const trendMetrics: TrendMetric[] = ["db", "storage", "denials", "requests", "uploads_votes", "downloads"];
export interface Trend { version: 1; observedAt: number; metric: TrendMetric; period: TrendPeriod; unit: string;
  bucketMs: number; points: { t: number; v: number; v2?: number }[]; total: number | null; previousTotal: number | null;
  total2: number | null; previousTotal2: number | null; provenance: "service_database" | "application_observed" }
export type BackupFrequency = "hourly" | "every_6h" | "daily" | "weekly";
export interface BackupConfig { enabled: boolean; frequency: BackupFrequency; atUtc: string; keepLast: number; keepWeekly: number;
  destination: string; integrityCheck: boolean }
export interface BackupDestination { configured: boolean; writable: boolean; freeBytes: number | null; root: string | null;
  path: string | null; error: string | null }
export interface BackupRun { runId: string; startedAt: number; trigger: "scheduled" | "manual"; actor: string | null; fileName: string | null;
  bytes: number | null; durationMs: number | null; integrity: "ok" | "failed" | "skipped" | null; sha256: string | null;
  status: "running" | "verified" | "failed" | "pruned"; errorCode: string | null; note: string | null }
export interface OperatorBackups { version: 1; observedAt: number; config: BackupConfig & { updatedAt: number; updatedBy: string | null };
  destination: BackupDestination; runs: BackupRun[]; next: { at: number } | null; running: boolean }
export interface StagedBackup { version: 1; observedAt: number; runId: string; stagedPath: string; sha256: string; bytes: number;
  schemaVersion: number; restoreVolume: string; commands: string[] }

export const maintenanceRun = fields({ runId: text, kind: oneOf("retention", "recompute_all", "metrics"), startedAt: number,
  durationMs: nullable(number), outcome: oneOf("ok", "batch_limit", "skipped", "failed"), detail: details });
/** Health body fields (also embedded in the home response without the envelope). */
export const healthFields = { ready: boolean, checks: array(fields({ id: text, label: text, ok: boolean, detail: text }), 30),
  database: fields({ bytes: nullable(number), walBytes: nullable(number), pageSize: nullable(number) }, { lastCheckpointAt: nullable(number) }),
  storage: fields({ payloadBytes: number, quotaBytes: nullable(number) }),
  lastRetention: nullable(maintenanceRun), lastRecomputeAll: nullable(maintenanceRun) };
const health = fields({ ...envelope, ...healthFields });
const trend = (metric: TrendMetric, period: TrendPeriod) => fields({ ...envelope, metric: literal(metric), period: literal(period), unit: text,
  bucketMs: number, points: array(fields({ t: number, v: finite }, { v2: finite }), 2200), total: nullable(finite), previousTotal: nullable(finite),
  total2: nullable(finite), previousTotal2: nullable(finite), provenance: oneOf("service_database", "application_observed") });
const frequency = oneOf("hourly", "every_6h", "daily", "weekly");
const atUtc = (value: unknown) => typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
const configFields = { enabled: boolean, frequency, atUtc, keepLast: number, keepWeekly: number, destination: text, integrityCheck: boolean };
const destination = fields({ configured: boolean, writable: boolean, freeBytes: nullable(number), root: nullable(text), path: nullable(text), error: nullable(text) });
const run = fields({ runId: identifier, startedAt: number, trigger: oneOf("scheduled", "manual"), actor: nullable(text), fileName: nullable(text),
  bytes: nullable(number), durationMs: nullable(number), integrity: nullable(oneOf("ok", "failed", "skipped")), sha256: nullable(text),
  status: oneOf("running", "verified", "failed", "pruned"), errorCode: nullable(text), note: nullable(text) });
const backups = fields({ ...envelope, config: fields({ ...configFields, updatedAt: number, updatedBy: nullable(text) }), destination,
  runs: array(run, 20), next: nullable(fields({ at: number })), running: boolean });
const big = { maxBytes: 524288 };

export const readOperatorHealth = (signal: AbortSignal) => operatorRequest<OperatorHealth>("/admin/v1/health", health, signal);
export function readOperatorTrend(metric: TrendMetric, period: TrendPeriod, signal: AbortSignal) {
  if (!trendMetrics.includes(metric) || !["24h", "7d", "30d", "90d"].includes(period)) throw new OperatorAccessError(400);
  return operatorRequest<Trend>(`/admin/v1/trends?metric=${metric}&period=${period}`, trend(metric, period), signal, undefined, big);
}
export function readOperatorMaintenanceRuns(kind: "retention" | "recompute_all", after: string | null, signal: AbortSignal) {
  if (!["retention", "recompute_all"].includes(kind)) throw new OperatorAccessError(400);
  const params = new URLSearchParams({ kind, ...(after ? { after } : {}) });
  return operatorRequest<OperatorList<MaintenanceRun>>(`/admin/v1/maintenance/runs?${params}`, listShape(maintenanceRun, 100), signal);
}
export const readOperatorBackups = (signal: AbortSignal) => operatorRequest<OperatorBackups>("/admin/v1/backups", backups, signal, undefined, big);

/** Validate and save the backup schedule; the service also resolves `destination` inside its configured root. */
export function writeOperatorBackupConfig(config: BackupConfig, signal: AbortSignal) {
  if (!fields(configFields)(config) || config.keepLast < 1 || config.keepLast > 365 || config.keepWeekly > 104 ||
    config.destination.includes("\0")) throw new OperatorAccessError(400);
  const body = { enabled: config.enabled, frequency: config.frequency, atUtc: config.atUtc, keepLast: config.keepLast,
    keepWeekly: config.keepWeekly, destination: config.destination, integrityCheck: config.integrityCheck };
  return operatorRequest<OperatorBackups>("/admin/v1/backups/config", backups, signal, body, { method: "PUT", ...big });
}
export const writeOperatorBackupRun = (signal: AbortSignal) => operatorRequest<{ version: 1; observedAt: number; runId: string; status: "running" }>(
  "/admin/v1/backups/run", fields({ ...envelope, runId: identifier, status: literal("running") }), signal, {});
export function writeOperatorBackupTest(destinationPath: string | null, signal: AbortSignal) {
  if (destinationPath !== null && (destinationPath.length > 1000 || destinationPath.includes("\0"))) throw new OperatorAccessError(400);
  return operatorRequest<{ version: 1; observedAt: number; destination: BackupDestination }>("/admin/v1/backups/test-destination",
    fields({ ...envelope, destination }), signal, destinationPath === null ? {} : { destination: destinationPath });
}
export function writeOperatorBackupStage(runId: string, signal: AbortSignal) {
  if (!identifier(runId)) throw new OperatorAccessError(400);
  return operatorRequest<StagedBackup>(`/admin/v1/backups/${runId}/stage`, fields({ ...envelope, runId: literal(runId), stagedPath: text,
    sha256: text, bytes: number, schemaVersion: number, restoreVolume: text, commands: array(text, 20) }), signal, {});
}
/** Same-origin download link for a verified backup file. */
export const backupDownloadHref = (runId: string) => identifier(runId) ? `/admin/v1/backups/${runId}/download` : null;
