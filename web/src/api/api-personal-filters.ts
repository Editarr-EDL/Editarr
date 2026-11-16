import type { FilterSelectionRule, McfSeverityDefault } from "./api-mcf-types";
import type { FilterMode, FilterPreferences } from "./api-user-access";
import { usersRequest } from "./api-users";

export type PersonalFilterUpdate = {
  mode: "live"; revision: number;
  overrides: { rule_id: string; disabled: boolean; rule?: FilterSelectionRule }[];
  personal_rules: FilterSelectionRule[]; severity_overrides: McfSeverityDefault[];
} | { mode: "copy"; revision: number; rules: FilterSelectionRule[]; severities: McfSeverityDefault[] };

export const readPersonalFilters = (signal?: AbortSignal) => usersRequest<FilterPreferences>("/settings/filters/personal", "GET", undefined, signal);
export const savePersonalFilters = (body: PersonalFilterUpdate) => usersRequest<FilterPreferences>("/settings/filters/personal", "PUT", body);
export const choosePersonalFilterMode = (revision: number, mode: FilterMode) => usersRequest<FilterPreferences>("/settings/filters/personal/mode", "PUT", { revision, mode });

/** The write for a draft. A live instance rule that is neither edited nor turned off follows the server. */
export function personalFilterUpdate(view: FilterPreferences, draft: FilterPreferences): PersonalFilterUpdate {
  const rules = draft.rules.filter(entry => !entry.enforced);
  const severities = draft.severities.filter(entry => !entry.enforced);
  if (view.mode === "copy") return { mode: "copy", revision: view.revision,
    rules: rules.filter(entry => !entry.disabled).map(entry => entry.rule),
    severities: severities.map(({ subcategory, severity }) => ({ subcategory, severity })) };
  return { mode: "live", revision: view.revision,
    overrides: rules.filter(entry => entry.origin === "instance" && (entry.disabled || entry.edited))
      .map(entry => ({ rule_id: entry.rule.id, disabled: entry.disabled, ...(entry.edited ? { rule: entry.rule } : {}) })),
    personal_rules: rules.filter(entry => entry.origin === "personal").map(entry => entry.rule),
    severity_overrides: severities.filter(entry => entry.origin === "override").map(({ subcategory, severity }) => ({ subcategory, severity })) };
}
