import { communityJson, postJson } from "./api-community-masters";
import { approveSharingPreview, type SharingSource } from "./api-community-sharing";
import type {
  CheckResult, MasterSharingDraft, MasterSharingPreview, MasterUpdate, SyncStatus, UpdateDecision, UpdateGroup,
} from "./api-community-master-types";

const update = (linkId: string) => `masters/${encodeURIComponent(linkId)}/update`;

/** Ask for the daily sync. The server runs it only when due and otherwise returns the status with `ran: false`. */
export const runCommunitySync = (signal?: AbortSignal) => communityJson<SyncStatus>("sync/run", { ...postJson({}), signal });
export const fetchCommunitySync = (signal?: AbortSignal) => communityJson<SyncStatus>("sync", { signal });

export const markSyncNotificationsRead = (ids: string[] | "all", signal?: AbortSignal) =>
  communityJson<Record<string, unknown>>("sync/notifications/read",
    { ...postJson(ids === "all" ? { all: true } : { notification_ids: ids.slice(0, 100) }), signal });

/** On-demand single-version check; it never applies the update and doesn't use the daily allowance. */
export const checkMasterUpdates = (linkId: string, signal?: AbortSignal) =>
  communityJson<CheckResult>(`masters/${encodeURIComponent(linkId)}/check`, { ...postJson({}), signal });

export const fetchMasterUpdate = (linkId: string, signal?: AbortSignal) => communityJson<MasterUpdate>(update(linkId), { signal });

export const decideMasterUpdate = (linkId: string, toRevision: number,
  decisions: { rowId: string; decision: UpdateDecision }[], signal?: AbortSignal) =>
  communityJson<Record<string, unknown>>(`${update(linkId)}/decisions`, { ...postJson({
    to_revision: toRevision, decisions: decisions.map(({ rowId, decision }) => ({ row_id: rowId, decision })),
  }), signal });

/** Accept every untouched, acceptable row in a group and return the remaining update. */
export const acceptAllMasterUpdate = (linkId: string, toRevision: number, group: UpdateGroup, signal?: AbortSignal) =>
  communityJson<MasterUpdate>(`${update(linkId)}/accept-all`, { ...postJson({ to_revision: toRevision, group }), signal });

const sourcePath = (source: SharingSource) => `sharing/sources/${source.kind}/${encodeURIComponent(source.id)}`;

/** Build a master-set sharing preview (community-artifact-3) with estimated counts. */
export const prepareMasterSharingPreview = (source: SharingSource, draft: MasterSharingDraft, signal?: AbortSignal) =>
  communityJson<MasterSharingPreview>(`${sourcePath(source)}/preview`, { ...postJson(draft), signal });

/** Approval is unchanged from manual sharing and queues a `submission` outbox operation. */
export const approveMasterSharingPreview = approveSharingPreview;
