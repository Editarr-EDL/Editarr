import type { FilterDefaultsSettings, FilterSelectionRule, McfRegistry, Severity } from "../api/api-mcf-types";
import { censorAudioWord } from "../review/review-results";

export const severityLabels: Record<Severity, string> = { low: "Low", medium: "Medium", high: "High" };
const evidenceLabels = { audio: "Audio", visual: "Visual" } as const;
const channelLabels = { audio: "Audio channel", video: "Video channel", both: "Both channels" } as const;

export function filterCategoryLabel(registry: McfRegistry, token: string): string {
  return registry.families.flatMap(family => family.categories).find(category => category.token === token)?.label || "Unavailable category";
}

function labelClause(rule: FilterSelectionRule): string | null {
  if (!rule.label_contains) return null;
  return `Label contains "${rule.evidence_source === "visual" ? rule.label_contains : censorAudioWord(rule.label_contains)}"`;
}

export function filterRuleSummary(rule: FilterSelectionRule, registry: McfRegistry): string {
  const family = registry.families.find(value => value.token === rule.category);
  const clauses = [
    rule.evidence_source && evidenceLabels[rule.evidence_source],
    rule.subcategory ? filterCategoryLabel(registry, rule.subcategory) : family?.label,
    rule.severity && `${severityLabels[rule.severity]} severity`,
    rule.mcf_channel && channelLabels[rule.mcf_channel],
    labelClause(rule),
  ].filter(Boolean);
  return `${rule.disposition === "allow" ? "Keep" : "Select"} ${clauses.join(", ") || "No match clauses"}`;
}

/** A rule's match clauses for a rule card, the matched category first. */
export function filterRuleClauses(rule: FilterSelectionRule, registry: McfRegistry): string[] {
  const family = registry.families.find(value => value.token === rule.category)?.label;
  const subcategory = rule.subcategory && filterCategoryLabel(registry, rule.subcategory);
  return [
    family && subcategory ? `${family} › ${subcategory}` : subcategory || family,
    rule.severity && `${severityLabels[rule.severity]} severity`,
    rule.evidence_source && evidenceLabels[rule.evidence_source],
    rule.mcf_channel && channelLabels[rule.mcf_channel],
    labelClause(rule),
  ].filter((clause): clause is string => Boolean(clause));
}

export function FilterDefaultsChanges({ previous, latest, registry }: {
  previous: FilterDefaultsSettings; latest: FilterDefaultsSettings; registry: McfRegistry;
}) {
  const changes: string[] = [];
  for (const value of latest.severity_defaults) {
    const before = previous.severity_defaults.find(item => item.subcategory === value.subcategory);
    if (before?.severity !== value.severity) changes.push(`${filterCategoryLabel(registry, value.subcategory)} severity: ${before ? severityLabels[before.severity] : "Not set"} to ${severityLabels[value.severity]}`);
  }
  for (const value of previous.severity_defaults) {
    if (!latest.severity_defaults.some(item => item.subcategory === value.subcategory)) changes.push(`${filterCategoryLabel(registry, value.subcategory)} severity removed`);
  }
  latest.selection_rules.forEach((rule, index) => {
    const oldIndex = previous.selection_rules.findIndex(item => item.id === rule.id);
    const before = previous.selection_rules[oldIndex];
    if (!before) changes.push(`Rule ${index + 1} added: ${filterRuleSummary(rule, registry)}`);
    else {
      if (Object.entries(rule).some(([key, value]) => before[key as keyof FilterSelectionRule] !== value)) changes.push(`Rule ${index + 1}: ${filterRuleSummary(before, registry)} to ${filterRuleSummary(rule, registry)}`);
      if (oldIndex !== index) changes.push(`Rule moved from ${oldIndex + 1} to ${index + 1}: ${filterRuleSummary(rule, registry)}`);
    }
  });
  previous.selection_rules.forEach((rule, index) => {
    if (!latest.selection_rules.some(item => item.id === rule.id)) changes.push(`Rule ${index + 1} removed: ${filterRuleSummary(rule, registry)}`);
  });
  return changes.length ? <ul aria-label="Changed instance values">{changes.map((change, index) => <li key={index}>{change}</li>)}</ul> : <p>No selection or severity values changed.</p>;
}