import { useEffect, useRef, useState } from "react";
import { checkCommunityRecovery, completeCommunityRecovery, fetchCommunityRecovery, retryCommunityRecovery, startCommunityRecovery } from "../../api/api-community";
import type { CommunityRecoveryStatus, CommunityRequest } from "../../api/api-community";
import { ModalDialog } from "../../ui/ModalDialog";
import { useCan } from "../../auth/auth-gate";
import { communityError, failureMessage, handoffState } from "./community-state";
import { useCommunityLifetime } from "./community-access";

export function CommunityIdentityRecovery({ onChanged, userId, mayManageKeys: permitted }: {
  onChanged: () => Promise<void>; userId?: number; mayManageKeys?: boolean;
}) {
  const ownKeys = useCan("community.identity.keys.own");
  const mayManageKeys = permitted ?? ownKeys;
  const lifetime = useCommunityLifetime();
  const options = (): CommunityRequest => ({ userId, signal: lifetime.signal() });
  const [status, setStatus] = useState<CommunityRecoveryStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [oldKey, setOldKey] = useState("");
  const [reference, setReference] = useState("");
  const [confirmation, setConfirmation] = useState<"start" | "complete" | "retry" | null>(null);
  const running = useRef(false);
  const active = useRef(true);
  const pending = status?.pending;
  const stateId = handoffState(reference, status?.serviceUrl);

  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    if (!mayManageKeys || !lifetime.current()) return () => controller.abort();
    void fetchCommunityRecovery(controller.signal, options()).then((value) => {
      if (lifetime.current() && !controller.signal.aborted) { setStatus(value); setOldKey(value.pending?.oldKeyId ?? value.identity?.keyId ?? ""); setReference(value.pending?.stateId ?? ""); }
    }).catch(() => {});
    return () => { active.current = false; controller.abort(); };
  }, [userId, mayManageKeys]);

  async function run(operation: (options?: CommunityRequest) => Promise<CommunityRecoveryStatus>) {
    if (!mayManageKeys || !lifetime.current() || running.current) return;
    running.current = true; setBusy(true); setError("");
    try {
      const value = await operation(options());
      if (!lifetime.current()) return;
      if (active.current) { setStatus(value); setOldKey(value.pending?.oldKeyId ?? value.identity?.keyId ?? ""); setReference(value.pending?.stateId ?? ""); }
      await onChanged();
    } catch (reason) {
      if (!lifetime.current()) return;
      const failure = communityError(reason);
      if ([403, 404].includes(failure.status)) { setStatus(null); setOldKey(""); setReference(""); setConfirmation(null); lifetime.refresh(); }
      else try { const value = await fetchCommunityRecovery(lifetime.signal(), options()); if (active.current) setStatus(value); } catch {}
      if (active.current && lifetime.current()) setError(failureMessage(failure.code));
    } finally { running.current = false; if (active.current && lifetime.current()) setBusy(false); }
  }

  if (!mayManageKeys || !status?.configured || !status.identity?.contributorId) return error ? <p role="alert">{error}</p> : null;
  return <section className="community-settings" aria-labelledby="community-recovery-heading" aria-busy={busy}>
    <h2 id="community-recovery-heading">Lost-key recovery</h2>
    <p className="muted-copy">Fresh authorization with the original Discord account replaces only this installation's affected key. Old-key copies and old backups lose community access immediately; other separately registered installations keep access. Contributor history and moderation are preserved.</p>
    <p className="muted-copy">Without the original Discord account, restore an active matching backup. There is no account-transfer bypass. Local scans and reviews remain available.</p>
    {error && <p className="community-notice" role="alert">{error}</p>}
    <dl className="community-identity"><div><dt>Installation</dt><dd><code>{status.identity.installationId}</code></dd></div></dl>
    {mayManageKeys && !pending && <>
      <label className="field"><span>Affected signing key fingerprint</span><input value={oldKey} onChange={(event) => setOldKey(event.target.value)} autoComplete="off" spellCheck={false} /></label>
      <button className="btn-secondary" disabled={busy || !/^sha256:[0-9a-f]{64}$/.test(oldKey)} onClick={() => setConfirmation("start")}>Recover with Discord</button>
    </>}
    {pending && <>
      <p>Saved recovery operation: <code>{pending.operationId}</code></p>
      <dl className="community-identity"><div><dt>Affected key</dt><dd><code>{pending.oldKeyId}</code></dd></div><div><dt>Replacement fingerprint</dt><dd><code>{pending.newKeyId}</code></dd></div></dl>
      {mayManageKeys && <>
        {status.authorizationUrl ? <p><a href={status.authorizationUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Authorize recovery with Discord</a></p>
          : <button className="btn-secondary" disabled={busy} onClick={() => setConfirmation("start")}>{pending.stateId ? "Fresh Discord recovery authorization" : "Resume recovery authorization"}</button>}
        <label className="field"><span>Discord recovery URL or reference</span><input value={reference} onChange={(event) => setReference(handoffState(event.target.value, status.serviceUrl) ?? event.target.value)} autoComplete="off" spellCheck={false} /></label>
        <button className="btn-primary" disabled={busy || !stateId} onClick={() => setConfirmation("complete")}>Review recovery</button>
      </>}
      {mayManageKeys && pending.stateId && <div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => void run(checkCommunityRecovery)}>Check recovery</button><button className="btn-primary" disabled={busy} onClick={() => setConfirmation("retry")}>Retry saved recovery</button></div>}
      {status.recoveryStatus === "not_committed" && <p role="status">The service has not committed this saved recovery. This is a read snapshot, not cancellation of an in-flight request.</p>}
    </>}
    {status.completed && <p className="community-notice" role="status">Identity recovery activated. Create a fresh identity backup for the current signing key. Backup freshness has not been verified.</p>}
    <p><a href="#/settings/general">Create identity backup</a></p>
    {mayManageKeys && confirmation && <ModalDialog labelledBy="recovery-confirm-heading" className="confirmation-dialog community-confirmation" onClose={busy ? undefined : () => setConfirmation(null)}>
      <h2 id="recovery-confirm-heading">{confirmation === "start" ? "Authorize identity recovery?" : "Replace this installation's key?"}</h2>
      <p>{confirmation === "start" ? "Prepare a saved replacement and authorize with the original Discord account. No key is replaced until your final confirmation." : "Confirm the original Discord account and this installation. Its affected key and old backups lose community access immediately. Other installations retain access; contributor history stays unchanged. Create a fresh backup after success; encryption remains optional."}</p>
      <dl className="community-identity"><div><dt>Installation</dt><dd><code>{status.identity.installationId}</code></dd></div><div><dt>Affected key</dt><dd><code>{pending?.oldKeyId ?? oldKey}</code></dd></div></dl>
      <div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => setConfirmation(null)}>Cancel</button><button className="btn-primary" disabled={busy} onClick={() => {
        const chosen = confirmation; setConfirmation(null);
        void run(chosen === "start" ? (options) => startCommunityRecovery(pending?.oldKeyId ?? oldKey, options) : chosen === "retry" ? retryCommunityRecovery : (options) => completeCommunityRecovery(stateId!, options));
      }}>{confirmation === "start" ? "Prepare recovery" : "Confirm recovery"}</button></div>
    </ModalDialog>}
  </section>;
}