import { useEffect, useRef, useState } from "react";

import { fetchFilterDefaultsSettings } from "../../api/api-service";
import { loadMcfRegistry } from "../../shared/mcf-registry";
import { choosePersonalFilterMode, personalFilterUpdate, readPersonalFilters, savePersonalFilters } from "../../api/api-personal-filters";
import type { FilterMode, FilterPreferences } from "../../api/api-user-access";
import type { FilterDefaultsSettings, McfRegistry } from "../../api/api-mcf-types";
import { ApiError } from "../../api/api-transport";
import { useCan, useOptionalAuthenticationGate } from "../../auth/auth-gate";
import { filterRuleIsValid } from "../filter-rule-fields";
import { RulesHelp, SeverityHelp } from "./filter-help";
import { personalHeatCells } from "./heat-cells";
import { ModeChoice, type ModeOption } from "./mode-choice";
import { editDraft, ruleBadge, type DraftEdit } from "./my-filter-draft";
import { RuleList, type RuleCardEntry } from "./rule-list";
import { changeCount, SaveBar } from "./save-bar";
import { SeverityHeatMap } from "./severity-heat-map";

const modeOptions: ModeOption[] = [
  { value: "live", title: "Keep me in sync", body: "Server changes apply to you automatically. Anything you change below stays yours." },
  { value: "copy", title: "Use my own copy", body: "Take a one-time copy of the defaults. Later server changes won't affect you." },
];
const modeSaved: Record<FilterMode, string> = { live: "You now keep in sync with the server.", copy: "You now use your own copy." };

