import { runtimeApiBasePath } from "../shared/runtime-urls";
import { jsonApi } from "./api-transport";

export const auditCategories = ["security", "export", "community", "settings"] as const;
export const auditOutcomes = ["succeeded", "denied", "failed"] as const;
export type AuditCategory = typeof auditCategories[number];
export type AuditOutcome = typeof auditOutcomes[number];

export interface AuditEntry {
  event_id: string;
  occurred_at: string;
  category: AuditCategory;
  action: string;
  outcome: AuditOutcome;
  actor_kind: "user" | "system" | "anonymous" | "unattributed";
  actor_id: number | null;
  credential: "session" | "api_key" | null;
  target_user_id: number | null;
  media_key: string | null;
  resource: string | null;
  details: Record<string, string | number | boolean | null>;
}

export interface AuditPage {
  events: AuditEntry[];
  total: number;
  pages: number;
  page: number;
  page_size: number;
  retained_since: string;
  retention_days: number;
}

export interface AuditFilters {
  page: number;
  page_size: number;
  category?: AuditCategory;
  outcome?: AuditOutcome;
  action?: string;
  actor_id?: number;
  target_user_id?: number;
  media_key?: string;
  since?: string;
  until?: string;
}

export function fetchAuditPage(filters: AuditFilters, signal: AbortSignal): Promise<AuditPage> {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") query.set(name, String(value));
  }
  return jsonApi(`${runtimeApiBasePath()}/audit?${query}`, { signal, cache: "no-store" }, "Unable to load audit history.");
}

export function fetchAuditEntry(id: string, signal: AbortSignal): Promise<AuditEntry> {
  return jsonApi(`${runtimeApiBasePath()}/audit/${encodeURIComponent(id)}`, { signal, cache: "no-store" }, "Unable to load audit event.");
}
