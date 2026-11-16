import { useEffect, useState } from "react";
import { ApiError } from "../../api/api-transport";
import { readAccount, type AccountSummary } from "../../api/api-users";
import { can, type Principal } from "../../auth/session";
import { accountName } from "../profile/profile-editor";
import { mayManageAccount } from "./account-actions";
import type { DeletionTarget } from "./account-deletion-dialog";

function refusal(principal: Principal, account: AccountSummary): string {
  if (account.id === principal.userId) return "You can't delete your own account.";
  if (account.role === "owner") return `${accountName(account)} is the owner and can't be deleted.`;
  if (account.role === "admin") return `${accountName(account)} is an admin. Only the owner can delete an admin account.`;
  return `You can't delete ${accountName(account)}.`;
}

/**
 * Open the deletion dialog named by `#/settings/users/{id}/delete` only for an account the
 * caller may delete; protected, own, and unknown accounts get a message and no preview.
 * @returns The refusal message to show, if any.
 */
export function useDeletionDeepLink(principal: Principal, id: number | undefined, open: (target: DeletionTarget) => void): string | null {
  const [message, setMessage] = useState<string | null>(null);
  const mayDelete = can(principal, "accounts.delete.other");
  useEffect(() => {
    setMessage(null);
    if (id === undefined) return;
    if (!mayDelete) { setMessage("You don't have permission to delete accounts."); return; }
    const controller = new AbortController();
    void readAccount(id, controller.signal).then(account => {
      if (controller.signal.aborted) return;
      if (mayManageAccount(principal, account, "accounts.delete.other")) open({ id: account.id, name: accountName(account) });
      else setMessage(refusal(principal, account));
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setMessage(reason instanceof ApiError && reason.status === 404 ? "That account doesn't exist or you can't see it." : reason instanceof Error ? reason.message : "Unable to read the account.");
    });
    return () => controller.abort();
  }, [id, principal.userId, principal.role, mayDelete]);
  return message;
}
