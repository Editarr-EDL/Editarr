import { useState } from "react";
import { transferOwnership } from "../../api/api-user-access";
import type { AccountDetail } from "../../api/api-users";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import type { Principal } from "../../auth/session";
import { mayManageAccount } from "./account-actions";

export function OwnershipTransfer({ account, principal, onTransferred }: { account: AccountDetail; principal: Principal; onTransferred: () => void }) {
  const [pending, setPending] = useState(false);
  if (principal.role !== "owner" || !mayManageAccount(principal, account, "ownership.transfer")) return null;
  const eligible = account.successor_refusal === null;
  return <section className="account-policy-section" aria-label="Ownership transfer"><h3>Ownership transfer</h3>
    {!eligible && <p>The successor must be active and linked to Jellyfin.</p>}
    <button className="danger-button" disabled={!eligible} onClick={() => setPending(true)}>Transfer ownership</button>
    {pending && <FreshAuthenticationDialog title="Transfer ownership?" body="You will become an admin. The successor becomes the sole owner and its enforced filter policy is cleared." actionLabel="Transfer ownership" danger onClose={() => setPending(false)} onConfirm={async password => {
      await transferOwnership(account.id, password); setPending(false); onTransferred(); window.dispatchEvent(new Event("focus"));
    }} />}
  </section>;
}