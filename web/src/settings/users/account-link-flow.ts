import { ApiError } from "../../api/api-transport";
import { LinkRefusal, type LinkResult, type ProvenLink } from "../../api/api-account-link";
import type { LinkCleanup } from "../../api/api-users";
import { freshAuthenticationError } from "../../auth/fresh-authentication";

/** What the dialog shows. Passwords never enter this state; the single-use proof token lives only here. */
export type LinkFlow =
  | { step: "prove"; notice: string | null }
  | { step: "review"; proven: ProvenLink; notice: string | null }
  | { step: "finished"; result: LinkResult }
  | { step: "stopped"; message: string };

export type LinkEvent =
  | { type: "proven"; proven: ProvenLink }
  | { type: "linked"; result: LinkResult }
  | { type: "refused"; reason: unknown }
  | { type: "restart"; notice?: string };

type Recovery = "prove" | "review" | "stop";
const NOTHING_CHANGED = "Nothing changed. The account keeps its identity and its existing exports.";

/** Each MU-35 refusal code and where the person goes next. Unknown codes stop without claiming an outcome. */
const REFUSALS: Record<string, { recovery: Recovery; message: string }> = {
  jellyfin_proof_failed: { recovery: "prove", message: "Jellyfin did not accept that username and password." },
  link_throttled: { recovery: "prove", message: "Too many failed proofs for this account. Wait a few minutes, then try again." },
  jellyfin_unavailable: { recovery: "prove", message: "Jellyfin did not answer. Check the server connection and try again." },
  link_proof_invalid: { recovery: "prove", message: "That proof is no longer valid. Prove both identities again." },
  link_proof_expired: { recovery: "prove", message: "The proof expired after ten minutes. Prove both identities again." },
  link_proof_stale: { recovery: "prove", message: "The account changed after it was proven. Prove both identities again." },
  link_preview_stale: { recovery: "review", message: "The impact changed. Review the current preview and confirm again." },
  link_blocked: { recovery: "review", message: "Linking is blocked. Resolve the blockers below first." },
  write_failed: { recovery: "stop", message: `A new export file could not be written. ${NOTHING_CHANGED} Fix the export location, then start again.` },
  export_changed: { recovery: "stop", message: `A personal export changed during linking. ${NOTHING_CHANGED} Start again to review the current exports.` },
  export_destination_changed: { recovery: "stop", message: `An export destination changed. ${NOTHING_CHANGED} Start again to review the current exports.` },
  export_target_unavailable: { recovery: "stop", message: `An export target is unavailable. ${NOTHING_CHANGED}` },
  export_recovery_required: { recovery: "stop", message: `An earlier export change still needs recovery. ${NOTHING_CHANGED} Editarr recovers it at startup and every hour; start again later.` },
  jellyfin_identity_in_use: { recovery: "stop", message: `Another Editarr account now holds this Jellyfin identity. ${NOTHING_CHANGED}` },
  jellyfin_user_blocked: { recovery: "stop", message: `This Jellyfin user was blocked while linking. ${NOTHING_CHANGED} The owner can unblock them in Users.` },
  jellyfin_user_unavailable: { recovery: "stop", message: `The Jellyfin user is disabled or no longer exists. ${NOTHING_CHANGED}` },
  account_disabled: { recovery: "stop", message: `The account is disabled. ${NOTHING_CHANGED}` },
};

/** A refusal's message, preferring the fresh-authentication wording for password checks. */
export function refusalMessage(reason: unknown): string {
  const known = reason instanceof ApiError && reason.code ? REFUSALS[reason.code] : undefined;
  return known?.message ?? freshAuthenticationError(reason, "Editarr did not confirm the link. Reopen the account to see its current state.");
}

/** Whether the fresh-authentication dialog should keep the attempt open for another password. */
export function isPasswordRefusal(reason: unknown): boolean {
  return reason instanceof ApiError && ["reauthentication_failed", "reauthentication_throttled", "reauthentication_required", "jellyfin_unavailable"].includes(reason.code ?? "");
}

