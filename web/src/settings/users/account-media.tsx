import { useEffect, useState } from "react";
import { readUserMedia, saveUserMedia, type ScopeChoice, type UserMediaAccess } from "../../api/api-user-access";
import type { AccountDetail } from "../../api/api-users";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import type { Principal } from "../../auth/session";
import { mayManageAccount } from "./account-actions";
import { MediaScopeEditor } from "./media-scope-editor";
import { PolicySection } from "./policy-section";

/** A local account's media scope; Jellyfin decides a linked account's media, so it has none here. */
export function AccountMedia({ account, principal, onChanged }: { account: AccountDetail; principal: Principal; onChanged: () => void }) {
  const allowed = account.source !== "jellyfin" && mayManageAccount(principal, account, "media_access.user.edit");
  return allowed ? <PolicySection title="Local media access"><AccountMediaEditor account={account} onChanged={onChanged} /></PolicySection> : null;
}
function AccountMediaEditor({ account, onChanged }: { account: AccountDetail; onChanged: () => void }) {
  const [view, setView] = useState<UserMediaAccess | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [choice, setChoice] = useState<ScopeChoice | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void readUserMedia(account.id, controller.signal).then(next => { if (!controller.signal.aborted) setView(next); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read media access."); });
    return () => controller.abort();
  }, [account.id]);
  return <>
    {error && <p role="alert">{error}</p>}
    {!view && !error && <p role="status">Loading media access...</p>}
    {view?.scope && <MediaScopeEditor key={JSON.stringify(view.scope)} scope={view.scope} allowInherit onSave={setChoice} />}
    {message && <p role="status">{message}</p>}
    {choice && <FreshAuthenticationDialog title="Save local media scope" body="The account's media access will be checked again by the server." actionLabel="Confirm media access" onClose={() => setChoice(null)} onConfirm={async password => {
      const next = await saveUserMedia(account.id, choice, password);
      setView(next); setChoice(null); setMessage("Media access saved."); onChanged();
    }} />}
  </>;
}
