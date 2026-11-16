import { useState } from "react";
import { reconcileCommunityKey, retryCommunityKey, rotateCommunityKey } from "../../api/api-community";
import type { CommunityConnection, CommunityRequest } from "../../api/api-community";
import { ModalDialog } from "../../ui/ModalDialog";
import { useCan } from "../../auth/auth-gate";

type Props = {
  connection: CommunityConnection;
  busy: boolean;
  verified: boolean;
  run: (operation: (options?: CommunityRequest) => Promise<CommunityConnection>, current?: boolean) => Promise<void>;
  mayManageKeys?: boolean;
};

export function CommunityKeyRotation({ connection, busy, verified, run, mayManageKeys: permitted }: Props) {
  const ownKeys = useCan("community.identity.keys.own");
  const mayManageKeys = permitted ?? ownKeys;
  const [confirming, setConfirming] = useState(false);
  const pending = connection.pendingRotation;
  const canStart = verified && !connection.pending && !connection.pendingRename;
  return <div className="community-rotation">
    <h3>Signing key</h3>
    {pending ? <>
      <p role="status">Key rotation pending. Service activation has not been confirmed locally.</p>
      <dl className="community-identity"><div><dt>Rotation operation</dt><dd><code>{pending.operationId}</code></dd></div><div><dt>Prepared replacement</dt><dd><code>{pending.newKeyId}</code></dd></div></dl>
      {connection.rotationStatus === "not_committed" && <p className="muted-copy">The service has not committed this saved rotation. The replacement is retained.</p>}
      {mayManageKeys && <div className="button-row">
        <button className="btn-secondary" disabled={busy} onClick={() => void run(reconcileCommunityKey)}>Check rotation</button>
        <button className="btn-primary" disabled={busy} onClick={() => setConfirming(true)}>Retry saved rotation</button>
      </div>}
    </> : mayManageKeys && <button className="btn-secondary" disabled={busy || !canStart} onClick={() => setConfirming(true)}>Rotate key</button>}
    {mayManageKeys && confirming && <ModalDialog labelledBy="community-rotation-heading" className="confirmation-dialog community-confirmation" onClose={busy ? undefined : () => setConfirming(false)}>
      <h2 id="community-rotation-heading">{pending ? "Retry saved key rotation?" : "Rotate this installation's key?"}</h2>
      <p>Copies of this installation using the old signing key lose community access immediately when the service activates the replacement. Other separately registered installations retain access.</p>
      <p>The contributor account and its history stay unchanged. Create a fresh identity backup after activation; encryption remains optional.</p>
      {pending && <p>This retries the same prepared replacement and operation.</p>}
      <dl className="community-identity"><div><dt>Installation</dt><dd><code>{connection.identity?.installationId}</code></dd></div><div><dt>Current key</dt><dd><code>{connection.identity?.keyId}</code></dd></div></dl>
      <div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button><button className="btn-primary" disabled={busy} onClick={() => {
        setConfirming(false); void run(pending ? retryCommunityKey : rotateCommunityKey);
      }}>{pending ? "Confirm retry" : "Confirm rotation"}</button></div>
    </ModalDialog>}
  </div>;
}