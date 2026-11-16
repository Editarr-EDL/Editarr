import { useEffect, useState } from "react";
import { readAccount, type AccountDetail } from "../../api/api-users";
import { usePrincipal } from "../../auth/auth-gate";
import { can } from "../../auth/session";
import { ApiKeySettings } from "../api-key-settings";
import { accountDate } from "../users/account-actions";
import { ProfileEditor } from "./profile-editor";
import { CommunitySettings } from "../community/community-settings";
import { AccountLinkSection } from "../users/account-link-section";

export function ProfileView() {
  const principal = usePrincipal();
  const [account, setAccount] = useState<AccountDetail | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const userId = principal?.userId;
  const allowed = principal !== null && can(principal, "profile.view.own");
  useEffect(() => {
    if (!allowed || userId === undefined) return;
    const controller = new AbortController();
    setAccount(null); setError("");
    void readAccount(userId, controller.signal).then((next) => { if (!controller.signal.aborted) setAccount(next); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load profile."); });
    return () => controller.abort();
  }, [allowed, userId, reload]);
  if (!allowed) return null;
  return <div className="account-page profile-page">
    <div className="section-heading"><h1>My profile</h1></div>
    {error && <div role="alert"><p>{error}</p><button className="btn-secondary" onClick={() => setReload(reload + 1)}>Retry profile</button></div>}
    {!account && !error && <p role="status">Loading profile...</p>}
    {account && <><dl className="account-facts"><div><dt>Created</dt><dd>{accountDate(account.created_at)}</dd></div><div><dt>Last login</dt><dd>{accountDate(account.last_login_at)}</dd></div></dl>
      <ProfileEditor account={account} editable={can(principal, "profile.edit.own")} onSaved={setAccount} />
      <AccountLinkSection account={account} principal={principal} own onChanged={() => void readAccount(account.id).then(setAccount).catch(() => undefined)} /></>}
    {account && <ApiKeySettings />}
    {account && <CommunitySettings />}
  </div>;
}