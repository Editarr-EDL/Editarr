import { communityJson, postJson } from "./api-community-masters";
import type {
  CheckResult, MasterUpdate, SyncStatus, UpdateDecision, UpdateGroup,
} from "./api-community-master-types";

const update = (linkId: string) => `masters/${encodeURIComponent(linkId)}/update`;

/** Ask for the daily sync. The server runs it only when due and otherwise returns the status with `ran: false`. */
export const runCommunitySync = (signal?: AbortSignal) => communityJson<SyncStatus>("sync/run", { ...postJson({}), signal });
export const fetchCommunitySync = (signal?: AbortSignal) => communityJson<SyncStatus>("sync", { signal });

export const markSyncNotificationsRead = (ids: string[] | "all", signal?: AbortSignal) =>
  communityJson<void>("sync/notifications/read",
    { ...postJson(ids === "all" ? { all: true } : { notification_ids: ids.slice(0, 100) }), signal });

/** On-demand single-version check; it never applies the update and doesn't use the daily allowance. */
export const checkMasterUpdates = (linkId: string, signal?: AbortSignal) =>
  communityJson<CheckResult>(`masters/${encodeURIComponent(linkId)}/check`, { ...postJson({}), signal });

export const fetchMasterUpdate = (linkId: string, signal?: AbortSignal) => communityJson<MasterUpdate>(update(linkId), { signal });

export const decideMasterUpdate = (linkId: string, toRevision: number,
  decisions: { rowId: string; decision: UpdateDecision }[], signal?: AbortSignal) =>
  communityJson<void>(`${update(linkId)}/decisions`, { ...postJson({
    to_revision: toRevision, decisions: decisions.map(({ rowId, decision }) => ({ row_id: rowId, decision })),
  }), signal });

/** Accept every untouched, acceptable row in a group and return the remaining update. */
export const acceptAllMasterUpdate = (linkId: string, toRevision: number, group: UpdateGroup, signal?: AbortSignal) =>
  communityJson<MasterUpdate>(`${update(linkId)}/accept-all`, { ...postJson({ to_revision: toRevision, group }), signal });