/** The My filters tab: the person's mode, severities, and selection rules. */
export function MyFilters() {
  const mayEdit = useCan("filters.preferences.edit.own");
  const mayChooseMode = useCan("filters.mode.choose.own");
  const gate = useOptionalAuthenticationGate();
  const [view, setView] = useState<FilterPreferences | null>(null);
  const [draft, setDraft] = useState<FilterPreferences | null>(null);
  const [registry, setRegistry] = useState<McfRegistry | null>(null);
  const [server, setServer] = useState<FilterDefaultsSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [conflict, setConflict] = useState<FilterPreferences | "unread" | null>(null);
  const lifetime = useRef(true);
  const current = () => lifetime.current && (gate?.isAuthenticationCurrent() ?? true);
  const accept = (next: FilterPreferences) => { setView(next); setDraft(structuredClone(next)); setConflict(null); };
  const readServer = () => fetchFilterDefaultsSettings().then(next => { if (current()) setServer(next); }, () => undefined);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = true;
    void Promise.all([readPersonalFilters(controller.signal), loadMcfRegistry()]).then(([next, categories]) => {
      if (!controller.signal.aborted && current()) { accept(next); setRegistry(categories); }
    }).catch(reason => { if (!controller.signal.aborted && current()) setError(reason instanceof Error ? reason.message : "Unable to read your filters."); });
    void readServer();
    return () => { lifetime.current = false; controller.abort(); };
  }, []);
  async function readLatest() {
    try { const next = await readPersonalFilters(); if (current()) setConflict(next); }
    catch { if (current()) setError("Your changes are still here. Load the latest version before saving."); }
  }
  async function mutate(operation: () => Promise<FilterPreferences>, saved: (next: FilterPreferences) => string) {
    if (!current() || busy || conflict) return;
    setBusy(true); setError(""); setStatus("");
    try {
      const next = await operation();
      if (current()) { accept(next); setStatus(saved(next)); }
    } catch (reason) {
      if (!current()) return;
      setError(reason instanceof Error ? reason.message : "Unable to save your filters.");
      if (reason instanceof ApiError && reason.status === 409) { setConflict("unread"); await readLatest(); }
    } finally { if (current()) setBusy(false); }
  }

  if (!view || !draft || !registry) return <section className="settings-card" aria-label="My filters">{error ? <p role="alert">{error}</p> : <p role="status">Loading your filters...</p>}</section>;
  const edit = (change: DraftEdit) => { setDraft(editDraft(draft, change)); setStatus(""); };
  const changes = changeCount(view.rules, draft.rules, entry => entry.rule.id) + changeCount(view.severities, draft.severities, entry => entry.subcategory);
  const invalid = draft.rules.some(entry => !entry.disabled && !filterRuleIsValid(entry.rule));
  const blocked = busy || Boolean(conflict);
  const cells = personalHeatCells(registry, draft.severities, server?.severity_defaults);
  const serverRule = (id: string) => server?.selection_rules.find(rule => rule.id === id);
  const entries: RuleCardEntry[] = draft.rules.map(entry => {
    const id = entry.rule.id;
    const badge = ruleBadge(entry);
    if (!mayEdit || entry.enforced) return { rule: entry.rule, badge };
    const onChange: RuleCardEntry["onChange"] = patch => edit({ kind: "edit-rule", id, patch });
    if (entry.origin !== "instance") return { rule: entry.rule, badge, onChange, onDelete: () => edit({ kind: "delete-rule", id }) };
    return { rule: entry.rule, badge, onChange, onToggle: () => edit({ kind: "toggle-rule", id }),
      onRestore: entry.edited || entry.disabled ? () => edit({ kind: "restore-rule", id, server: serverRule(id) }) : undefined };
  });
  const modeNote = view.mode_locked ? "An admin requires some of your filters, so they manage this choice for you."
    : !mayChooseMode ? "Your account can't change this choice."
    : changes > 0 ? "Save or discard your changes below before you switch." : null;

  return <div className="filters-tab">
    <ModeChoice heading="When the server defaults change" description="An admin sets filter defaults for everyone. Pick how those defaults reach you."
      options={modeOptions} value={view.mode} disabled={!mayChooseMode || view.mode_locked || blocked || changes > 0}
      note={modeNote && <p className="filter-help" role="note">{modeNote}</p>}
      onChoose={mode => mutate(() => choosePersonalFilterMode(view.revision, mode), next => modeSaved[next.mode])} />
    {error && <p className="form-error" role="alert">{error}</p>}
    {conflict && <section className="review-policy-conflict" aria-label="Filter preference conflict">
      <p>Your filters changed somewhere else. Your unsaved changes are still here, but you need the latest version before you can save.</p>
      {conflict === "unread" ? <button type="button" className="btn-secondary" onClick={() => void readLatest()}>Load the latest version</button>
        : <button type="button" className="btn-secondary" onClick={() => { accept(conflict); setError(""); void readServer(); }}>Discard my changes and use the latest version</button>}
    </section>}
    <div className="filters-columns">
      <section className="settings-card" aria-labelledby="my-severity-heading">
        <div><h2 id="my-severity-heading">Severity</h2><SeverityHelp /></div>
        <SeverityHeatMap cells={cells} showPersonalState disabled={blocked || !mayEdit} hint={mayEdit ? "Click a category to change its level." : undefined}
          onChange={(token, severity) => edit({ kind: "severity", token, severity })}
          onReset={mayEdit ? token => edit({ kind: "reset-severity", token, server: cells.find(cell => cell.token === token)?.server ?? null }) : undefined} />
      </section>
      <section className="settings-card" aria-labelledby="my-rules-heading">
        <div className="filter-card-heading"><h2 id="my-rules-heading">Selection rules</h2>
          {mayEdit && <button type="button" className="btn-secondary compact-button" disabled={blocked} onClick={() => edit({ kind: "add-rule" })}>Add my rule</button>}</div>
        <RulesHelp />
        {entries.length ? <RuleList entries={entries} registry={registry} disabled={blocked} /> : <p className="filter-help">No rules yet.</p>}
      </section>
    </div>
    {mayEdit && <SaveBar count={changes} saving={busy} blocked={blocked} invalid={invalid} status={status}
      onDiscard={() => { setDraft(structuredClone(view)); setStatus(""); }}
      onSave={() => void mutate(() => savePersonalFilters(personalFilterUpdate(view, draft)), () => "Your filters are saved.")} />}
    {!mayEdit && status && <p className="form-status" role="status">{status}</p>}
  </div>;
}
