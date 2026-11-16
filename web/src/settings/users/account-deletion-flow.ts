import type { AccountDeletionPreview, AccountDeletionResult, DeletionMode, Disposition, FileChoices } from "../../api/api-account-deletion";
import { useEffect, useRef } from "react";
import { ApiError } from "../../api/api-transport";
import { freshAuthenticationError } from "../../auth/fresh-authentication";

/**
 * One account's deletion dialog. Passwords never enter this state. `review` holds the
 * exact preview whose confirmation the request carries, with every visible file's choice.
 */
export type DeletionFlow =
  | { step: "choose"; notice: string | null; mode: DeletionMode | null }
  | { step: "loading"; disposition: Disposition; choices: FileChoices; notice: string | null }
  | { step: "review"; disposition: Disposition; preview: AccountDeletionPreview; choices: FileChoices; notice: string | null }
  | { step: "done"; result: AccountDeletionResult; disposition: Disposition }
  | { step: "stopped"; message: string };

export type DeletionEvent =
  | { type: "choose" }
  | { type: "preview"; disposition: Disposition }
  | { type: "previewed"; preview: AccountDeletionPreview }
  | { type: "choice"; exportId: number; choice: "retain" | "remove" }
  | { type: "deleted"; result: AccountDeletionResult }
  | { type: "refused"; reason: unknown }
  | { type: "unconfirmed"; message: string };

type Recovery = "review" | "choose" | "stop";
const NOTHING_CHANGED = "Nothing changed.";

/** Each MU-38 refusal and where the person goes next. Unknown codes stop without claiming an outcome. */
const REFUSALS: Record<string, { recovery: Recovery; message: string }> = {
  stale_account_deletion: { recovery: "review", message: `The account changed after this preview. ${NOTHING_CHANGED} Review the current impact and confirm again.` },
  account_deletion_blocked: { recovery: "review", message: `Something now blocks this deletion. ${NOTHING_CHANGED} Resolve the blockers below first.` },
  account_deletion_choice_required: { recovery: "review", message: `The published files changed. ${NOTHING_CHANGED} Choose what happens to each file again.` },
  transfer_destination_unavailable: { recovery: "choose", message: `That destination can no longer receive the account's data. ${NOTHING_CHANGED} Choose another destination.` },
  protected_account: { recovery: "stop", message: "This account is protected. The owner can't be deleted, and only the owner can delete an admin." },
  own_account: { recovery: "stop", message: "You can't delete your own account." },
  not_found: { recovery: "stop", message: "This account no longer exists or you can't see it." },
  capability_denied: { recovery: "stop", message: "You no longer have permission to delete accounts." },
  export_write_failed: { recovery: "stop", message: `A moved export file could not be written. ${NOTHING_CHANGED} Fix the export location, then start again.` },
  export_io_failed: { recovery: "stop", message: `An export file could not be read or removed. ${NOTHING_CHANGED} Check the export location, then start again.` },
  export_destination_unavailable: { recovery: "stop", message: `An export's media folder can't be reached. ${NOTHING_CHANGED}` },
  export_recovery_required: { recovery: "stop", message: `An earlier export change still needs recovery. ${NOTHING_CHANGED} Editarr recovers it at startup and every hour; start again later.` },
};

const code = (reason: unknown) => reason instanceof ApiError ? reason.code ?? (reason.status === 404 ? "not_found" : "") : "";

/** A refusal's message, preferring the fresh-authentication wording for password and session checks. */
export function deletionRefusalMessage(reason: unknown, fallback = "Editarr did not confirm the deletion. Reopen Users to see the account's current state."): string {
  return REFUSALS[code(reason)]?.message ?? freshAuthenticationError(reason, fallback);
}

/** The message for one refused bulk target's code. */
export const refusalCodeMessage = (refusal: string) => deletionRefusalMessage(new ApiError("", 409, refusal), `Editarr refused this account (${refusal}).`);

/**
 * Codes Editarr can return after a deletion already committed: a transfer commits, then
 * finishing its export files can time out on the database lock. They leave the outcome unknown.
 * Retrying the same confirmation is safe because Editarr replays a committed deletion.
 */
const UNCONFIRMED_CODES = new Set(["database_busy"]);

/** Whether a refusal code proves the deletion was not applied. */
export const refusedUnchanged = (refusal: string) => !UNCONFIRMED_CODES.has(refusal);

/**
 * Whether Editarr answered a failed deletion request with a refusal, so nothing changed.
 * No response, an unreadable one, a gateway error, or a code that can follow a commit
 * leaves the outcome unknown: the request may have been applied.
 */
export const answered = (reason: unknown) => reason instanceof ApiError && (reason.code !== undefined ? refusedUnchanged(reason.code) : reason.status < 500);

/** Whether the fresh-authentication dialog should keep the attempt open for another password. */
export function isPasswordRefusal(reason: unknown): boolean {
  return ["reauthentication_failed", "reauthentication_throttled", "reauthentication_required", "jellyfin_unavailable"].includes(code(reason));
}

/** Whether a refused bulk target can be reviewed again and resubmitted. */
export const reviewAgain = (refusal: string) => REFUSALS[refusal]?.recovery === "review";

/** Keep a still-listed file's earlier choice; default new files to the non-destructive keep. */
export function fileChoices(preview: AccountDeletionPreview, previous: FileChoices): FileChoices {
  return Object.fromEntries(preview.files.map(({ publication, choices }) => {
    const kept = previous[publication.id];
    return [publication.id, kept && choices.includes(kept) ? kept : "retain"];
  }));
}

export function deletionFlow(state: DeletionFlow, event: DeletionEvent): DeletionFlow {
  switch (event.type) {
    case "choose": return { step: "choose", notice: null, mode: "disposition" in state ? state.disposition.mode : null };
    case "preview": return { step: "loading", disposition: event.disposition, choices: {}, notice: null };
    case "previewed":
      if (state.step !== "loading") return state;
      return { step: "review", disposition: state.disposition, preview: event.preview, choices: fileChoices(event.preview, state.choices), notice: state.notice };
    case "choice":
      if (state.step !== "review") return state;
      return { ...state, choices: { ...state.choices, [event.exportId]: event.choice } };
    case "deleted":
      if (state.step !== "review") return state;
      return { step: "done", result: event.result, disposition: state.disposition };
    case "refused": {
      const message = deletionRefusalMessage(event.reason);
      const recovery = REFUSALS[code(event.reason)]?.recovery ?? "stop";
      if (recovery === "review" && (state.step === "review" || state.step === "loading")) {
        return { step: "loading", disposition: state.disposition, choices: state.choices, notice: message };
      }
      if (recovery === "choose") return { step: "choose", notice: message, mode: "disposition" in state ? state.disposition.mode : null };
      return { step: "stopped", message };
    }
    case "unconfirmed": return { step: "stopped", message: event.message };
  }
}

/**
 * Keep keyboard focus inside the dialog when a later step replaces the focused control:
 * the returned container takes focus unless focus is already within it. ModalDialog
 * places the opening focus, so the first step leaves focus alone.
 */
export function useStepFocus(step: unknown) {
  const container = useRef<HTMLDivElement>(null);
  const shown = useRef(step);
  useEffect(() => {
    if (Object.is(shown.current, step)) return;
    shown.current = step;
    const element = container.current;
    if (element && !element.contains(document.activeElement)) element.focus();
  }, [step]);
  return container;
}
