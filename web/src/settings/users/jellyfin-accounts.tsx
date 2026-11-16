import { useEffect, useState } from "react";
import { importJellyfinAccounts, listJellyfinAccounts, readJellyfinSync, runJellyfinSync, type JellyfinCandidate, type JellyfinImportResult, type JellyfinSyncRun, type JellyfinSyncStatus } from "../../api/api-jellyfin-accounts";
import { can, type Principal } from "../../auth/session";
import { ModalDialog } from "../../ui/ModalDialog";
import { accountDate } from "./account-actions";

export function JellyfinAccounts({ principal, onChanged }: { principal: Principal; onChanged: () => void }) {
  const mayImport = can(principal, "accounts.jellyfin.import");
  const maySync = can(principal, "accounts.jellyfin.sync");
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  if (!mayImport && !maySync) return null;
  return <section className="account-section" aria-labelledby="jellyfin-accounts-heading"><h3 id="jellyfin-accounts-heading">Jellyfin accounts</h3>
    {mayImport && <button className="btn-secondary" onClick={() => setImporting(true)}>Import Jellyfin users</button>}
    {maySync && <JellyfinSync onChanged={onChanged} revision={revision} />}
    {importing && <JellyfinImport onClose={() => setImporting(false)} onChanged={() => { setRevision((value) => value + 1); onChanged(); }} />}
  </section>;
}

function JellyfinImport({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [users, setUsers] = useState<JellyfinCandidate[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [results, setResults] = useState<JellyfinImportResult[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [all, setAll] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void listJellyfinAccounts(controller.signal).then((value) => { if (!controller.signal.aborted) setUsers(value.users); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to list Jellyfin users."); });
    return () => controller.abort();
  }, [reload]);
  async function run(selection: { all: true } | { user_ids: string[] }) {
    if (busy) return;
    setBusy(true); setError(""); setResults([]);
    try { const next = await importJellyfinAccounts(selection); setResults(next.results); setSelected([]); onChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to import Jellyfin users."); }
    finally { setBusy(false); setAll(false); }
  }
  return <><ModalDialog labelledBy="jellyfin-import-heading" className="account-dialog" onClose={busy || all ? undefined : onClose}>
    <h2 id="jellyfin-import-heading">Import Jellyfin users</h2>
    {error && <div role="alert"><p>{error}</p>{!users && <button className="btn-secondary" onClick={() => { setError(""); setReload(reload + 1); }}>Retry Jellyfin users</button>}</div>}
    {!users && !error && <p role="status">Loading Jellyfin users...</p>}
    {users && <><ul className="jellyfin-import-list">{users.map((user) => <li key={user.jellyfin_user_id}><label className="account-checkbox"><input type="checkbox" disabled={busy || user.is_disabled || user.blocked} checked={selected.includes(user.jellyfin_user_id)} onChange={(event) => setSelected(event.target.checked ? [...selected, user.jellyfin_user_id] : selected.filter((id) => id !== user.jellyfin_user_id))} /><span>{user.name} {user.is_administrator ? "(administrator)" : ""}{user.is_disabled ? " (disabled in Jellyfin)" : ""}{user.user_id !== null ? " (already linked)" : ""}{user.blocked ? " (blocked; unblock them in Users first)" : ""}</span></label></li>)}</ul>
      {users.length === 0 && <p>No Jellyfin users found.</p>}
      <div className="button-row"><button className="btn-primary" disabled={busy || !selected.length} onClick={() => void run({ user_ids: selected })}>Import selected</button><button className="btn-secondary" disabled={busy || !users.length} onClick={() => setAll(true)}>Import all</button></div>
    </>}
    {results.length > 0 && <section aria-label="Import results"><h3>Import results</h3><ul>{results.map((result) => <li key={result.jellyfin_user_id}>{result.name || result.jellyfin_user_id}: {result.outcome === "blocked" ? "blocked; unblock them in Users first" : result.outcome}{result.user_id !== null ? ` (account ${result.user_id})` : ""}</li>)}</ul></section>}
    <div className="button-row"><button className="btn-secondary" disabled={busy} onClick={onClose}>Close</button></div>
  </ModalDialog>
    {all && <ModalDialog labelledBy="jellyfin-import-all-heading" className="confirmation-dialog" onClose={busy ? undefined : () => setAll(false)}><h2 id="jellyfin-import-all-heading">Import all Jellyfin users?</h2><p>Disabled upstream accounts and blocked users are skipped. Existing linked accounts are refreshed.</p><div className="button-row"><button className="btn-secondary" disabled={busy} onClick={() => setAll(false)}>Cancel</button><button className="btn-primary" disabled={busy} onClick={() => void run({ all: true })}>Confirm import all</button></div></ModalDialog>}
  </>;
}

function latestRun(previous: JellyfinSyncRun | null, next: JellyfinSyncRun | null): JellyfinSyncRun | null {
  if (!previous) return next;
  if (!next) return previous;
  return Date.parse(next.finished_at) >= Date.parse(previous.finished_at) ? next : previous;
}

function JellyfinSync({ onChanged, revision }: { onChanged: () => void; revision: number }) {
  const [status, setStatus] = useState<JellyfinSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void readJellyfinSync(controller.signal).then((value) => { if (!controller.signal.aborted) setStatus((current) => ({ ...value, last_run: latestRun(current?.last_run ?? null, value.last_run) })); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read sync status."); });
    return () => controller.abort();
  }, [reload, revision]);
  async function sync() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const next = await runJellyfinSync();
      setStatus((current) => ({ running: false, accounts: current?.accounts ?? [], last_run: latestRun(current?.last_run ?? null, next) }));
      setReload((value) => value + 1); onChanged();
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to sync accounts."); setReload((value) => value + 1); }
    finally { setBusy(false); }
  }
  const results = status?.last_run?.results ?? [];
  return <div className="jellyfin-sync">
    <div className="button-row"><button className="btn-secondary" disabled={busy || status?.running === true} onClick={() => void sync()}>{busy ? "Syncing accounts..." : "Sync Jellyfin now"}</button><button className="btn-secondary" disabled={busy} onClick={() => { setError(""); setReload(reload + 1); }}>Refresh sync status</button></div>
    {status?.running && <p role="status">Jellyfin sync is running.</p>}
    {error && <p role="alert">{error}</p>}
    {status && <><p>Last completed run: {status.last_run ? accountDate(status.last_run.finished_at) : "Never"}</p>
      {status.accounts.length === 0 ? <p>No linked accounts.</p> : <ul>{status.accounts.map((account) => <li key={account.user_id}>{account.name || `Account ${account.user_id}`}<dl className="account-facts"><div><dt>Link state</dt><dd>{account.link_state}</dd></div><div><dt>Last successful verification</dt><dd>{accountDate(account.last_verified_at)}</dd></div><div><dt>Last attempt</dt><dd>{accountDate(account.last_attempt_at)}{account.last_outcome ? ` (${account.last_outcome})` : ""}</dd></div>{account.last_failure && <div><dt>Failure</dt><dd>{account.last_failure}</dd></div>}</dl></li>)}</ul>}
    </>}
    {results.length > 0 && <section aria-label="Sync results"><h4>Sync results</h4><ul>{results.map((result) => <li key={result.user_id}>{status?.accounts.find((account) => account.user_id === result.user_id)?.name || `Account ${result.user_id}`}: {result.outcome}, {result.link_state}, {result.role}, {result.status}{result.failure ? ` (${result.failure})` : ""}</li>)}</ul></section>}
  </div>;
}