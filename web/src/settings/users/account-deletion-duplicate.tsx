import { useEffect, useState } from "react";
import { readJellyfinSync } from "../../api/api-jellyfin-accounts";
import { readAccount, type AccountDetail } from "../../api/api-users";
import { usePrincipal } from "../../auth/auth-gate";
import { can } from "../../auth/session";
import { accountName } from "../profile/profile-editor";
import { mayManageAccount } from "./account-actions";
import { AccountDeletionDialog, type DeletionTarget } from "./account-deletion-dialog";
import type { DeletionOutcome } from "./account-deletion-outcomes";

/**
 * Find the account that already holds a Jellyfin identity, from the linked-account list.
 * Returns null when the caller can't read that list or the holder is not exactly one visible account.
 */
function useIdentityHolder(jellyfinUserId: string, linkTargetId: number): AccountDetail | null {
  const principal = usePrincipal();
  const allowed = principal !== null && can(principal, "accounts.jellyfin.sync") && can(principal, "accounts.delete.other");
  const [holder, setHolder] = useState<AccountDetail | null>(null);
  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    void readJellyfinSync(controller.signal).then(async status => {
      const holders = status.accounts.filter(account => account.jellyfin_user_id === jellyfinUserId && account.user_id !== linkTargetId);
      if (holders.length !== 1) return;
      const account = await readAccount(holders[0].user_id, controller.signal);
      if (!controller.signal.aborted) setHolder(account);
    }).catch(() => undefined);
    return () => controller.abort();
  }, [allowed, jellyfinUserId, linkTargetId]);
  return holder;
}

/**
 * The duplicate-identity blocker's way out: review deleting or transferring the account that
 * holds the identity, then start linking again. Editarr never merges the two accounts.
 * Only an active link target can receive the holder's data, so a disabled one is not offered.
 * `onResolved` receives the notice for the restarted link, after a deletion or an unknown outcome.
 */
export function DuplicateIdentityResolution({ jellyfinUserId, linkTarget, onResolved }: {
  jellyfinUserId: string;
  linkTarget: DeletionTarget & { active: boolean };
  onResolved: (notice: string) => void;
}) {
  const principal = usePrincipal();
  const holder = useIdentityHolder(jellyfinUserId, linkTarget.id);
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState<DeletionOutcome["state"] | null>(null);
  if (!holder || !principal) return null;
  const name = accountName(holder);
  if (!mayManageAccount(principal, holder, "accounts.delete.other")) {
    return <p className="link-hint">{name} holds this Jellyfin identity. You can't delete that account; {holder.role === "admin" ? "only the owner can delete an admin." : "ask the owner."}</p>;
  }
  const destination = linkTarget.active ? { id: linkTarget.id, name: linkTarget.name } : undefined;
  return <div className="duplicate-identity">
    {destination
      ? <p className="link-hint">{name} holds this Jellyfin identity. Review deleting it, or transferring its reviews and exports to {destination.name} or another account. Then prove the identity again.</p>
      : <p className="link-hint">{name} holds this Jellyfin identity. Review deleting it, then prove the identity again. {linkTarget.name} can't receive its data until it is linked.</p>}
    <div className="button-row"><button type="button" className="btn-secondary" onClick={() => setOpen(true)}>{destination ? `Review deleting or transferring ${name}` : `Review deleting ${name}`}</button></div>
    {open && <AccountDeletionDialog target={{ id: holder.id, name }} destination={destination}
      onClose={() => { setOpen(false); if (outcome) onResolved(outcome === "deleted" ? `${name} was deleted. Prove the Jellyfin identity again to review a fresh link preview.` : `Editarr did not confirm whether ${name} was deleted. Prove the Jellyfin identity again to see whether it is still in use.`); }}
      onDeleted={next => setOutcome(next.state)} />}
  </div>;
}
