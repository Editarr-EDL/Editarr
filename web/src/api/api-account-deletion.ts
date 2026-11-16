import type { ExportPublication } from "./api-mcf-types";
import type { ReviewFileChoice } from "./api-review-types";
import { usersRequest } from "./api-users";

export type DeletionMode = "retain" | "transfer" | "delete";
/** What a linked account's Jellyfin user may do after deletion: sign in again to a fresh account, or nothing until unblocked. */
export type JellyfinReturn = "allow" | "block";

/** The chosen disposition. A transfer always names its destination account. */
export type Disposition =
  | { mode: "retain" }
  | { mode: "delete" }
  | { mode: "transfer"; destination: { id: number; name: string } };

export interface DeletionFile { review_id: string; publication: ExportPublication; choices: ReviewFileChoice[] }
export interface DeletionBlocker { code: string; count: number }

/** MU-38's consent. Only what the caller may see is named; the rest are `hidden` counts. */
export interface AccountDeletionPreview {
  user_id: number;
  mode: DeletionMode;
  destination_id: number | null;
  confirmation: string;
  reviews: { count: number; hidden: number; ids: string[] };
  files: DeletionFile[];
  scans: { hidden: number; items: { scan_id: string; status: string; required: boolean }[] };
  jobs: { waiting: string[]; running: string[]; hidden: number };
  personal_files: number;
  publications: { hidden: number; items: ExportPublication[] };
  transfer: { files_moved: number; policy_files: number } | null;
  credentials: { sessions: number; api_key: boolean; linked: boolean };
  community: { identity: boolean; operations: number; pending: number };
  blockers: DeletionBlocker[];
}

export interface AccountDeletionResult {
  user_id: number;
  mode: DeletionMode;
  destination_id: number | null;
  replayed: boolean;
  reviews_deleted: string[];
  reviews_retained: string[];
  reviews_transferred: string[];
  reviews_hidden: number;
  files_retained: number[];
  files_removed: number[];
  files_moved: number;
  scans_deleted: string[];
  scans_kept: string[];
  scans_hidden: number;
  jobs_stopped: string[];
  jobs_hidden: number;
  personal_files_kept: number;
  community_operations_kept: number;
  community_identity_kept: boolean;
  sessions_revoked: number;
  api_key_revoked: boolean;
  identity_released: boolean;
  profile_removed: boolean;
  jellyfin_blocked: boolean;
}

export type BulkDeletionOutcome =
  | { user_id: number; outcome: "applied"; code: null; result: AccountDeletionResult }
  | { user_id: number; outcome: "refused"; code: string; result: null };

export type FileChoices = Record<number, ReviewFileChoice>;

/** One target of a confirmed request: the disposition, the preview's confirmation, and every visible file's choice. */
export interface ConfirmedDeletion { userId: number; disposition: Disposition; preview: AccountDeletionPreview; choices: FileChoices; jellyfinUser: JellyfinReturn }

function body({ disposition, preview, choices, jellyfinUser }: ConfirmedDeletion) {
  return {
    mode: disposition.mode,
    jellyfin_user: jellyfinUser,
    confirmation: preview.confirmation,
    ...(disposition.mode === "transfer" ? { destination_id: disposition.destination.id } : {}),
    files: disposition.mode === "delete" ? preview.files.map(({ publication }) => ({ export_id: publication.id, choice: choices[publication.id] })) : [],
  };
}

export function previewAccountDeletion(userId: number, disposition: Disposition, signal?: AbortSignal): Promise<AccountDeletionPreview> {
  const query = new URLSearchParams({ mode: disposition.mode });
  if (disposition.mode === "transfer") query.set("destination", String(disposition.destination.id));
  return usersRequest(`/users/${userId}/deletion?${query}`, "GET", undefined, signal);
}

/** Apply exactly the reviewed preview. A changed account answers 409 `stale_account_deletion`. */
export function deleteAccount(target: ConfirmedDeletion, password: string): Promise<AccountDeletionResult> {
  return usersRequest(`/users/${target.userId}/deletion`, "POST", { ...body(target), reauthentication: { password } });
}

/** Apply each reviewed target alone, in order; every target reports `applied` or `refused`. */
export async function bulkDeleteAccounts(targets: ConfirmedDeletion[], password: string): Promise<BulkDeletionOutcome[]> {
  const response = await usersRequest<{ results: BulkDeletionOutcome[] }>("/users/bulk-deletion", "POST", {
    targets: targets.map(target => ({ user_id: target.userId, ...body(target) })),
    reauthentication: { password },
  });
  return response.results;
}
