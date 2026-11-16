import { useEffect, useState } from "react";
import { listBlockedJellyfinUsers, unblockJellyfinUser, type BlockedJellyfinUser } from "../../api/api-jellyfin-accounts";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import { can, type Principal } from "../../auth/session";
import { accountDate } from "./account-actions";

const blockName = (block: BlockedJellyfinUser) => block.name ?? `Jellyfin user ${block.jellyfin_user_id}`;

/** Jellyfin users kept out when their accounts were deleted. Rereads whenever `revision` changes. */
export function BlockedJellyfinUsers({ principal, revision }: { principal: Principal; revision: number }) {
  const mayList = can(principal, "accounts.list");
  const mayUnblock = can(principal, "accounts.jellyfin.unblock");
  const [blocks, setBlocks] = useState<BlockedJellyfinUser[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [unblocking, setUnblocking] = useState<BlockedJellyfinUser | null>(null);
  useEffect(() => {
    if (!mayList) return;
    const controller = new AbortController();
    void listBlockedJellyfinUsers(controller.signal).then((value) => { if (!controller.signal.aborted) { setBlocks(value.blocks); setError(""); } })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load blocked Jellyfin users."); });
    return () => controller.abort();
  }, [mayList, revision]);
  if (!mayList || (blocks.length === 0 && !error && !message)) return null;
  return <section className="account-section" aria-labelledby="jellyfin-blocks-heading"><h3 id="jellyfin-blocks-heading">Blocked Jellyfin users</h3>
    <p>These Jellyfin users can't sign in or be imported. Unblocking lets their next sign-in create a fresh account.</p>
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {blocks.length > 0 && <ul className="jellyfin-block-list">{blocks.map((block) => <li key={`${block.server_id}/${block.jellyfin_user_id}`}>
      <span>{blockName(block)}</span> <span className="account-field-label">Blocked {accountDate(block.blocked_at)}</span>
      {mayUnblock && <button type="button" className="btn-secondary" aria-label={`Unblock ${blockName(block)}`} onClick={() => { setMessage(""); setUnblocking(block); }}>Unblock</button>}
    </li>)}</ul>}
    {unblocking && <FreshAuthenticationDialog title={`Unblock ${blockName(unblocking)}`}
      body="They can sign in with Jellyfin again, and their next sign-in creates a fresh Editarr account with the role Jellyfin gives them."
      actionLabel="Unblock"
      onClose={() => setUnblocking(null)}
      onConfirm={async (password) => {
        const next = await unblockJellyfinUser(unblocking, password);
        setBlocks(next.blocks); setMessage(`${blockName(unblocking)} is unblocked.`); setUnblocking(null);
      }} />}
  </section>;
}
