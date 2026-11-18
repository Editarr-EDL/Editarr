import { useEffect, useRef, useState, type FormEvent } from "react";

import { setupWithJellyfin } from "../api/api-auth";
import { ApiError } from "../api/api-transport";
import { applicationAssetUrl } from "../shared/runtime-urls";
import { SetupRestore } from "../settings/backup-restore";
import { useAuthenticationGate } from "./auth-gate";
import { RememberMe } from "./remember-me";
import { RetryAfterNotice, useRetryAfter } from "./retry-after";

type FieldName = "serverUrl" | "username";
type FieldErrors = Partial<Record<FieldName, string>>;

const BAD_URL = "Enter an http or https address, such as http://jellyfin.local:8096.";

/** What the owner sees for each setup refusal the server names. */
const REFUSALS: Record<string, string> = {
  jellyfin_unreachable: "Editarr could not reach a Jellyfin server at that address. Check the address and that Jellyfin is running.",
  jellyfin_not_jellyfin: "That address did not answer as a Jellyfin server. Check the address.",
  invalid_credentials: "Jellyfin did not accept that username and password.",
  jellyfin_not_administrator: "That Jellyfin user is not an administrator. Sign in with a Jellyfin administrator to set up Editarr.",
  jellyfin_key_refused: "Jellyfin did not create an API key for Editarr. Try again.",
  jellyfin_key_left_behind: "Setup stopped, and Jellyfin may still hold an API key named Editarr from this attempt. Delete it in Jellyfin under Dashboard > API Keys, then try again.",
  login_throttled: "Too many setup attempts from this address.",
};

/** Check what the browser can before contacting Editarr; the server stays authoritative. */
export function validateJellyfinSetup(serverUrl: string, username: string): FieldErrors {
  const errors: FieldErrors = {};
  let parsed: URL | null = null;
  try { parsed = new URL(serverUrl.trim()); } catch { parsed = null; }
  if (!parsed || !["http:", "https:"].includes(parsed.protocol) || !parsed.host) errors.serverUrl = BAD_URL;
  if (!username.trim()) errors.username = "Enter your Jellyfin username.";
  return errors;
}

