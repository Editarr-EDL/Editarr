import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchFilterDefaultsSettings,
  updateFilterDefaultsSettings,
} from "../../api/api-service";
import { ApiError } from "../../api/api-transport";
import { loadMcfRegistry } from "../../shared/mcf-registry";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import { useOptionalAuthenticationGate } from "../../auth/auth-gate";
import type {
  FilterDefaultsSettings,
  FilterSelectionRule,
  McfCategory,
  McfRegistry,
  Severity,
} from "../../api/api-mcf-types";
import { newFilterRule as newRule, filterRuleIsValid as ruleIsValid } from "../filter-rule-fields";
import { FilterDefaultsChanges } from "../filter-policy-display";
import { RulesHelp, SeverityHelp } from "./filter-help";
import { serverHeatCells } from "./heat-cells";
import { RuleList, type RuleCardEntry } from "./rule-list";
import { changeCount, SaveBar } from "./save-bar";
import { SeverityHeatMap } from "./severity-heat-map";

function cloneSettings(settings: FilterDefaultsSettings): FilterDefaultsSettings {
  return structuredClone(settings);
}

/** The server's default severities and rules: load, validate, and save them with conflict and re-confirm handling. */
export function DefaultFilters() {
  const [registry, setRegistry] = useState<McfRegistry | null>(null);
  const [saved, setSaved] = useState<FilterDefaultsSettings | null>(null);
  const [draft, setDraft] = useState<FilterDefaultsSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [confirmPolicyChange, setConfirmPolicyChange] = useState(false);
  const [conflict, setConflict] = useState<FilterDefaultsSettings | "unread" | null>(null);
  const gate = useOptionalAuthenticationGate();
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const [loadedRegistry, loadedSettings] = await Promise.all([
        loadMcfRegistry(),
        fetchFilterDefaultsSettings(),
      ]);
      if (generation !== loadGeneration.current) return;
      setRegistry(loadedRegistry);
      setSaved(loadedSettings);
      setDraft(cloneSettings(loadedSettings));
      setStatus(null);
    } catch (reason) {
      if (generation !== loadGeneration.current) return;
      setError(reason instanceof Error ? reason.message : "Unable to load filter settings.");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => { loadGeneration.current += 1; };
  }, [load]);

  if (loading) return <p className="loading-state">Loading filter defaults…</p>;
  if (!registry || !saved || !draft) {
    return <div className="load-error" role="alert"><span>{error || "Unable to load filter settings."}</span><button className="btn-secondary" type="button" onClick={() => void load()}>{saved && draft ? "Reload" : "Retry"}</button></div>;
  }

  const blocked = saving || Boolean(conflict);
  const changes = changeCount(saved.selection_rules, draft.selection_rules, rule => rule.id)
    + changeCount(saved.severity_defaults, draft.severity_defaults, value => value.subcategory);
  const invalidRules = draft.selection_rules.some((rule) => !ruleIsValid(rule));
  const updateRule = (id: string, patch: Partial<FilterSelectionRule>) => {
    setDraft((current) => current ? {
      ...current,
      selection_rules: current.selection_rules.map((rule) => rule.id === id ? { ...rule, ...patch } : rule),
    } : current);
    setStatus(null);
  };
  const moveRule = (index: number, target: number) => {
    const rules = [...draft.selection_rules];
    [rules[index], rules[target]] = [rules[target], rules[index]];
    setDraft({ ...draft, selection_rules: rules });
    setStatus(null);
  };
  const save = async (password?: string) => {
    if (changes === 0 || invalidRules || saving || conflict || gate?.isAuthenticationCurrent() === false) return;
    const generation = loadGeneration.current;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const result = await updateFilterDefaultsSettings(draft, password === undefined ? undefined : { password });
      if (generation !== loadGeneration.current || gate?.isAuthenticationCurrent() === false) return;
      setSaved(result);
      setDraft(cloneSettings(result));
      setStatus("Server defaults saved.");
      setConfirmPolicyChange(false);
    } catch (reason) {
      if (generation !== loadGeneration.current || gate?.isAuthenticationCurrent() === false) return;
      if (reason instanceof ApiError && reason.status === 409) {
        setConfirmPolicyChange(false);
        setConflict("unread");
        setError("Filter settings changed elsewhere. Your unsaved changes are retained.");
      } else if (password !== undefined) throw reason;
      else if (reason instanceof ApiError && reason.code === "reauthentication_required") {
        setConfirmPolicyChange(true);
      } else {
        setError(reason instanceof Error ? reason.message : "Unable to save filter settings.");
      }
    } finally {
      if (generation === loadGeneration.current) setSaving(false);
    }
  };

  const setRules = (rules: FilterSelectionRule[]) => { setDraft({ ...draft, selection_rules: rules }); setStatus(null); };
  const setSeverity = (token: McfCategory, severity: Severity) => {
    const known = draft.severity_defaults.some(item => item.subcategory === token);
    setDraft({ ...draft, severity_defaults: known
      ? draft.severity_defaults.map(item => item.subcategory === token ? { ...item, severity } : item)
      : [...draft.severity_defaults, { subcategory: token, severity }] });
    setStatus(null);
  };
  const entries: RuleCardEntry[] = draft.selection_rules.map((rule, index) => ({
    rule,
    onChange: patch => updateRule(rule.id, patch),
    onDuplicate: () => setRules([...draft.selection_rules.slice(0, index + 1), { ...rule, id: globalThis.crypto.randomUUID() }, ...draft.selection_rules.slice(index + 1)]),
    onDelete: () => setRules(draft.selection_rules.filter(item => item.id !== rule.id)),
  }));

  return <>
    {error && <div className="load-error" role="alert"><span>{error}</span></div>}
    {conflict && <section className="review-policy-conflict" aria-label="Instance filter conflict">
      {conflict === "unread" ? <button type="button" className="btn-secondary" onClick={async () => {
        const generation = loadGeneration.current;
        try { const next = await fetchFilterDefaultsSettings(); if (generation === loadGeneration.current && gate?.isAuthenticationCurrent() !== false) setConflict(next); }
        catch { if (generation === loadGeneration.current) setError("Unable to read latest filter settings. Your draft is retained."); }
      }}>Read latest filter defaults</button> : <>
        <p>Latest instance revision {conflict.revision}. Local revision {draft.revision}.</p>
        <details><summary>Changed instance values</summary><FilterDefaultsChanges previous={saved} latest={conflict} registry={registry} /></details>
        <div className="button-row"><button type="button" className="btn-secondary" onClick={() => { setSaved(conflict); setDraft({ ...draft, revision: conflict.revision }); setConflict(null); setError(null); }}>Keep my draft against latest revision</button><button type="button" className="btn-secondary" onClick={() => { setSaved(conflict); setDraft(cloneSettings(conflict)); setConflict(null); setError(null); }}>Discard local changes and use latest defaults</button></div>
      </>}
    </section>}
    <div className="filters-columns">
      <section className="settings-card" aria-labelledby="default-severity-heading">
        <div><h2 id="default-severity-heading">Default severity</h2><SeverityHelp /></div>
        <SeverityHeatMap cells={serverHeatCells(registry, draft.severity_defaults)} disabled={blocked} hint="Click a category to change its default." onChange={setSeverity} />
      </section>
      <section className="settings-card" aria-labelledby="default-rules-heading">
        <div className="filter-card-heading"><h2 id="default-rules-heading">Default rules</h2>
          <button type="button" className="btn-secondary compact-button" disabled={blocked} onClick={() => setRules([...draft.selection_rules, newRule()])}>Add rule</button></div>
        <RulesHelp />
        {entries.length ? <RuleList entries={entries} registry={registry} disabled={blocked} onMove={moveRule} /> : <p className="filter-help">No rules yet.</p>}
        <p className="filter-help">To require a rule or severity for one person, open their account under Settings › Users.</p>
      </section>
    </div>
    <SaveBar count={changes} saving={saving} blocked={blocked} invalid={invalidRules} status={status}
      onDiscard={() => { setDraft(cloneSettings(saved)); setError(null); setStatus("Filter changes discarded."); }}
      onSave={() => void save()} />
    {confirmPolicyChange && <FreshAuthenticationDialog title="Confirm enforced filter changes" body="This instance edit changes enforced filters for other accounts." actionLabel="Save enforced changes" onConfirm={(password) => save(password)} onClose={() => setConfirmPolicyChange(false)} />}
  </>;
}
