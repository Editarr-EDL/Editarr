import { useEffect, useState, type FormEvent } from "react";
import { bulkAccountStatus, listAccounts, type AccountPage, type AccountStatus, type AccountSummary, type BulkAccountResult } from "../../api/api-users";
import { useOptionalAuthenticationGate, usePrincipal } from "../../auth/auth-gate";
import { can, type Principal } from "../../auth/session";
import { ModalDialog } from "../../ui/ModalDialog";
import { AccountAvatar, accountName, jellyfinUsername } from "../profile/profile-editor";
import { AccountDetailsDialog } from "./account-details";
import { accountDate, accountStatusLabel, mayEnable, mayLinkOther, mayManageAccount } from "./account-actions";
import { AccountLinkDialog, type LinkTarget } from "./account-link-dialog";
import { BlockedJellyfinUsers } from "./jellyfin-blocks";
import { JellyfinAccounts } from "./jellyfin-accounts";
import { AccessDefaults } from "./access-defaults";
import { BulkAccess } from "./bulk-access";
import { navigate } from "../../routes";
import { notifyScansChanged } from "../../shared/scan-events";
import { AccountDeletionDialog, type DeletionTarget } from "./account-deletion-dialog";
import { BulkAccountDeletion } from "./account-deletion-bulk";
import { DeletionOutcomes, useDeletionOutcomes, type DeletionOutcome } from "./account-deletion-outcomes";
import { useDeletionDeepLink } from "./account-deletion-link";

/** The Users directory. `deleteAccountId` comes from the `#/settings/users/{id}/delete` deep link. */
export function UsersView({ deleteAccountId }: { deleteAccountId?: number }) {
  const principal = usePrincipal();
  return principal && can(principal, "accounts.list") ? <UsersDirectory principal={principal} deleteAccountId={deleteAccountId} /> : null;
}

