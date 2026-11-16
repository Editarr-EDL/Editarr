import { useEffect, useState } from "react";
import { readJellyfinSync } from "../../api/api-jellyfin-accounts";
import type { AccountDetail } from "../../api/api-users";
import { useOptionalAuthenticationGate } from "../../auth/auth-gate";
import { useOperationReceipt } from "../../auth/operation-receipts";
import type { LinkResult } from "../../api/api-account-link";
import { can, type Principal } from "../../auth/session";
import { mayManageAccount } from "./account-actions";
import { AccountLinkDialog, type LinkTarget } from "./account-link-dialog";
import { cleanupMessage, linkedMessage } from "./account-link-flow";
import { accountName } from "../profile/profile-editor";

type LinkState = Principal["linkState"];
interface LinkStatus { state: LinkState | null; graceUntil: string | null }

const STATUS: Record<LinkState, (status: LinkStatus) => string> = {
  unlinked: () => "Not linked to Jellyfin. The account cannot sign in until it is linked.",
  verified: () => "Linked to Jellyfin. Name and avatar sync from Jellyfin. A linked account cannot be unlinked.",
  outage_grace: ({ graceUntil }) => `Jellyfin cannot be reached. Media access continues from the last successful check ${graceUntil ? `until ${new Date(graceUntil).toLocaleString()}` : "for up to 24 hours"}. Profile access is unaffected.`,
  outage_expired: () => "Jellyfin has not answered for more than 24 hours. Only profile access remains; media is blocked until Jellyfin answers again.",
  server_replaced: () => "The Jellyfin server this account was linked to is no longer the configured server. Media access is suspended until an admin relinks the account to the current server. Profile access continues.",
};

/** The effective link state: the sync status when readable, else the session's own state. */
function useLinkStatus(account: AccountDetail, principal: Principal, own: boolean, reload: number): LinkStatus {
  const readable = account.source === "jellyfin" && can(principal, "accounts.jellyfin.sync");
  const fallback: LinkStatus = { state: account.source === "local" ? "unlinked" : own ? principal.linkState : null, graceUntil: null };
  const [read, setRead] = useState<LinkStatus | null>(null);
  useEffect(() => {
    if (!readable) return;
    const controller = new AbortController();
    void readJellyfinSync(controller.signal).then(status => {
      const entry = status.accounts.find(item => item.user_id === account.id);
      if (!controller.signal.aborted && entry) setRead({ state: entry.link_state as LinkState, graceUntil: entry.grace_expires_at });
    }).catch(() => undefined);
    return () => controller.abort();
  }, [readable, account.id, reload]);
  return readable && read ? read : fallback;
}

/** Link status, disconnection messaging, and the link or relink action for one account. */
export function AccountLinkSection({ account, principal, own, onChanged }: { account: AccountDetail; principal: Principal; own: boolean; onChanged: () => void }) {
  const gate = useOptionalAuthenticationGate();
  const [reload, setReload] = useState(0);
  const [target, setTarget] = useState<LinkTarget | null>(null);
  const [receipt, saveReceipt] = useOperationReceipt<LinkResult>(`account-link:${account.id}`);
  const status = useLinkStatus(account, principal, own, reload);
  const kind = status.state === "unlinked" ? "link" : status.state === "server_replaced" ? "relink" : null;
  const allowed = own ? can(principal, "accounts.link.own") : mayManageAccount(principal, account, "accounts.link.other");
  const name = accountName(account);
  const cleanup = account.link_cleanup;
  const cleanupText = cleanup && (receipt || cleanup.state === "removing" || cleanup.kept > 0) ? cleanupMessage(cleanup) : "";
  const message = status.state ? STATUS[status.state](status) : "Linked to Jellyfin.";
  return <section className="account-section account-link-section" aria-labelledby={`account-link-${account.id}`}>
    <h3 id={`account-link-${account.id}`}>Jellyfin link</h3>
    <p className={`link-status link-status-${status.state ?? "linked"}`} role={status.state && status.state !== "unlinked" && status.state !== "verified" ? "status" : undefined}>{message}</p>
    {receipt && <p className="link-receipt" role="status">{linkedMessage(receipt)}</p>}
    {cleanup && cleanupText && <p className={`link-cleanup link-cleanup-${cleanup.state}`} role="status">{cleanupText}</p>}
    {allowed && kind && <div className="button-row"><button type="button" className="btn-secondary" onClick={() => setTarget({ id: account.id, name, own, kind })}>{kind === "link" ? "Link to Jellyfin" : "Relink to current server"}</button></div>}
    {target && <AccountLinkDialog target={target} onClose={() => setTarget(null)} onAccountDeleted={onChanged} onLinked={result => {
      saveReceipt(result); setReload(value => value + 1); onChanged(); if (own) gate?.refreshAuthenticationStatus();
    }} />}
  </section>;
}
