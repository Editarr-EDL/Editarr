import type { FilterSelectionRule, McfCategory, Severity } from "./api-types";
import { usersRequest, type AccountDetail, type BulkAccountResult } from "./api-users";
import { ApiError } from "./api-transport";

export type AssignedRole = "admin" | "user";
export type Override = "inherit" | "allow" | "deny";
export interface PermissionView {
  role: AssignedRole; permission_revision: number;
  overrides: Record<string, "allow" | "deny">; effective: string[];
}
export interface RoleDefault { capability: string; granted: boolean; catalog_default: boolean; explicit: boolean }
export type RoleDefaults = Record<AssignedRole, RoleDefault[]>;
export interface MediaSource { kind: "jellyfin_library"; id: string }
export interface MediaScope { inherited: boolean; all_sources: boolean; sources: MediaSource[] }
export interface ScopeChoice { inherit?: boolean; all_sources?: boolean; sources?: MediaSource[] }
export interface UserMediaAccess { linked: boolean; scope: MediaScope | null }
export type FilterMode = "live" | "copy";
export interface FilterPreferences {
  user_id: number; mode: FilterMode; mode_locked: boolean;
  revision: number; policy_revision: number; instance_revision: number;
  rules: { rule: FilterSelectionRule; origin: "instance" | "personal" | "copy"; edited: boolean; disabled: boolean; enforced: boolean }[];
  severities: { subcategory: McfCategory; severity: Severity; origin: "instance" | "override" | "copy"; enforced: boolean }[];
  enforced_rules: string[]; enforced_severities: McfCategory[];
}
const proof = (password: string) => ({ reauthentication: { password } });
type Watch = { id: number; denied: () => void };
const watches = new Set<Watch>();
export function watchAccountAccess(id: number, denied: () => void) {
  const watch = { id, denied }; watches.add(watch);
  return () => { watches.delete(watch); };
}
async function guardedAccountRequest<T>(id: number, path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const started = [...watches].filter(watch => watch.id === id);
  try { return await usersRequest<T>(path, method, body, signal); }
  catch (error) {
    if (error instanceof ApiError && ["protected_account", "not_found", "capability_denied"].includes(error.code ?? "")) {
      for (const watch of started) if (watches.has(watch)) watch.denied();
    }
    throw error;
  }
}
function accountRequest<T>(id: number, suffix: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  return guardedAccountRequest(id, `/users/${id}${suffix}`, method, body, signal);
}
export const readPermissions = (id: number, signal?: AbortSignal) => accountRequest<PermissionView>(id, "/permissions", "GET", undefined, signal);
export const savePermissions = (id: number, overrides: Record<string, Override>, password: string) => accountRequest<PermissionView>(id, "/permissions", "PATCH", { overrides, ...proof(password) });
export const assignAccountRole = (id: number, role: AssignedRole, password: string) => accountRequest<AccountDetail>(id, "/role", "PUT", { role, ...proof(password) });
export const readRoleDefaults = (signal?: AbortSignal) => usersRequest<RoleDefaults>("/roles/defaults", "GET", undefined, signal);
export const saveRoleDefault = (role: AssignedRole, capability: string, granted: boolean | null, password: string) => usersRequest<RoleDefaults>(`/roles/${role}/defaults`, "PATCH", { defaults: { [capability]: granted }, ...proof(password) });
export const readMediaDefault = (signal?: AbortSignal) => usersRequest<MediaScope>("/media-access/default", "GET", undefined, signal);
export const saveMediaDefault = (choice: ScopeChoice, password: string) => usersRequest<MediaScope>("/media-access/default", "PUT", { ...choice, ...proof(password) });
export const readUserMedia = (id: number, signal?: AbortSignal) => accountRequest<UserMediaAccess>(id, "/media-access", "GET", undefined, signal);
export const saveUserMedia = (id: number, choice: ScopeChoice, password: string) => accountRequest<UserMediaAccess>(id, "/media-access", "PUT", { ...choice, ...proof(password) });
export const readFilterPreferences = (id: number, signal?: AbortSignal) => guardedAccountRequest<FilterPreferences>(id, `/settings/filters/users/${id}`, "GET", undefined, signal);
export const saveFilterMode = (id: number, revision: number, mode: FilterMode) => guardedAccountRequest<FilterPreferences>(id, `/settings/filters/users/${id}/mode`, "PUT", { revision, mode });
export const saveFilterPolicy = (id: number, policy_revision: number, rules: string[], severities: McfCategory[], password: string) => guardedAccountRequest<FilterPreferences>(id, `/settings/filters/users/${id}/policy`, "PUT", { policy_revision, rules, severities, ...proof(password) });
export const readDefaultFilterMode = (signal?: AbortSignal) => usersRequest<{ mode: FilterMode }>("/settings/filters/default-mode", "GET", undefined, signal);
export const saveDefaultFilterMode = (mode: FilterMode) => usersRequest<{ mode: FilterMode }>("/settings/filters/default-mode", "PUT", { mode });
export const transferOwnership = (id: number, password: string) => accountRequest<{ owner_id: number; former_owner_id: number; former_owner_role: "admin" }>(id, "/ownership", "POST", proof(password));
export const bulkRoles = (user_ids: number[], role: AssignedRole, password: string) => usersRequest<{ results: BulkAccountResult[] }>("/users/bulk", "POST", { action: "set_role", user_ids, role, ...proof(password) });
export const bulkMedia = (user_ids: number[], media_access: ScopeChoice, password: string) => usersRequest<{ results: BulkAccountResult[] }>("/users/bulk", "POST", { action: "set_media_access", user_ids, media_access, ...proof(password) });
