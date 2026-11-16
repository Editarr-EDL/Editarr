import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/api-transport";
import { bulkMedia, bulkRoles, readFilterPreferences, saveFilterMode, type AssignedRole, type FilterMode, type ScopeChoice } from "../../api/api-user-access";
import type { AccountSummary, BulkAccountResult } from "../../api/api-users";
import { useOptionalAuthenticationGate } from "../../auth/auth-gate";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import { useOperationReceipt } from "../../auth/operation-receipts";
import type { Principal } from "../../auth/session";
import { ModalDialog } from "../../ui/ModalDialog";
import { accountName } from "../profile/profile-editor";
import { mayManageAccount } from "./account-actions";
import { MediaScopeEditor } from "./media-scope-editor";

type AccessResult = BulkAccountResult | { user_id: number; outcome: "unknown"; code: null };

export function BulkAccess({ selected, principal, onChanged }: { selected: AccountSummary[]; principal: Principal; onChanged: () => void }) {
  const gate = useOptionalAuthenticationGate();
  const readLifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    readLifetime.current = controller;
    return () => controller.abort();
  }, []);
  const [action, setAction] = useState<"role" | "media" | "mode" | null>(null);
  const [role, setRole] = useState<AssignedRole>("user");
  const [mode, setMode] = useState<FilterMode>("live");
  const [choice, setChoice] = useState<ScopeChoice | null>(null);
  const [proof, setProof] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setResults] = useOperationReceipt<(AccessResult & { name: string })[]>("users.bulk-access");
  const results = receipt ?? [];
  const mayRole = principal.role === "owner" && selected.some(account => mayManageAccount(principal, account, "roles.assign"));
  const mayMedia = selected.some(account => mayManageAccount(principal, account, "media_access.user.edit"));
  const mayMode = selected.some(account => mayManageAccount(principal, account, "filters.mode.assign.other"));
  function finish(next: AccessResult[]) {
    setResults(next.map(result => ({ ...result, name: accountName(selected.find(account => account.id === result.user_id) ?? { id: result.user_id, display_name: null, username: null }) })));
    setAction(null); setProof(false); setChoice(null); onChanged();
  }
  async function assignModes() {
    const signal = readLifetime.current!.signal;
    const isCurrent = () => !signal.aborted && (gate?.isAuthenticationCurrent() ?? true);
    setBusy(true); const next: AccessResult[] = [];
    try {
      for (const account of selected) {
        if (!isCurrent()) break;
        let writeStarted = false;
        try {
          const current = await readFilterPreferences(account.id, signal);
          if (!isCurrent()) break;
          writeStarted = true;
          await saveFilterMode(account.id, current.revision, mode);
          next.push({ user_id: account.id, outcome: isCurrent() ? "applied" : "unknown", code: null });
        } catch (reason) {
          if (!isCurrent()) {
            if (writeStarted) next.push({ user_id: account.id, outcome: "unknown", code: null });
            else if (reason instanceof ApiError) next.push({ user_id: account.id, outcome: "refused", code: reason.code ?? `http_${reason.status}` });
            break;
          }
          next.push(reason instanceof ApiError
            ? { user_id: account.id, outcome: "refused", code: reason.code ?? `http_${reason.status}` }
            : { user_id: account.id, outcome: "unknown", code: null });
        }
      }
      finish(next);
    } finally { setBusy(false); }
  }
  return <>
    {selected.length > 0 && <div className="button-row">
      {mayRole && <button className="btn-secondary" onClick={() => setAction("role")}>Assign selected roles</button>}
      {mayMedia && <button className="btn-secondary" onClick={() => setAction("media")}>Set selected media access</button>}
      {mayMode && <button className="btn-secondary" onClick={() => setAction("mode")}>Set selected filter modes</button>}
    </div>}
    {results.length > 0 && <section aria-label="Bulk access results"><h3>Bulk access results</h3><ul>{results.map(result => <li key={result.user_id}>{result.name}: {result.outcome === "unknown" ? "outcome unknown" : result.outcome}{result.code ? ` (${result.code})` : ""}</li>)}</ul></section>}
    {action && <ModalDialog labelledBy="bulk-access-heading" className="confirmation-dialog" onClose={busy || proof ? undefined : () => { setAction(null); setChoice(null); }}>
      <h2 id="bulk-access-heading">{action === "role" ? "Assign selected roles" : action === "media" ? "Set selected media access" : "Set selected filter modes"}</h2>
      <ul>{selected.map(account => <li key={account.id}>{accountName(account)}</li>)}</ul>
      {action === "role" && <label className="field"><span>Selected accounts role</span><select disabled={busy || proof} value={role} onChange={event => setRole(event.target.value as AssignedRole)}><option value="user">User</option><option value="admin">Admin</option></select></label>}
      {action === "mode" && <label className="field"><span>Selected accounts filter mode</span><select disabled={busy} value={mode} onChange={event => setMode(event.target.value as FilterMode)}><option value="live">Live</option><option value="copy">Copy</option></select></label>}
      {action === "media" && <MediaScopeEditor scope={{ inherited: true, all_sources: false, sources: [] }} allowInherit busy={proof} onSave={next => { setChoice(next); setProof(true); }} />}
      <div className="button-row"><button className="btn-secondary" disabled={busy || proof} onClick={() => setAction(null)}>Cancel</button>{action !== "media" && <button className="btn-primary" disabled={busy || proof} onClick={() => action === "mode" ? void assignModes() : setProof(true)}>Confirm selected changes</button>}</div>
    </ModalDialog>}
    {proof && action && <FreshAuthenticationDialog title="Confirm selected account changes" body="Each selected target is checked separately. Refused targets remain unchanged. Targets with unknown outcomes may have changed." actionLabel="Apply selected changes" onClose={() => { setProof(false); setChoice(null); }} onConfirm={async password => {
      const ids = selected.map(account => account.id);
      const next = action === "role" ? await bulkRoles(ids, role, password) : await bulkMedia(ids, choice!, password);
      finish(next.results);
    }} />}
  </>;
}
