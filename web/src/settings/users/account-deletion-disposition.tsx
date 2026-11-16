import { useEffect, useId, useState, type FormEvent } from "react";
import type { DeletionMode, Disposition } from "../../api/api-account-deletion";
import { listAccounts, type AccountSummary } from "../../api/api-users";
import { accountName } from "../profile/profile-editor";
import { MODES } from "./account-deletion-copy";

type Destination = { id: number; name: string };

/**
 * Choose retain, transfer, or permanent deletion. Nothing is preselected; a transfer
 * needs an active destination that is not being deleted. The server decides which
 * destinations it accepts; this list only offers the accounts the caller can see.
 */
export function DispositionPicker({ excluded, initial, notice, actionLabel = "Review impact", onReview, onCancel }: {
  excluded: number[];
  initial?: { mode?: DeletionMode; destination?: Destination };
  notice: string | null;
  actionLabel?: string;
  onReview: (disposition: Disposition) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [mode, setMode] = useState<DeletionMode | null>(initial?.mode ?? null);
  const [destination, setDestination] = useState<Destination | null>(initial?.destination ?? null);
  const [error, setError] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!mode) { setError("Choose what happens to the account's data."); return; }
    if (mode === "transfer" && !destination) { setError("Choose the account that receives the data."); return; }
    onReview(mode === "transfer" ? { mode, destination: destination! } : { mode });
  }
  return <form className="deletion-disposition" onSubmit={submit} noValidate>
    {notice && <p role="alert" className="form-status">{notice}</p>}
    <fieldset>
      <legend>What happens to the account's data</legend>
      {(Object.keys(MODES) as DeletionMode[]).map(option => <label key={option} className="deletion-mode">
        <input type="radio" name={`${id}-mode`} value={option} checked={mode === option} onChange={() => { setMode(option); setError(""); }} />
        <span><strong>{MODES[option].label}</strong><small>{MODES[option].summary}</small></span>
      </label>)}
    </fieldset>
    {mode === "transfer" && <DestinationSearch excluded={excluded} value={destination} onChange={next => { setDestination(next); setError(""); }} />}
    {error && <p role="alert" className="form-status">{error}</p>}
    <div className="button-row"><button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button><button type="submit" className="btn-primary">{actionLabel}</button></div>
  </form>;
}

function DestinationSearch({ excluded, value, onChange }: { excluded: number[]; value: Destination | null; onChange: (next: Destination | null) => void }) {
  const id = useId();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<AccountSummary[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setOptions(null); setError("");
    void listAccounts(query, 0, controller.signal)
      .then(page => { if (!controller.signal.aborted) setOptions(page.items.filter(account => account.status === "active" && !excluded.includes(account.id))); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load accounts."); });
    return () => controller.abort();
  }, [query, excluded.join(",")]);
  const listed = options ?? [];
  const choices = value && !listed.some(account => account.id === value.id) ? [value, ...listed.map(account => ({ id: account.id, name: accountName(account) }))] : listed.map(account => ({ id: account.id, name: accountName(account) }));
  return <div className="deletion-destination">
    <div className="deletion-destination-search">
      <label className="field" htmlFor={`${id}-search`}><span>Find a destination account</span>
        <input id={`${id}-search`} type="search" maxLength={100} value={search} onChange={event => setSearch(event.target.value)}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); setQuery(search.trim()); } }} /></label>
      <button type="button" className="btn-secondary" onClick={() => setQuery(search.trim())}>Search accounts</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {!options && !error && <p role="status">Loading accounts...</p>}
    {options && <label className="field" htmlFor={`${id}-destination`}><span>Destination account</span>
      <select id={`${id}-destination`} value={value?.id ?? ""} onChange={event => onChange(choices.find(choice => choice.id === Number(event.target.value)) ?? null)}>
        <option value="">Choose an account</option>
        {choices.map(choice => <option key={choice.id} value={choice.id}>{choice.name}</option>)}
      </select></label>}
    {options && choices.length === 0 && <p>No active account matches. Accounts being deleted can't receive data.</p>}
  </div>;
}
