import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiKeyStatus, issueApiKey, revealApiKey, revokeApiKey, type ApiKeyStatus } from "../api/api-key";
import { issueAccountKey, revealAccountKey, revokeAccountKey, type AccountSummary } from "../api/api-users";
import { usePrincipal } from "../auth/auth-gate";
import { FreshAuthenticationDialog } from "../auth/fresh-authentication";
import { can } from "../auth/session";
import { ModalDialog } from "../ui/ModalDialog";

type Pending = "reveal" | "issue" | "revoke" | null;

const DIALOG = {
  reveal: { title: "Show API key?", action: "Show key", body: "Confirm your Jellyfin password to show the key." },
  issue: { title: "Generate API key?", action: "Generate key", body: "Confirm your Jellyfin password to create a key." },
  replace: { title: "Replace API key?", action: "Replace key", body: "The current key will stop working immediately. Update any integrations that use it." },
};

/** Manage one key without retaining a secret beyond this account view. */
export function ApiKeySettings({ target }: { target?: AccountSummary } = {}) {
  const principal = usePrincipal();
  const other = target !== undefined && target.id !== principal?.userId;
  const protectedTarget = other && (target.role === "owner" || (target.role === "admin" && (principal?.role !== "owner" || !can(principal, "accounts.admins.manage"))));
  const allowed = principal !== null && !protectedTarget;
  const mayReveal = allowed && can(principal, other ? "api_keys.reveal.other" : "api_keys.reveal.own");
  const mayRevoke = allowed && can(principal, other ? "api_keys.revoke.other" : "api_keys.revoke.own");
  const mayCreate = allowed && can(principal, other ? "api_keys.issue.other" : "api_keys.create.own");
  const mayRotate = allowed && can(principal, other ? "api_keys.issue.other" : "api_keys.rotate.own");
  const mayReadOwn = !other && (mayReveal || mayRevoke || mayCreate || mayRotate);
  const [status, setStatus] = useState<ApiKeyStatus | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    if (mayReadOwn) void apiKeyStatus().then((value) => { if (active.current) setStatus(value); })
      .catch((error: Error) => { if (active.current) setMessage(error.message); });
    return () => { active.current = false; };
  }, [mayReadOwn]);
  const mayIssue = other ? mayCreate : status?.configured ? mayRotate : mayCreate;
  if (!allowed || (!mayReveal && !mayRevoke && !mayCreate && !mayRotate)) return null;
  const dialog = pending === "issue" ? (other || status?.configured ? DIALOG.replace : DIALOG.issue) : pending === "reveal" ? DIALOG.reveal : null;
  function open(action: Pending) { setMessage(""); setPending(action); }
  async function confirmWithPassword(password: string) {
    const secret = other
      ? await (pending === "reveal" ? revealAccountKey : issueAccountKey)(target!.id, password)
      : await (pending === "reveal" ? revealApiKey : issueApiKey)(password);
    if (!active.current) return;
    setShown(secret.api_key);
    setStatus({ configured: true, created_at: secret.created_at, revealable: true });
    if (pending === "issue") setMessage("API key generated. Confirm your Jellyfin password to show it again later.");
    setPending(null);
  }
  async function revoke(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const next = other ? await revokeAccountKey(target!.id) : await revokeApiKey();
      if (!active.current) return;
      setStatus(next); setShown(null); setMessage("API key revoked.");
      setPending(null);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Unable to update the API key."); }
    finally { setBusy(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(shown ?? ""); setMessage("API key copied."); }
    catch { setMessage("Clipboard unavailable. Select and copy the API key."); }
  }
  return <section className="settings-card" aria-labelledby="api-key-heading">
    <div className="section-heading"><div><h2 id="api-key-heading">API Key</h2>
      <p>Automate Editarr with the X-Api-Key header. The key has your account's current permissions; keep it private.</p></div>
      {status?.configured && <span className="configured-badge">API key configured</span>}
    </div>
    {status?.configured && !status.revealable && <p>This key cannot be shown. Generate a new key to see it here.</p>}
    {shown && <>
      <label className="field"><span>Current API key</span><input readOnly type="text" value={shown} autoComplete="off" spellCheck={false} /></label>
      <div className="button-row"><button className="btn-secondary" onClick={() => void copy()}>Copy key</button>
        <button className="btn-secondary" onClick={() => setShown(null)}>Hide key</button></div>
    </>}
    <div className="button-row">
      {mayReveal && (other || status?.revealable) && !shown && <button className="btn-secondary" disabled={busy} onClick={() => open("reveal")}>Show API key</button>}
      {mayIssue && <button className="btn-primary" disabled={busy || (!other && !status)} onClick={() => open("issue")}>{other || status?.configured ? "Generate new API key" : "Generate API key"}</button>}
      {mayRevoke && (other || status?.configured) && <button className="danger-button" disabled={busy} onClick={() => open("revoke")}>Revoke API key</button>}
    </div>
    {message && pending === null && <p role="status" className="form-status">{message}</p>}
    <div onKeyDown={(event) => event.stopPropagation()}>
    {dialog && <FreshAuthenticationDialog title={dialog.title} body={dialog.body} actionLabel={dialog.action} danger={dialog === DIALOG.replace}
      onConfirm={confirmWithPassword} onClose={() => setPending(null)} />}
    {pending === "revoke" && <ModalDialog labelledBy="api-key-revoke-heading" className="confirmation-dialog" onClose={busy ? undefined : () => setPending(null)}>
      <form onSubmit={(event) => void revoke(event)}>
        <h2 id="api-key-revoke-heading">Revoke API key?</h2>
        <p>The current key will stop working immediately. Update any integrations that use it.</p>
        {message && <p role="alert" className="form-status">{message}</p>}
        <div className="button-row"><button type="button" className="btn-secondary" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
          <button type="submit" className="danger-button" disabled={busy}>Revoke key</button></div>
      </form>
    </ModalDialog>}
    </div>
  </section>;
}
