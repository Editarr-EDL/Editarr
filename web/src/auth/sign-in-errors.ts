import { ApiError } from "../api/api-transport";

const SIGN_IN_ERRORS: Record<string, string> = {
  login_throttled: "Too many sign-in attempts. Please wait and try again.",
  account_disabled: "This account is disabled. Ask an administrator for help.",
  jellyfin_user_blocked: "This Jellyfin user is blocked from Editarr. Ask the owner to unblock you.",
  jellyfin_unavailable: "Jellyfin could not confirm the sign-in. Try again shortly.",
  jellyfin_not_configured: "Jellyfin sign-in is not available.",
  database_busy: "Editarr is busy. Try again in a moment.",
  quick_connect_disabled: "Quick Connect is turned off in Jellyfin.",
  quick_connect_throttled: "Too many Quick Connect codes were requested.",
  quick_connect_busy: "Too many Quick Connect codes are waiting. Try again later or sign in with a password.",
};

/** Wrong, unknown, and malformed credentials share one message so none can be told apart. */
export function signInError(reason: unknown): string {
  const code = reason instanceof ApiError ? reason.code : undefined;
  return (code && SIGN_IN_ERRORS[code]) || "Unable to sign in with those credentials. Please try again.";
}
