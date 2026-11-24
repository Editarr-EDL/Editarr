import type { OperatorResource } from "../../api/api-community-operator";

export const operatorViews = { overview: "Overview", versions: "Versions", aliases: "Alias suggestions", reports: "Reports",
  contributors: "Contributors", operations: "Operations", audit: "Audit history", inspect: "Resource lookup",
  recompute: "Recompute", backup: "Backup status" };
export type OperatorView = keyof typeof operatorViews;
const params = ["after", "selected", "keysAfter", "filtersAfter", "submissionsAfter", "filter", "kind", "contributor"] as const;
export type OperatorRouteParams = Partial<Record<typeof params[number], string | null>>;
export type OperatorRoute = { view: OperatorView } & Record<typeof params[number], string | null>;
/** Filter choices per list view; the first entry is the default. */
export const listFilters: Partial<Record<OperatorView, string[]>> = {
  versions: ["all", "candidate", "confirmed", "merged_into"], aliases: ["pending", "approved", "rejected", "all"] };

/** Parse the hash route; untrusted values that fail their pattern are dropped. */
export function operatorRoute(): OperatorRoute {
  const url = new URL(window.location.hash.slice(1) || "/overview", window.location.origin);
  const candidate = url.pathname.slice(1);
  const view: OperatorView = Object.prototype.hasOwnProperty.call(operatorViews, candidate) ? candidate as OperatorView : "overview";
  const value = (name: string) => {
    const raw = url.searchParams.get(name);
    return raw && /^[A-Za-z0-9_.=:-]{1,1024}$/.test(raw) ? raw : null;
  };
  const route = { view } as OperatorRoute;
  for (const name of params) route[name] = value(name);
  const choices = listFilters[view];
  if (!choices || !route.filter || !choices.includes(route.filter)) route.filter = null;
  return route;
}
export function operatorLink(view: OperatorView, values: OperatorRouteParams = {}) {
  const query = new URLSearchParams();
  for (const name of params) if (values[name]) query.set(name, values[name]!);
  return `#/${view}${query.size ? `?${query}` : ""}`;
}
/** The selected list filter sent to the service, or null for all. */
export function activeFilter(route: OperatorRoute) {
  const value = route.filter ?? listFilters[route.view]?.[0] ?? null;
  return value === "all" ? null : value;
}
/** Where a resource of `kind` is inspected: its own list view when one exists, otherwise the generic inspect view. */
export function resourceLink(kind: OperatorResource, id: string, contributor?: string) {
  if (kind === "version") return operatorLink("versions", { selected: id });
  if (kind === "alias_suggestion") return operatorLink("aliases", { selected: id, filter: "all" });
  if (kind === "contributor") return operatorLink("contributors", { selected: id });
  if (kind === "report") return operatorLink("reports", { selected: id });
  return operatorLink("inspect", { kind, selected: id, contributor });
}