function UsersDirectory({ principal, deleteAccountId }: { principal: Principal; deleteAccountId?: number }) {
  const gate = useOptionalAuthenticationGate();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState<AccountPage | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [target, setTarget] = useState<AccountSummary | null>(null);
  const [linking, setLinking] = useState<LinkTarget | null>(null);
  const [bulk, setBulk] = useState<AccountStatus | null>(null);
  const [results, setResults] = useState<BulkAccountResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<DeletionTarget | null>(null);
  const [outcomes, saveOutcomes] = useDeletionOutcomes();
  const deepLink = useDeletionDeepLink(principal, deleteAccountId, setDeleting);
  useEffect(() => {
    const controller = new AbortController();
    setPage(null); setError(""); setSelected([]);
    void listAccounts(query, offset, controller.signal).then((next) => { if (!controller.signal.aborted) setPage(next); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load users."); });
    return () => controller.abort();
  }, [query, offset, reload]);
  function refresh() { setReload((value) => value + 1); }
  function deletedAccounts(next: DeletionOutcome[]) { saveOutcomes(next); refresh(); notifyScansChanged(); gate?.refreshAuthenticationStatus(); }
  function submitSearch(event: FormEvent) { event.preventDefault(); setOffset(0); setQuery(search.trim()); refresh(); }
  const maySelect = (account: AccountSummary) => (["accounts.disable.other", "accounts.enable.other", "media_access.user.edit", "filters.mode.assign.other", "roles.assign", "accounts.delete.other"] as const).some(capability => mayManageAccount(principal, account, capability));
  const selectedAccounts = page?.items.filter((account) => selected.includes(account.id)) ?? [];
  const mayBulk = (status: AccountStatus) => selectedAccounts.some((account) => status === "active" ? mayEnable(principal, account) : mayManageAccount(principal, account, "accounts.disable.other"));
  async function applyBulk() {
    if (!bulk || busy) return;
    setBusy(true); setError("");
    try { const next = await bulkAccountStatus(selected, bulk); setResults(next.results); setBulk(null); refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to update selected accounts."); }
    finally { setBusy(false); }
  }
  return <div className="account-page users-page">
    <div className="section-heading"><h1>Users</h1><div className="button-row">
      <button className="btn-secondary" onClick={refresh}>Refresh users</button>
    </div></div>
    <form className="users-search" onSubmit={submitSearch}><label className="field"><span>Search users</span><input type="search" value={search} maxLength={100} onChange={(event) => setSearch(event.target.value)} /></label><button type="submit" className="btn-secondary">Search</button></form>
    {error && <p role="alert">{error}</p>}
    {deepLink && <p role="alert">{deepLink}</p>}
    {!page && !error && <p role="status">Loading users...</p>}
    {page && <>
      <div className="users-table-scroll" role="region" aria-label="User directory" tabIndex={0}><table className="users-table">
        <thead><tr><th scope="col">Select</th><th scope="col">Name</th><th scope="col">Username</th><th scope="col">Source</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Last login</th><th scope="col">Created</th><th scope="col">Overrides</th><th scope="col">Account</th></tr></thead>
        <tbody>{page.items.map((account) => <tr key={account.id}>
          <td>{maySelect(account) && <input type="checkbox" aria-label={`Select ${accountName(account)}`} checked={selected.includes(account.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, account.id] : selected.filter((id) => id !== account.id))} />}</td>
          <th scope="row"><span className="account-identity"><AccountAvatar account={account} /><span>{accountName(account)}</span></span></th>
          <td>{account.source === "jellyfin" ? jellyfinUsername(account) : account.username ?? "No Jellyfin link"}</td><td>{account.source === "jellyfin" ? "Jellyfin" : "Local"}</td><td>{account.role}</td><td>{accountStatusLabel(account)}</td><td>{accountDate(account.last_login_at)}</td><td>{accountDate(account.created_at)}</td>
          <td>{([account.overrides.permissions && "Permissions", account.overrides.media_access && "Media access", account.overrides.filter_policy && "Filter policy"].filter(Boolean).join(", ")) || "None"}</td>
          <td><button className="btn-secondary" aria-label={`View ${accountName(account)}`} onClick={() => setTarget(account)}>View</button>{mayLinkOther(principal, account) && <button className="btn-secondary" aria-label={`Link ${accountName(account)} to Jellyfin`} onClick={() => setLinking({ id: account.id, name: accountName(account), own: false, kind: "link" })}>Link to Jellyfin</button>}{mayManageAccount(principal, account, "accounts.delete.other") && <button className="danger-button" aria-label={`Delete ${accountName(account)}`} onClick={() => setDeleting({ id: account.id, name: accountName(account) })}>Delete</button>}</td>
        </tr>)}</tbody>
      </table></div>
      {page.items.length === 0 && <p>No users found.</p>}
      <div className="users-pagination"><span>{page.total ? `${page.offset + 1}-${Math.min(page.offset + page.items.length, page.total)} of ${page.total}` : "0 users"}</span><div className="button-row"><button className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous page</button><button className="btn-secondary" disabled={offset + page.limit >= page.total} onClick={() => setOffset(offset + 20)}>Next page</button></div></div>
      {selected.length > 0 && <div className="button-row"><span>{selected.length} selected</span>{mayBulk("active") && <button className="btn-secondary" onClick={() => setBulk("active")}>Enable selected</button>}{mayBulk("disabled") && <button className="danger-button" onClick={() => setBulk("disabled")}>Disable selected</button>}</div>}
    </>}
    {results.length > 0 && <section aria-label="Bulk results"><h3>Bulk results</h3><ul>{results.map((result) => {
      const account = page?.items.find((item) => item.id === result.user_id);
      return <li key={result.user_id}>{account ? accountName(account) : `Account ${result.user_id}`}: {result.outcome}{result.code ? ` (${result.code})` : ""}</li>;
    })}</ul></section>}
    <DeletionOutcomes outcomes={outcomes ?? []} />
    <BulkAccountDeletion selected={selectedAccounts} principal={principal} onDeleted={deletedAccounts} />
    <BulkAccess selected={selectedAccounts} principal={principal} onChanged={refresh} />
    <AccessDefaults principal={principal} />
    <JellyfinAccounts principal={principal} onChanged={refresh} />
    <BlockedJellyfinUsers principal={principal} revision={reload} />
    {linking && <AccountLinkDialog target={linking} onClose={() => setLinking(null)} onAccountDeleted={refresh} onLinked={refresh} />}
    {deleting && <AccountDeletionDialog target={deleting} onClose={() => { setDeleting(null); if (deleteAccountId !== undefined) navigate("settings/users"); }}
      onDeleted={outcome => deletedAccounts([outcome])} />}
    {target && <AccountDetailsDialog key={target.id} target={target} principal={principal} onClose={() => setTarget(null)} onChanged={refresh} onDeleted={deletedAccounts} />}
    {bulk && <ModalDialog labelledBy="bulk-account-heading" className="confirmation-dialog" onClose={busy ? undefined : () => setBulk(null)}><h2 id="bulk-account-heading">{bulk === "active" ? "Enable" : "Disable"} selected accounts?</h2><p>{selected.length} accounts. Each target is checked separately. Data and keys are retained.</p>{error && <p role="alert">{error}</p>}<div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => setBulk(null)}>Cancel</button><button className="danger-button" disabled={busy} onClick={() => void applyBulk()}>Confirm</button></div></ModalDialog>}
  </div>;
}