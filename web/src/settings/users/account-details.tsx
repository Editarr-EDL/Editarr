import { useEffect, useState } from "react";
import { readAccount, revokeAccountSessions, setAccountStatus, type AccountDetail, type AccountSummary } from "../../api/api-users";
import { can, type Principal } from "../../auth/session";
import { ModalDialog } from "../../ui/ModalDialog";
import { ApiKeySettings } from "../api-key-settings";
import { ProfileEditor, accountName } from "../profile/profile-editor";
import { accountDate, accountStatusLabel, mayEnable, mayManageAccount } from "./account-actions";
import { watchAccountAccess } from "../../api/api-user-access";
import { AccountMedia } from "./account-media";
import { AccountFilterPolicy } from "./account-filter-policy";
import { OwnershipTransfer } from "./ownership-transfer";
import { RolePermissions } from "./role-permissions";
import { PolicySection } from "./policy-section";
import { AccountLinkSection } from "./account-link-section";
import { CommunitySettings } from "../community/community-settings";
import { useCommunityAccount } from "../community/community-access";
import { AccountDeletionDialog } from "./account-deletion-dialog";
import type { DeletionOutcome } from "./account-deletion-outcomes";

type Pending = "disable" | "enable" | "sessions" | "delete" | null;

export function AccountDetailsDialog({ target, principal, onClose, onChanged, onDeleted }: {
  target: AccountSummary; principal: Principal; onClose: () => void; onChanged: () => void; onDeleted: (outcomes: DeletionOutcome[]) => void;
}) {
  const [account, setAccount] = useState<AccountDetail | null>(null);
  const community = useCommunityAccount(account ?? target);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);
  const [deleted, setDeleted] = useState(false);
  useEffect(() => watchAccountAccess(target.id, onClose), [target.id, onClose]);
  useEffect(() => {
    const controller = new AbortController();
    void readAccount(target.id, controller.signal).then((next) => { if (!controller.signal.aborted) setAccount(next); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read account."); });
    return () => controller.abort();
  }, [target.id, reload]);
  const own = target.id === principal.userId;
  const editable = own ? can(principal, "profile.edit.own") : mayManageAccount(principal, account ?? target, "accounts.profile.edit.other");
  function saved(next: AccountDetail) { setAccount(next); onChanged(); }
  async function confirm() {
    if (!account || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      if (pending === "sessions") {
        const result = await revokeAccountSessions(account.id);
        setMessage(`${result.sessions_revoked} browser sessions revoked.`);
      } else {
        saved(await setAccountStatus(account.id, pending === "enable" ? "active" : "disabled"));
        setMessage(pending === "enable" ? "Account enabled." : "Account disabled. Data retained.");
      }
      setPending(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to update account."); }
    finally { setBusy(false); }
  }
  return <><ModalDialog labelledBy="account-details-heading" className="account-dialog" onClose={busy || pending ? undefined : onClose}>
    <div className="section-heading"><h2 id="account-details-heading">Account details</h2><button type="button" className="btn-secondary" disabled={busy || pending !== null} onClick={onClose}>Close</button></div>
    {!account && !error && <p role="status">Loading account...</p>}
    {error && <p role="alert">{error}</p>}
    {!account && error && <button className="btn-secondary" onClick={() => { setError(""); setReload(reload + 1); }}>Retry account</button>}
    {account && <>
      <ProfileEditor account={account} editable={editable} onSaved={saved} />
      <dl className="account-facts"><div><dt>Role</dt><dd>{account.role}</dd></div><div><dt>Status</dt><dd>{accountStatusLabel(account)}</dd></div><div><dt>Created</dt><dd>{accountDate(account.created_at)}</dd></div><div><dt>Last login</dt><dd>{accountDate(account.last_login_at)}</dd></div></dl>
      <AccountLinkSection account={account} principal={principal} own={own} onChanged={() => { setReload(value => value + 1); onChanged(); }} />
      <div className="button-row account-management">
        {account.status === "active" && mayManageAccount(principal, account, "accounts.disable.other") && <button className="danger-button" onClick={() => { setError(""); setPending("disable"); }}>Disable account</button>}
        {mayEnable(principal, account) && <button className="btn-secondary" onClick={() => { setError(""); setPending("enable"); }}>Enable account</button>}
        {mayManageAccount(principal, account, "accounts.sessions.revoke.other") && <button className="btn-secondary" onClick={() => { setError(""); setPending("sessions"); }}>Revoke sessions</button>}
        {mayManageAccount(principal, account, "accounts.delete.other") && <button className="danger-button" onClick={() => { setError(""); setPending("delete"); }}>Delete account</button>}
      </div>
      <ApiKeySettings key={account.id} target={account} />
      {community.allowed && community.view && <PolicySection title="Community connection"><CommunitySettings target={account} /></PolicySection>}
      {principal.role === "owner" && mayManageAccount(principal, account, "roles.assign") && <PolicySection title="Role and permissions"><RolePermissions account={account} principal={principal} onChanged={next => { if (next) setAccount(next); onChanged(); }} /></PolicySection>}
      <AccountMedia account={account} principal={principal} onChanged={onChanged} />
      <AccountFilterPolicy account={account} principal={principal} onChanged={onChanged} />
      <OwnershipTransfer account={account} principal={principal} onTransferred={() => { onClose(); onChanged(); }} />
      {message && <p role="status">{message}</p>}
    </>}
  </ModalDialog>
    {pending === "delete" && <AccountDeletionDialog target={{ id: target.id, name: accountName(account ?? target) }}
      onClose={() => { setPending(null); if (deleted) onClose(); }}
      onDeleted={outcome => { setDeleted(true); onDeleted([outcome]); }} />}
    {(pending === "disable" || pending === "enable" || pending === "sessions") && <ModalDialog labelledBy="account-confirm-heading" className="confirmation-dialog" onClose={busy ? undefined : () => setPending(null)}>
      <h2 id="account-confirm-heading">{pending === "sessions" ? "Revoke sessions" : pending === "disable" ? "Disable account" : "Enable account"}?</h2>
      <p>{accountName(target)}. {pending === "disable" ? "Sign-in and API access stop. Saved data and keys are retained." : pending === "sessions" ? "All browser sessions end. The API key keeps working." : "The account can sign in again. Revoked sessions stay revoked."}</p>
      {error && <p role="alert">{error}</p>}
      <div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => setPending(null)}>Cancel</button><button className="danger-button" disabled={busy} onClick={() => void confirm()}>Confirm</button></div>
    </ModalDialog>}
  </>;
}