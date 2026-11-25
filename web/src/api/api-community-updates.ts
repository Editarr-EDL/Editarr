import { communityJson, postJson } from "./api-community-masters";
import type { CheckResult, SyncStatus } from "./api-community-master-types";

/** Ask for the daily sync. The server runs it only when due and otherwise returns the status with `ran: false`. */
export const runCommunitySync = (signal?: AbortSignal) => communityJson<SyncStatus>("sync/run", { ...postJson({}), signal });
export const fetchCommunitySync = (signal?: AbortSignal) => communityJson<SyncStatus>("sync", { signal });

export const markSyncNotificationsRead = (ids: string[] | "all", signal?: AbortSignal) =>
  communityJson<void>("sync/notifications/read",
    { ...postJson(ids === "all" ? { all: true } : { notification_ids: ids.slice(0, 100) }), signal });

/** On-demand single-version check: downloads and applies a newer revision without using the daily allowance. */
export const checkMasterUpdates = (linkId: string, signal?: AbortSignal) =>
  communityJson<CheckResult>(`masters/${encodeURIComponent(linkId)}/check`, { ...postJson({}), signal });