/** Connect a Jellyfin server with an administrator's sign-in, then enter the app as its owner. */
export function JellyfinSetup() {
  const { acceptAuthenticationStatus, continueToSignIn, retryAuthenticationStatus } = useAuthenticationGate();
  const [serverUrl, setServerUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submissionState, setSubmissionState] = useState<"idle" | "pending" | "success">("idle");
  const [restoreMode, setRestoreMode] = useState(false);
  const [restored, setRestored] = useState(false);
  const completionTimer = useRef<number | null>(null);
  const formErrorRef = useRef<HTMLParagraphElement>(null);
  const serverUrlRef = useRef<HTMLInputElement>(null);
  const [refusals, setRefusals] = useState(0);
  const retry = useRetryAfter();

  // Inputs are disabled while the request is pending, so focus moves after the refusal renders.
  useEffect(() => {
    if (refusals > 0) (fieldErrors.serverUrl ? serverUrlRef : formErrorRef).current?.focus();
  }, [refusals]);

  useEffect(() => () => {
    if (completionTimer.current !== null) window.clearTimeout(completionTimer.current);
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submissionState !== "idle" || retry.remaining > 0) return;

    const errors = validateJellyfinSetup(serverUrl, username);
    setFieldErrors(errors);
    setFormError(null);
    if (Object.keys(errors).length > 0) return;

    setSubmissionState("pending");
    try {
      const status = await setupWithJellyfin({
        server_url: serverUrl.trim(), username, password, remember_me: rememberMe,
      });
      setPassword("");
      setSubmissionState("success");
      // Let assistive technology announce the completion before the protected shell mounts.
      completionTimer.current = window.setTimeout(() => acceptAuthenticationStatus(status), 200);
    } catch (reason) {
      setPassword("");
      const code = reason instanceof ApiError ? reason.code : undefined;
      if (code === "setup_already_complete") {
        continueToSignIn("Editarr was already set up from another browser. Sign in to continue.");
        return;
      }
      retry.waitFor(reason);
      if (code === "jellyfin_bad_url") setFieldErrors({ serverUrl: BAD_URL });
      // Show only messages written here: server text could echo what was typed.
      else setFormError((code && REFUSALS[code]) ?? "Unable to set up Editarr. Please try again.");
      setSubmissionState("idle");
      setRefusals((count) => count + 1);
    }
  };

  const disabled = submissionState !== "idle";
  const field = (name: FieldName) => ({
    "aria-invalid": fieldErrors[name] ? "true" as const : undefined,
    "aria-describedby": fieldErrors[name] ? `setup-${name}-error` : name === "serverUrl" ? "setup-serverUrl-hint" : undefined,
  });

  return <main className="auth-page">
    <section className={`auth-panel${restoreMode ? " auth-restore-panel" : ""}`} aria-labelledby="setup-heading" aria-busy={submissionState === "pending"}>
      <header className="auth-brand">
        <img src={applicationAssetUrl("editarr-app-icon.png?v=1")} alt="" width="48" height="48" />
        <span>Editarr</span>
      </header>
      <div className="auth-heading">
        <p className="eyebrow">Welcome to Editarr</p>
        <h1 id="setup-heading">{restoreMode ? "RESTORE FROM BACKUP" : "CONNECT YOUR JELLYFIN SERVER"}</h1>
        <p>{restoreMode ? "Restore a full backup into this new installation instead of connecting a server." : "Sign in with a Jellyfin administrator. Editarr creates its own Jellyfin API key and makes you its owner."}</p>
      </div>
      {restoreMode ? <><h2 className="visually-hidden" id="backup-heading">Restore backup</h2><SetupRestore onComplete={retryAuthenticationStatus} onRestored={() => setRestored(true)} /></> : submissionState === "success" ? <p className="auth-success" role="status" aria-live="polite">Jellyfin connected. Signing you in…</p> : <form className="auth-form" onSubmit={(event) => void submit(event)} noValidate>
        <div className="auth-field">
          <label htmlFor="setup-server-url">Jellyfin server URL</label>
          <input ref={serverUrlRef} id="setup-server-url" name="server_url" type="url" inputMode="url" autoComplete="url" autoCapitalize="none" spellCheck={false} autoFocus placeholder="http://jellyfin.local:8096" value={serverUrl} onChange={(event) => setServerUrl(event.target.value)} {...field("serverUrl")} disabled={disabled} />
          <p className="auth-field-hint" id="setup-serverUrl-hint">The address Editarr uses to reach Jellyfin.</p>
          {fieldErrors.serverUrl && <p className="auth-field-error" id="setup-serverUrl-error" role="alert">{fieldErrors.serverUrl}</p>}
        </div>
        <div className="auth-field">
          <label htmlFor="setup-username">Jellyfin username</label>
          <input id="setup-username" name="username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(event) => setUsername(event.target.value)} {...field("username")} disabled={disabled} />
          {fieldErrors.username && <p className="auth-field-error" id="setup-username-error" role="alert">{fieldErrors.username}</p>}
        </div>
        <div className="auth-field">
          <label htmlFor="setup-password">Jellyfin password</label>
          <input id="setup-password" name="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} disabled={disabled} />
        </div>
        <RememberMe checked={rememberMe} disabled={disabled} onChange={setRememberMe} />
        {formError && <p ref={formErrorRef} tabIndex={-1} className="auth-form-error" role="alert" aria-live="assertive">{formError}<RetryAfterNotice retry={retry} /></p>}
        <button className="auth-submit btn-primary" type="submit" disabled={disabled || retry.remaining > 0}>{submissionState === "pending" ? "Connecting…" : "Connect and sign in"}</button>
      </form>}
      {submissionState === "idle" && !restored && <button className="btn-secondary auth-restore-toggle" type="button" onClick={() => { setPassword(""); setRestoreMode(!restoreMode); }}>{restoreMode ? "Connect a Jellyfin server instead" : "Restore from backup"}</button>}
    </section>
  </main>;
}
