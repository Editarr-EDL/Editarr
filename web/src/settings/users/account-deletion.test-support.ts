import { vi } from "vitest";
import type { AccountDeletionPreview, AccountDeletionResult } from "../../api/api-account-deletion";
import type { ExportPublication } from "../../api/api-mcf-types";
import type { AccountSummary } from "../../api/api-users";

export const publication = (id: number, overrides: Partial<ExportPublication> = {}): ExportPublication => ({
  id, publication_id: `pub-${id}`, publication_revision: 1, media_item_id: "movie-1", media_source_id: "src-1",
  format: id % 2 ? "edl" : "mcf", scope: "personal", target: "self", target_user_id: 3, target_export_id: "local-3",
  actor_id: 3, policy_owner_id: 3, original_author_id: 3, policy_revision: 1, review_id: "review-1",
  exported_format_revision: 1, content_sha256: "c".repeat(64), current: true, outdated: false,
  regeneration_state: "idle", regenerating: false, failed: false, failure_reason: null, retryable: false, serving: true,
  desired_policy_revision: null, ...overrides,
});

export const deletionPreview = (overrides: Partial<AccountDeletionPreview> = {}): AccountDeletionPreview => ({
  user_id: 3, mode: "retain", destination_id: null, confirmation: "a".repeat(64),
  reviews: { count: 2, hidden: 0, ids: ["review-1", "review-2"] },
  files: [], scans: { hidden: 0, items: [] }, jobs: { waiting: [], running: [], hidden: 0 },
  personal_files: 2, publications: { hidden: 0, items: [publication(11), publication(12)] }, transfer: null,
  credentials: { sessions: 1, api_key: true, linked: false },
  community: { identity: true, operations: 3, pending: 1 }, blockers: [],
  ...overrides,
});

export const deletionResult = (overrides: Partial<AccountDeletionResult> = {}): AccountDeletionResult => ({
  user_id: 3, mode: "retain", destination_id: null, replayed: false,
  reviews_deleted: [], reviews_retained: ["review-1", "review-2"], reviews_transferred: [], reviews_hidden: 0,
  files_retained: [], files_removed: [], files_moved: 0, scans_deleted: [], scans_kept: [], scans_hidden: 0,
  jobs_stopped: [], jobs_hidden: 0, personal_files_kept: 2, community_operations_kept: 3, community_identity_kept: true,
  sessions_revoked: 1, api_key_revoked: true, identity_released: false, profile_removed: false, jellyfin_blocked: false,
  ...overrides,
});

export const summary = (id: number, name: string, overrides: Partial<AccountSummary> = {}): AccountSummary => ({
  id, username: null, display_name: name, source: "jellyfin", role: "user", status: "active", disable_cause: null,
  last_login_at: null, created_at: "2026-10-24T12:00:00Z", avatar: null,
  overrides: { permissions: false, media_access: false, filter_policy: false }, ...overrides,
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
export const refusal = (status: number, code: string) => ({ status, body: { detail: { code, message: code } } });

type Route = "preview" | "delete" | "bulk" | "list" | "account" | "sync";

/** A scripted HTTP boundary: each matching request takes the next queued answer and is recorded. */
export function deletionServer() {
  const answers: Record<Route, (Response | Error)[]> = { preview: [], delete: [], bulk: [], list: [], account: [], sync: [] };
  const requests: { route: Route; url: string; body: Record<string, unknown> | null }[] = [];
  const route = (url: string, method: string): Route | null => {
    const path = new URL(url, "http://editarr.test").pathname;
    if (path.endsWith("/users/bulk-deletion")) return "bulk";
    if (/\/users\/\d+\/deletion$/.test(path)) return method === "GET" ? "preview" : "delete";
    if (path.endsWith("/users")) return "list";
    if (/\/users\/\d+$/.test(path)) return "account";
    if (path.endsWith("/jellyfin/sync")) return "sync";
    return null;
  };
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const name = route(url, init?.method ?? "GET");
    if (!name) throw new Error(`Unexpected request ${url}`);
    requests.push({ route: name, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const next = answers[name].shift();
    if (!next) throw new Error(`Unanswered request ${url}`);
    if (next instanceof Error) throw next;
    return next;
  });
  vi.stubGlobal("fetch", fetch);
  return {
    requests,
    answer(name: Route, status: number, body: unknown) { answers[name].push(json(status, body)); },
    refuse(name: Route, status: number, code: string) { answers[name].push(json(status, refusal(status, code).body)); },
    /** The connection drops before any response, as `fetch` reports it. */
    disconnect(name: Route) { answers[name].push(new TypeError("Failed to fetch")); },
    accounts(items: AccountSummary[]) { answers.list.push(json(200, { total: items.length, offset: 0, limit: 20, items })); },
  };
}
