import { apiRequest, jsonApi, responseError } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import type { ApiKeySecret, ApiKeyStatus } from "./api-key";

export interface AccountSummary {
  id: number;
  username: string | null;
  display_name: string | null;
  source: "local" | "jellyfin";
  role: "owner" | "admin" | "user";
  status: "active" | "disabled";
  /** Why a disabled account is disabled; `unlinked` accounts are enabled only by linking them to Jellyfin. */
  disable_cause: "local" | "upstream_disabled" | "upstream_deleted" | "unlinked" | null;
  last_login_at: string | null;
  created_at: string;
  avatar: string | null;
  overrides: { permissions: boolean; media_access: boolean; filter_policy: boolean };
}

export interface ProfileValues {
  display_name: string | null;
  email: string | null;
  timezone: string | null;
  language: string | null;
}

/** Old export removal after the account's latest link. Counts only; no paths. */
export interface LinkCleanup { state: "removing" | "completed"; pending: number; removed: number; absent: number; kept: number }

export interface AccountDetail extends AccountSummary, Omit<ProfileValues, "display_name"> {
  read_only_fields: ("username" | "display_name" | "avatar")[];
  available_languages: string[];
  link_cleanup: LinkCleanup | null;
  /** Why the server would refuse to make this account the owner now, or null. */
  successor_refusal: "successor_disabled" | "successor_credentials_required" | null;
}

export interface AccountPage { total: number; offset: number; limit: number; items: AccountSummary[] }
export interface BulkAccountResult { user_id: number; outcome: "applied" | "refused"; code: string | null }
export type AccountStatus = AccountSummary["status"];

export function usersRequest<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  return jsonApi<T>(`${runtimeApiBasePath()}${path}`, {
    method, cache: "no-store", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  }, "Unable to update accounts.");
}

export function listAccounts(search: string, offset: number, signal?: AbortSignal): Promise<AccountPage> {
  const query = new URLSearchParams({ search, offset: String(offset), limit: "20" });
  return usersRequest(`/users?${query}`, "GET", undefined, signal);
}
export const readAccount = (id: number, signal?: AbortSignal) => usersRequest<AccountDetail>(`/users/${id}`, "GET", undefined, signal);
export const editAccount = (id: number, body: Partial<ProfileValues>) => usersRequest<AccountDetail>(`/users/${id}`, "PATCH", body);
export const setAccountStatus = (id: number, status: AccountStatus) => usersRequest<AccountDetail>(`/users/${id}/status`, "PUT", { status });
export const revokeAccountSessions = (id: number) => usersRequest<{ sessions_revoked: number }>(`/users/${id}/sessions`, "DELETE");
export const bulkAccountStatus = (user_ids: number[], status: AccountStatus) => usersRequest<{ results: BulkAccountResult[] }>("/users/bulk", "POST", { action: status === "active" ? "enable" : "disable", user_ids });
export const accountAvatarUrl = (account: Pick<AccountSummary, "id" | "avatar">) => `${runtimeApiBasePath()}/users/${account.id}/avatar?v=${encodeURIComponent(account.avatar ?? "")}`;

export async function uploadAccountAvatar(id: number, image: File): Promise<AccountDetail> {
  const response = await apiRequest(`${runtimeApiBasePath()}/users/${id}/avatar`, {
    method: "PUT", cache: "no-store", headers: { "Content-Type": image.type || "application/octet-stream" }, body: image,
  });
  if (!response.ok) throw await responseError(response, "Unable to upload avatar.");
  return response.json();
}
export const removeAccountAvatar = (id: number) => usersRequest<AccountDetail>(`/users/${id}/avatar`, "DELETE");
export const revealAccountKey = (id: number, password: string) => usersRequest<ApiKeySecret>(`/users/${id}/api-key/reveal`, "POST", { reauthentication: { password } });
export const issueAccountKey = (id: number, password: string) => usersRequest<ApiKeySecret>(`/users/${id}/api-key`, "POST", { reauthentication: { password } });
export const revokeAccountKey = (id: number) => usersRequest<ApiKeyStatus>(`/users/${id}/api-key`, "DELETE");