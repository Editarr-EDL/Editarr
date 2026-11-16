import type { FilterSelectionRule, McfCategory, Severity } from "../../api/api-mcf-types";
import type { FilterPreferences } from "../../api/api-user-access";
import { newFilterRule } from "../filter-rule-fields";
import type { RuleBadge } from "./rule-list";

type RuleEntry = FilterPreferences["rules"][number];

/** Every edit a person can make to their filter draft on My filters. */
export type DraftEdit =
  | { kind: "severity"; token: McfCategory; severity: Severity }
  | { kind: "reset-severity"; token: McfCategory; server: Severity | null }
  | { kind: "add-rule" }
  | { kind: "edit-rule"; id: string; patch: Partial<FilterSelectionRule> }
  | { kind: "toggle-rule"; id: string }
  | { kind: "restore-rule"; id: string; server: FilterSelectionRule | undefined }
  | { kind: "delete-rule"; id: string };

/**
 * Apply one edit. In live mode a changed severity becomes an override and a reset drops it.
 * In copy mode the copy holds every value, so a reset writes the server's value into it.
 */
export function editDraft(draft: FilterPreferences, edit: DraftEdit): FilterPreferences {
  const live = draft.mode === "live";
  const rules = (update: (entry: RuleEntry) => RuleEntry | null, id: string) => ({ ...draft,
    rules: draft.rules.flatMap(entry => entry.rule.id !== id ? [entry] : [update(entry)].filter(value => value !== null)) });
  const severity = (token: McfCategory, value: Severity, origin: "instance" | "override" | "copy") => {
    const next = { subcategory: token, severity: value, origin, enforced: false };
    const known = draft.severities.some(entry => entry.subcategory === token);
    return { ...draft, severities: known ? draft.severities.map(entry => entry.subcategory === token ? next : entry) : [...draft.severities, next] };
  };
  switch (edit.kind) {
    case "severity": return severity(edit.token, edit.severity, live ? "override" : "copy");
    case "reset-severity": {
      const current = draft.severities.find(entry => entry.subcategory === edit.token);
      return severity(edit.token, edit.server ?? current?.severity ?? "medium", live ? "instance" : "copy");
    }
    case "add-rule": return { ...draft, rules: [...draft.rules,
      { rule: newFilterRule(), origin: live ? "personal" : "copy", edited: false, disabled: false, enforced: false }] };
    case "edit-rule": return rules(entry => ({ ...entry, rule: { ...entry.rule, ...edit.patch }, edited: entry.origin === "instance" || entry.edited }), edit.id);
    case "toggle-rule": return rules(entry => ({ ...entry, disabled: !entry.disabled }), edit.id);
    case "restore-rule": return rules(entry => ({ ...entry, rule: edit.server ?? entry.rule, edited: false, disabled: false }), edit.id);
    case "delete-rule": return rules(() => null, edit.id);
  }
}

/** How a rule card labels where the rule comes from. */
export function ruleBadge(entry: RuleEntry): RuleBadge {
  if (entry.enforced) return "required";
  if (entry.origin !== "instance") return "yours";
  if (entry.disabled) return "off";
  return entry.edited ? "edited" : "server";
}
