import type { McfCategory, McfRegistry, McfSeverityDefault, Severity } from "../../api/api-mcf-types";
import type { FilterPreferences } from "../../api/api-user-access";

/**
 * One category chip in the severity heat map.
 *
 * `server` is null when the personal view cannot say what the server default is. That view
 * only carries the server value on `instance` entries, so an `override` or `copy` entry knows
 * its server value only when the caller also passes the instance severity defaults.
 */
export interface HeatCell {
  token: McfCategory;
  label: string;
  family: string;
  value: Severity;
  server: Severity | null;
  changed: boolean;
  locked: boolean;
}

type PersonalSeverity = FilterPreferences["severities"][number];
type CellState = Omit<HeatCell, "token" | "label" | "family">;

const unsetSeverity: Severity = "medium";

function registryCells(registry: McfRegistry, state: (token: McfCategory) => CellState): HeatCell[] {
  return registry.families.flatMap(family => family.categories.map(category => ({
    token: category.token, label: category.label, family: family.label, ...state(category.token),
  })));
}

/** Cells for the server defaults editor. A category with no stored default shows as Medium. */
export function serverHeatCells(registry: McfRegistry, defaults: McfSeverityDefault[]): HeatCell[] {
  const values = new Map(defaults.map(item => [item.subcategory, item.severity]));
  return registryCells(registry, token => {
    const value = values.get(token) ?? unsetSeverity;
    return { value, server: value, changed: false, locked: false };
  });
}

/**
 * Cells for a person's effective filters. An override always counts as changed. A copied value
 * counts as changed only when `serverDefaults` is given and differs from it.
 */
export function personalHeatCells(registry: McfRegistry, severities: PersonalSeverity[], serverDefaults?: McfSeverityDefault[]): HeatCell[] {
  const entries = new Map(severities.map(entry => [entry.subcategory, entry]));
  const servers = new Map(serverDefaults?.map(item => [item.subcategory, item.severity]));
  return registryCells(registry, token => {
    const entry = entries.get(token);
    if (!entry) return { value: unsetSeverity, server: unsetSeverity, changed: false, locked: false };
    const server = entry.origin === "instance" ? entry.severity : servers.get(token) ?? null;
    const changed = entry.origin === "override" || (entry.origin === "copy" && server !== null && server !== entry.severity);
    return { value: entry.severity, server, changed, locked: entry.enforced };
  });
}
