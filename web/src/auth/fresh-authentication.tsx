import { useState, type FormEvent } from "react";

import { ApiError } from "../api/api-transport";
import { ModalDialog } from "../ui/ModalDialog";
import { RetryAfterNotice, useRetryAfter } from "./retry-after";

const FRESH_ERRORS: Record<string, string> = {
  reauthentication_failed: "That password is incorrect.",
  reauthentication_throttled: "Too many password attempts. Please wait before trying again.",
  jellyfin_unavailable: "Editarr could not reach Jellyfin to check your password. Nothing changed. Try again when Jellyfin is back.",
  reauthentication_required: "Confirm your Jellyfin password to continue.",
  interactive_session_required: "This action needs a browser session, not an API key.",
  database_busy: "Editarr is busy. Try again in a moment.",
};

/** A user-facing message for a refused fresh-authentication request. */
export function freshAuthenticationError(reason: unknown, fallback = "Unable to complete the action."): string {
  if (reason instanceof ApiError && reason.code && FRESH_ERRORS[reason.code]) return FRESH_ERRORS[reason.code];
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

/**
 * Ask for the signed-in account's Jellyfin password and send it inside the sensitive request.
 * The password stays in this component only until the attempt ends.
 * @param onConfirm Sends the request with `{ password }`; a rejection is shown here.
 * @param onClose Called on cancel, and by the caller after `onConfirm` succeeds.
 */
export function FreshAuthenticationDialog({ title, body, actionLabel, danger = false, onConfirm, onClose }: {
  title: string;
  body: string;
  actionLabel: string;
  danger?: boolean;
  onConfirm: (password: string) => Promise<void>;
  onClose: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const retry = useRetryAfter();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || retry.remaining > 0) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(password);
    } catch (reason) {
      retry.waitFor(reason);
      setError(freshAuthenticationError(reason));
    } finally {
      setPassword("");
      setBusy(false);
    }
  };

  return <ModalDialog labelledBy="fresh-authentication-heading" className="confirmation-dialog" onClose={busy ? undefined : onClose}>
    <form onSubmit={(event) => void submit(event)}>
      <h2 id="fresh-authentication-heading">{title}</h2>
      <p>{body}</p>
      <label className="field"><span>Jellyfin password</span>
        <input type="password" required autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} /></label>
      {error && <p role="alert" className="form-status">{error}<RetryAfterNotice retry={retry} /></p>}
      <div className="button-row"><button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className={danger ? "danger-button" : "btn-primary"} disabled={busy || retry.remaining > 0}>{actionLabel}</button></div>
    </form>
  </ModalDialog>;
}
