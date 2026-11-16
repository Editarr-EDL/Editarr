import { useState } from "react";

import type { FilterSelectionRule, McfRegistry } from "../../api/api-mcf-types";
import { filterRuleClauses } from "../filter-policy-display";
import { FilterRuleFields, filterRuleIsValid } from "../filter-rule-fields";

export type RuleBadge = "server" | "edited" | "yours" | "off" | "required";

const badgeText: Record<RuleBadge, string> = {
  server: "Server default", edited: "Server default · edited by you", yours: "Yours", off: "Server default · turned off", required: "Required by an admin",
};

/** One rule card. Each handler the caller passes adds its action to the card. */
export interface RuleCardEntry {
  rule: FilterSelectionRule;
  badge?: RuleBadge;
  /** Adds Edit, which expands the card into the rule's fields. */
  onChange?: (patch: Partial<FilterSelectionRule>) => void;
  /** Adds Turn off, or Turn on when the badge is `off`. */
  onToggle?: () => void;
  /** Adds "Use server's rule", which drops the person's edit or turn-off. */
  onRestore?: () => void;
  onDuplicate?: () => void;
  onDelete?: () => void;
}

/**
 * Selection rules as numbered cards in evaluation order. `onMove` adds move up and down
 * buttons. A rule with no match clause stays open so it can be finished.
 */
export function RuleList({ entries, registry, onMove, disabled = false }: {
  entries: RuleCardEntry[]; registry: McfRegistry;
  onMove?: (from: number, to: number) => void;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState<ReadonlySet<string>>(new Set());
  const setOpen = (id: string, open: boolean) => setEditing(current => {
    const next = new Set(current);
    if (open) next.add(id); else next.delete(id);
    return next;
  });

  return <ol className="rule-list">{entries.map((entry, index) => {
    const { rule, badge } = entry;
    const number = index + 1;
    const valid = filterRuleIsValid(rule);
    const open = Boolean(entry.onChange) && (editing.has(rule.id) || !valid);
    const off = badge === "off";
    return <li key={rule.id} className={off ? "rule-card is-off" : "rule-card"} aria-label={`Rule ${number}`}>
      <div className="rule-card-head">
        <span className="rule-number">{number}</span>
        <span className={`rule-action rule-action-${rule.disposition}`}>{rule.disposition === "deny" ? "Select" : "Keep"}</span>
        {badge && <span className={`rule-badge rule-badge-${badge}`}>{badge === "required" && <span aria-hidden="true">🔒 </span>}{badgeText[badge]}</span>}
      </div>
      <p className="rule-card-summary">{filterRuleClauses(rule, registry).join(" · ") || "No match clauses"}</p>
      {open && entry.onChange && <fieldset className="selection-rule-row" disabled={disabled} aria-label={`Rule ${number} fields`}>
        <FilterRuleFields rule={rule} registry={registry} label={`Rule ${number}`} onChange={patch => { setOpen(rule.id, true); entry.onChange?.(patch); }} />
      </fieldset>}
      {open && !valid && <p className="filter-help">Choose at least one thing for this rule to match.</p>}
      <div className="rule-card-actions">
        {onMove && <>
          <button type="button" aria-label={`Move rule ${number} up`} disabled={disabled || index === 0} onClick={() => onMove(index, index - 1)}>↑</button>
          <button type="button" aria-label={`Move rule ${number} down`} disabled={disabled || index === entries.length - 1} onClick={() => onMove(index, index + 1)}>↓</button>
        </>}
        {entry.onChange && <button type="button" aria-expanded={open} aria-label={`${open ? "Done editing" : "Edit"} rule ${number}`}
          disabled={disabled || (open && !valid)} onClick={() => setOpen(rule.id, !open)}>{open ? "Done" : "Edit"}</button>}
        {entry.onToggle && <button type="button" aria-label={`${off ? "Turn on" : "Turn off"} rule ${number}`} disabled={disabled} onClick={entry.onToggle}>{off ? "Turn on" : "Turn off"}</button>}
        {entry.onRestore && <button type="button" aria-label={`Use server's rule for rule ${number}`} disabled={disabled} onClick={entry.onRestore}>Use server's rule</button>}
        {entry.onDuplicate && <button type="button" aria-label={`Duplicate rule ${number}`} disabled={disabled} onClick={entry.onDuplicate}>Duplicate</button>}
        {entry.onDelete && <button type="button" className="rule-delete" aria-label={`Delete rule ${number}`} disabled={disabled} onClick={entry.onDelete}>Delete</button>}
      </div>
    </li>;
  })}</ol>;
}
