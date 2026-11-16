import type { FilterSelectionRule, McfCategoryFamily, McfRegistry } from "../api/api-mcf-types";

export function newFilterRule(): FilterSelectionRule {
  return { id: globalThis.crypto.randomUUID(), disposition: "allow", evidence_source: null,
    category: null, subcategory: null, label_contains: null, severity: null, mcf_channel: null };
}

export function filterRuleIsValid(rule: FilterSelectionRule): boolean {
  return Boolean(rule.evidence_source || rule.category || rule.subcategory || rule.label_contains?.trim()
    || rule.severity || rule.mcf_channel) && (!rule.subcategory || Boolean(rule.category));
}

export function FilterRuleFields({ rule, registry, label, onChange }: {
  rule: FilterSelectionRule; registry: McfRegistry; label: string;
  onChange: (patch: Partial<FilterSelectionRule>) => void;
}) {
  const family = registry.families.find(item => item.token === rule.category);
  return <>
    <label><span>Action</span><select aria-label={`${label} action`} value={rule.disposition} onChange={event => onChange({ disposition: event.target.value as FilterSelectionRule["disposition"] })}><option value="deny">Select</option><option value="allow">Keep</option></select></label>
    <label><span>Evidence</span><select aria-label={`${label} evidence`} value={rule.evidence_source || ""} onChange={event => onChange({ evidence_source: (event.target.value || null) as FilterSelectionRule["evidence_source"] })}><option value="">Any</option><option value="audio">Audio</option><option value="visual">Visual</option></select></label>
    <label><span>Family</span><select aria-label={`${label} family`} value={rule.category || ""} onChange={event => onChange({ category: (event.target.value || null) as McfCategoryFamily | null, subcategory: null })}><option value="">Any</option>{registry.families.map(item => <option value={item.token} key={item.token}>{item.label}</option>)}</select></label>
    <label><span>Subcategory</span><select aria-label={`${label} subcategory`} value={rule.subcategory || ""} disabled={!family} onChange={event => onChange({ subcategory: event.target.value || null })}><option value="">Any</option>{family?.categories.map(item => <option value={item.token} key={item.token}>{item.label}</option>)}</select></label>
    <label><span>Severity</span><select aria-label={`${label} severity`} value={rule.severity || ""} onChange={event => onChange({ severity: (event.target.value || null) as FilterSelectionRule["severity"] })}><option value="">Any</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
    <label><span>Channel</span><select aria-label={`${label} channel`} value={rule.mcf_channel || ""} onChange={event => onChange({ mcf_channel: (event.target.value || null) as FilterSelectionRule["mcf_channel"] })}><option value="">Any</option><option value="both">Both</option><option value="video">Video</option><option value="audio">Audio</option></select></label>
    <label className="rule-label-clause"><span>Label contains</span><input aria-label={`${label} label contains`} value={rule.label_contains || ""} onChange={event => onChange({ label_contains: event.target.value || null })} /></label>
  </>;
}