export function linkFlow(state: LinkFlow, event: LinkEvent): LinkFlow {
  switch (event.type) {
    case "proven": return { step: "review", proven: event.proven, notice: null };
    case "linked": return { step: "finished", result: event.result };
    case "restart": return { step: "prove", notice: event.notice ?? null };
    case "refused": {
      const message = refusalMessage(event.reason);
      const code = event.reason instanceof ApiError ? event.reason.code ?? "" : "";
      const recovery = REFUSALS[code]?.recovery ?? (state.step === "prove" ? "prove" : "stop");
      if (recovery === "review" && state.step === "review" && event.reason instanceof LinkRefusal && event.reason.review) {
        return { step: "review", proven: { ...state.proven, ...event.reason.review }, notice: message };
      }
      if (recovery === "prove") return { step: "prove", notice: message };
      return { step: "stopped", message };
    }
  }
}

/** Plain-language explanations of MU-35 preview blockers. */
export const BLOCKERS: Record<string, string> = {
  already_linked: "The account is already linked to this Jellyfin server. Editarr has no unlink; a linked account stays linked.",
  jellyfin_identity_in_use: "Another Editarr account already uses this Jellyfin identity. Editarr never merges accounts. Delete one of the accounts or transfer its data explicitly, then link again.",
  jellyfin_user_disabled: "The Jellyfin user is disabled in Jellyfin.",
  jellyfin_user_blocked: "This Jellyfin user was blocked when their account was deleted. The owner can unblock them in Users.",
  account_disabled: "The Editarr account is disabled. Enable it before linking.",
  link_in_progress: "Another link of this account is still being finished. Try again shortly.",
  export_regeneration_pending: "A personal export is being regenerated. Wait for it to finish.",
  export_recovery_required: "A personal export needs recovery first. Editarr recovers it at startup and every hour.",
  export_filename_collision: "Another export already records the new file name.",
  export_file_collision: "A file already exists where a new export would be written. An operator must remove it first.",
  export_destination_changed: "A personal export's destination changed. Prove again to review it.",
  export_destination_unavailable: "A personal export's media folder cannot be reached.",
};

export const blockerText = (code: string) => BLOCKERS[code] ?? `Blocked: ${code}.`;

const files = (count: number) => `${count} old export ${count === 1 ? "file" : "files"}`;

/** The identity and export ID a committed link moved to, without any claim about old files. */
export function linkedMessage({ preview }: LinkResult): string {
  const moved = preview.exports.filter(move => move.action === "move").length;
  const ids = moved ? ` ${moved} personal export ${moved === 1 ? "file now uses" : "files now use"} export ID ${preview.export_id.after}.` : "";
  return `${preview.kind === "relink" ? "Relinked" : "Linked"} to Jellyfin user ${preview.profile.name.after}.${ids}`;
}

/** The link response's outcome. Old files are removed only when they still hold the bytes Editarr wrote. */
export function linkResultMessage(result: LinkResult): string {
  if (result.state === "removing") return `${linkedMessage(result)} Some old export files are not removed yet. Editarr retries their removal at startup and every hour.`;
  if (!result.preview.exports.some(move => move.action === "move")) return linkedMessage(result);
  return `${linkedMessage(result)} Old export files that still held what Editarr wrote were removed. Any other file at an old location was left in place.`;
}

/** What the account read says about the latest link's old export files. */
export function cleanupMessage({ state, pending, removed, absent, kept }: LinkCleanup): string {
  const parts = [
    state === "removing" && `Some old export files are not removed yet. ${pending} ${pending === 1 ? "is" : "are"} waiting; Editarr retries their removal at startup and every hour.`,
    removed > 0 && `${files(removed)} ${removed === 1 ? "was" : "were"} removed.`,
    absent > 0 && `${files(absent)} ${absent === 1 ? "was" : "were"} already gone.`,
    kept > 0 && `${files(kept)} ${kept === 1 ? "was" : "were"} left in place because ${kept === 1 ? "it holds" : "they hold"} content Editarr did not write.`,
  ];
  return parts.filter(Boolean).join(" ");
}
