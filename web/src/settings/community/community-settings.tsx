import { useEffect, useState } from "react";
import { cancelCommunityEnrollment, completeCommunityEnrollment, disconnectCommunity, startCommunityEnrollment } from "../../api/api-community";
import type { AccountSummary } from "../../api/api-users";
import { BACKUP_SETTINGS_ROUTE } from "../../routes";
import { ModalDialog } from "../../ui/ModalDialog";
import { useCommunityAccount } from "./community-access";
import { CommunityProfileEditor } from "./community-profile";
import { CommunityKeyRotation } from "./community-rotation";
import { CommunityIdentityRecovery } from "./community-recovery";
import { CommunityReceipts } from "./community-receipts";
import { blocksIdentity, failureMessage, handoffState, useCommunityConnection } from "./community-state";

export function CommunitySettings({ target }: { target?: AccountSummary } = {}) {
  const access = useCommunityAccount(target);
  if (!access.allowed || !access.view) return null;
  return <CommunitySettingsBody key={access.userId ?? "own"} target={target} />;
}

function CommunitySettingsBody({ target }: { target?: AccountSummary }) {
  const access = useCommunityAccount(target);
  const mayEnroll = access.enroll;
  const { connection, busy, error, verified, run, refresh } = useCommunityConnection(true, access.userId);
  const [reference, setReference] = useState("");
  const [confirming, setConfirming] = useState(false);
  const pending = connection?.pending;
  const profile = connection?.profile;
  const stateId = pending?.stateId ?? handoffState(reference, connection?.serviceUrl);
  const blocked = blocksIdentity(error?.code) || Boolean(connection?.identity && !["unenrolled", "active"].includes(connection.identity.lastKnownKeyState));
  const enrollmentFailed = Boolean(error && !connection?.connected);

  useEffect(() => { setReference(pending?.stateId ?? ""); setConfirming(false); }, [pending?.stateId, Boolean(pending)]);

  const status = !connection ? "Connection unavailable" : !connection.configured ? "Not configured"
    : connection.pendingRotation ? "Key rotation pending" : blocked ? "Identity unavailable" : error ? "Community access paused"
    : pending ? "Awaiting confirmation" : connection.connected ? verified ? "Connected" : "Connected; access not verified" : "Not connected";

  return <><section className="community-settings" aria-labelledby="community-heading" aria-busy={busy}>
    <div className="section-heading"><div><h2 id="community-heading">Community scans{access.other && ` for ${target!.display_name || target!.username}`}</h2><p role="status">{busy ? "Checking community connection..." : status}</p></div>
      <button className="btn-secondary" disabled={busy} onClick={() => void refresh()}>Refresh connection</button>
    </div>
    {error && <p className="community-notice" role="alert">{failureMessage(error.code)}</p>}
    {blocked && !connection?.pendingRotation && <p className="muted-copy">Restore a matching backup or use explicit Discord recovery below when installation metadata survives. Normal reconnect never replaces a key.</p>}
    {connection && !connection.configured && <p className="muted-copy">Community service is not configured. Local scans and reviews remain available.</p>}
    {connection?.configured && <>
      {connection.serviceUrl && <p className="community-service-address"><span className="muted-copy">Service</span> <code>{connection.serviceUrl}</code></p>}
      {connection.identity && <dl className="community-identity"><div><dt>Installation ID</dt><dd><code>{connection.identity.installationId}</code></dd></div><div><dt>Signing key fingerprint</dt><dd><code>{connection.identity.keyId}</code></dd></div><div><dt>Saved key state</dt><dd>{connection.identity.lastKnownKeyState}</dd></div></dl>}
      {profile && !verified && <p className="community-notice" role="status">Last saved profile: {profile.displayName}. Current community access is not verified.</p>}
      {profile && <CommunityProfileEditor connection={connection} busy={busy} verified={verified && !error && !connection.pendingRotation} run={run} mayEdit={access.profile} />}
      {connection.connected && <CommunityKeyRotation connection={connection} busy={busy} verified={verified && !error} run={run} mayManageKeys={access.keys} />}
      {access.disconnect && connection.connected && <button className="btn-secondary" disabled={busy} onClick={() => void run(disconnectCommunity)}>Disconnect community</button>}
      {mayEnroll && !blocked && !pending && !connection.connected && <div className="button-row"><button className="btn-primary" disabled={busy} onClick={() => void run(startCommunityEnrollment)}>Connect with Discord</button></div>}
      {mayEnroll && !blocked && pending && <div className="community-enrollment">
        <h3>Discord authorization</h3>
        <p className="muted-copy">Expires {new Date(pending.expiresAt * 1000).toLocaleString()}.</p>
        {!pending.stateId && <>
          {connection.authorizationUrl ? <p><a className="community-authorize-link" href={connection.authorizationUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Continue with Discord</a></p>
            : <button className="btn-secondary" disabled={busy} onClick={() => void run(startCommunityEnrollment)}>Resume Discord authorization</button>}
          <label className="field"><span>Discord confirmation URL or enrollment reference</span><input value={reference} onChange={(event) => setReference(handoffState(event.target.value, connection.serviceUrl) ?? event.target.value)} autoComplete="off" spellCheck={false} /></label>
          {reference && !stateId && <p role="status" className="muted-copy">Enter a 64-character enrollment reference or a confirmation URL from this community service.</p>}
        </>}
        {pending.stateId && <p>Pending enrollment reference: <code>{pending.stateId}</code></p>}
        <div className="button-row"><button className="btn-primary" disabled={busy || !stateId} onClick={() => setConfirming(true)}>{pending.stateId || enrollmentFailed ? "Retry confirmation" : "Review connection"}</button><button className="btn-secondary" disabled={busy} onClick={() => void run(cancelCommunityEnrollment)}>Cancel enrollment</button></div>
      </div>}
    </>}
    <div className="community-backup"><h3>Identity backup</h3>
      {connection?.rotationCompleted && <p className="community-notice" role="status">Key rotation activated. Create a fresh identity backup for the current signing key. Backup freshness has not been verified.</p>}
      <p className="muted-copy">Backup status: not verified. Backups include every account's signing identity. Encryption is optional and off by default.</p><a href={`#/${BACKUP_SETTINGS_ROUTE}`}>Backup and restore</a></div>
    {mayEnroll && confirming && stateId && <ModalDialog labelledBy="community-confirm-heading" className="confirmation-dialog community-confirmation" onClose={busy ? undefined : () => setConfirming(false)}>
      <h2 id="community-confirm-heading">Connect this installation?</h2>
      <p>Confirm that you authorized the intended Discord account. This registers the existing signing key without replacing another key or enabling automatic sharing.</p>
      <dl className="community-identity"><div><dt>Installation</dt><dd><code>{connection?.identity?.installationId ?? profile?.installationId ?? "Current installation"}</code></dd></div><div><dt>Signing key</dt><dd><code>{connection?.identity?.keyId ?? profile?.keyId ?? "Current signing key"}</code></dd></div><div><dt>Enrollment reference</dt><dd><code>{stateId}</code></dd></div></dl>
      <div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => setConfirming(false)}>Back</button><button className="btn-primary" disabled={busy} onClick={() => { setConfirming(false); void run((options) => completeCommunityEnrollment(stateId, options), true); }}>Confirm connection</button></div>
    </ModalDialog>}
  </section>{connection && access.keys && <CommunityIdentityRecovery onChanged={refresh} userId={access.userId} mayManageKeys={access.keys} />}
    {connection && access.receipts && <CommunityReceipts userId={access.userId} />}</>;
}
