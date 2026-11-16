import { usersRequest } from "./api-users";

export interface JellyfinCandidate {
  jellyfin_user_id: string;
  name: string;
  is_administrator: boolean;
  is_disabled: boolean;
  user_id: number | null;
  blocked: boolean;
}
export interface JellyfinImportResult {
  jellyfin_user_id: string;
  name: string | null;
  outcome: "imported" | "existing" | "upstream_disabled" | "blocked" | "not_found";
  user_id: number | null;
}
export interface JellyfinSyncResult {
  user_id: number; outcome: string; link_state: string; role: string; status: string; failure: string | null;
}
export interface JellyfinSyncRun {
  trigger: string; started_at: string; finished_at: string; counts: Record<string, number>; results: JellyfinSyncResult[];
}
export interface JellyfinLinkedStatus {
  user_id: number; jellyfin_user_id: string; name: string | null;
  link_state: string; upstream_state: string | null;
  last_verified_at: string | null; grace_expires_at: string | null;
  last_attempt_at: string | null; last_outcome: string | null; last_failure: string | null;
}
export interface JellyfinSyncStatus { running: boolean; last_run: JellyfinSyncRun | null; accounts: JellyfinLinkedStatus[] }

export const listJellyfinAccounts = (signal?: AbortSignal) => usersRequest<{ users: JellyfinCandidate[] }>("/jellyfin/users", "GET", undefined, signal);
export const importJellyfinAccounts = (selection: { all: true } | { user_ids: string[] }) => usersRequest<{ results: JellyfinImportResult[] }>("/jellyfin/users/import", "POST", selection);
export const readJellyfinSync = (signal?: AbortSignal) => usersRequest<JellyfinSyncStatus>("/jellyfin/sync", "GET", undefined, signal);
export const runJellyfinSync = () => usersRequest<JellyfinSyncRun>("/jellyfin/sync", "POST");

/** A Jellyfin user kept out when their account was deleted; `name` is Jellyfin's name for them then. */
export interface BlockedJellyfinUser { server_id: string; jellyfin_user_id: string; name: string | null; blocked_by: number | null; blocked_at: string }
export const listBlockedJellyfinUsers = (signal?: AbortSignal) => usersRequest<{ blocks: BlockedJellyfinUser[] }>("/jellyfin/blocks", "GET", undefined, signal);
export const unblockJellyfinUser = (block: BlockedJellyfinUser, password: string) => usersRequest<{ blocks: BlockedJellyfinUser[] }>("/jellyfin/blocks/unblock", "POST", { server_id: block.server_id, jellyfin_user_id: block.jellyfin_user_id, reauthentication: { password } });
