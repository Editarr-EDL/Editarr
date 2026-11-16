import { useEffect, useState } from "react";
import type { FilterDefaultsSettings, McfCategory } from "../../api/api-types";
import { fetchFilterDefaultsSettings } from "../../api/api-service";
import { readFilterPreferences, saveFilterMode, saveFilterPolicy, type FilterMode, type FilterPreferences } from "../../api/api-user-access";
import type { AccountDetail } from "../../api/api-users";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import type { Principal } from "../../auth/session";
import { mayManageAccount } from "./account-actions";
import { PolicySection } from "./policy-section";

export function AccountFilterPolicy({ account, principal, onChanged }: { account: AccountDetail; principal: Principal; onChanged: () => void }) {
  const mode = mayManageAccount(principal, account, "filters.mode.assign.other");
  const enforce = principal.role === "owner" && mayManageAccount(principal, account, "filters.policy.enforce");
  return mode || enforce ? <PolicySection title="Filter mode and policy"><FilterPolicyEditor key={`${account.id}:${enforce}`} id={account.id} modeAllowed={mode} enforceAllowed={enforce} onChanged={onChanged} /></PolicySection> : null;
}
function FilterPolicyEditor({ id, modeAllowed, enforceAllowed, onChanged }: { id: number; modeAllowed: boolean; enforceAllowed: boolean; onChanged: () => void }) {
  const [view, setView] = useState<FilterPreferences | null>(null);
  const [instance, setInstance] = useState<FilterDefaultsSettings | null>(null);
  const [mode, setMode] = useState<FilterMode>("live");
  const [rules, setRules] = useState<string[]>([]);
  const [severities, setSeverities] = useState<McfCategory[]>([]);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);
  function accept(next: FilterPreferences) {
    setView(next); setMode(next.mode);
    setRules(next.enforced_rules); setSeverities(next.enforced_severities);
  }
  useEffect(() => {
    const controller = new AbortController();
    setView(null); setError("");
    void Promise.all([readFilterPreferences(id, controller.signal), enforceAllowed ? fetchFilterDefaultsSettings() : Promise.resolve(null)]).then(([next, defaults]) => {
      if (!controller.signal.aborted) { accept(next); setInstance(defaults); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read filter policy."); });
    return () => controller.abort();
  }, [id, enforceAllowed, reload]);
  async function assignMode() {
    if (!view || busy) return;
    setBusy(true); setError(""); setMessage("");
    try { accept(await saveFilterMode(id, view.revision, mode)); setMessage("Filter mode saved."); onChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to assign mode."); }
    finally { setBusy(false); }
  }
  return <>
    {error && <p role="alert">{error}</p>}
    <button className="btn-secondary" disabled={busy || pending} onClick={() => setReload(reload + 1)}>Reload filter policy</button>
    {!view && !error && <p role="status">Loading filter policy...</p>}
    {view && <>
      <p>Filter mode: {view.mode}. Policy revision: {view.policy_revision}. {view.mode_locked ? "Self-service mode locked." : "Self-service mode unlocked."}</p>
      {modeAllowed && <div className="button-row"><label className="field"><span>Assigned filter mode</span><select disabled={busy} value={mode} onChange={event => setMode(event.target.value as FilterMode)}><option value="live">Live</option><option value="copy">Copy</option></select></label><button className="btn-secondary" disabled={busy} onClick={() => void assignMode()}>Save filter mode</button></div>}
      {enforceAllowed && instance && <>
        <fieldset disabled={pending || busy}><legend>Enforced instance rules</legend>{instance.selection_rules.map(rule => <label className="checkbox-row" key={rule.id}><input type="checkbox" checked={rules.includes(rule.id)} onChange={event => setRules(event.target.checked ? [...rules, rule.id] : rules.filter(value => value !== rule.id))} /><span>{rule.id}: {rule.disposition} {rule.subcategory ?? rule.category ?? "all categories"} {rule.label_contains ?? ""}</span></label>)}{instance.selection_rules.length === 0 && <p>No instance rules.</p>}</fieldset>
        <details><summary>Enforced instance severities</summary><fieldset disabled={pending || busy}><legend>Severity enforcement</legend>{instance.severity_defaults.map(entry => <label className="checkbox-row" key={entry.subcategory}><input type="checkbox" checked={severities.includes(entry.subcategory)} onChange={event => setSeverities(event.target.checked ? [...severities, entry.subcategory] : severities.filter(value => value !== entry.subcategory))} /><span>{entry.subcategory}: {entry.severity}</span></label>)}</fieldset></details>
        <button className="btn-secondary" disabled={pending || busy} onClick={() => setPending(true)}>Save enforced policy</button>
      </>}
      <details><summary>Effective filter values</summary><ul>{view.rules.map(entry => <li key={entry.rule.id}>{entry.rule.id}: {entry.rule.disposition}, {entry.origin}{entry.edited ? ", edited" : ""}{entry.disabled ? ", disabled" : ""}{entry.enforced ? ", enforced" : ""}</li>)}</ul><ul>{view.severities.map(entry => <li key={entry.subcategory}>{entry.subcategory}: {entry.severity}, {entry.origin}{entry.enforced ? ", enforced" : ""}</li>)}</ul></details>
    </>}
    {message && <p role="status">{message}</p>}
    {pending && view && <FreshAuthenticationDialog title="Save enforced filter policy" body="The selected instance values remain enforced in live and copy mode." actionLabel="Confirm enforced policy" onClose={() => setPending(false)} onConfirm={async password => {
      accept(await saveFilterPolicy(id, view.policy_revision, rules, severities, password)); setPending(false); setMessage("Enforced policy saved."); onChanged();
    }} />}
  </>;
}