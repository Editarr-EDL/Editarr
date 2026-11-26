import { readUserValue } from "./shared/user-storage";
import type { FilterTab } from "./settings/filters/filters-page";
import type { SettingsSection } from "./settings/settings-sections";

/** An item workflow tab a route opens instead of the default Review tab. */
export type ItemRouteStep = "community";

export type Route =
  | { kind: "settings"; section: SettingsSection; deleteAccountId?: number; filterTab?: FilterTab; eventId?: string; openBackups?: boolean }
  | { kind: "title"; titleId: string; versionId?: string; itemId?: string; step?: ItemRouteStep }
  | { kind: "library"; libraryId: string; itemId?: string; seriesId?: string; seasonId?: string; sourceId?: string; step?: ItemRouteStep };

export const AUDIT_SETTINGS_ROUTE = "settings/audit";
export const BACKUP_SETTINGS_ROUTE = "settings/general/backups";

export function libraryLastRoute(libraryId: string): string {
  const root = `library/${encodeURIComponent(libraryId)}`;
  try {
    const saved = readUserValue(`library-last-route.${libraryId}`);
    if (!saved) return root;
    const route = parseRoute(`#${saved}`);
    return route.kind === "title" || route.kind === "library" && route.libraryId === libraryId ? saved : root;
  } catch { return root; }
}

/** Parse an application hash into the route rendered by App.
 * @param hash Hash to parse; defaults to the browser's current hash.
 */
export function parseRoute(hash = window.location.hash): Route {
  const path = hash.replace(/^#\/?/, "");
  const audit = /^(?:settings\/)?audit(?:\/(.*))?$/.exec(path);
  if (audit) return { kind: "settings", section: "audit", eventId: audit[1] ? decodeURIComponent(audit[1]) : undefined };
  if (path === "settings" || path === "settings/connections") return { kind: "settings", section: "connections" };
  if (path === "settings/filters") return { kind: "settings", section: "filters" };
  if (path === "settings/filters/server") return { kind: "settings", section: "filters", filterTab: "server" };
  if (path === "settings/models") return { kind: "settings", section: "models" };
  if (path === "settings/general") return { kind: "settings", section: "general" };
  if (path === BACKUP_SETTINGS_ROUTE) return { kind: "settings", section: "general", openBackups: true };
  if (path === "settings/users") return { kind: "settings", section: "users" };
  const deletion = /^settings\/users\/(\d+)\/delete$/.exec(path);
  if (deletion) return { kind: "settings", section: "users", deleteAccountId: Number(deletion[1]) };
  if (path === "settings/profile") return { kind: "settings", section: "profile" };
  const title = /^title\/([^/]+)(?:\/(version|item)\/([^/]+))?(\/community)?$/.exec(path);
  if (title) return {
    kind: "title",
    titleId: decodeURIComponent(title[1]),
    versionId: title[2] === "version" ? decodeURIComponent(title[3]) : undefined,
    itemId: title[2] === "item" ? decodeURIComponent(title[3]) : undefined,
    ...(title[4] ? { step: "community" as const } : {}),
  };
  if (path.startsWith("library/")) {
    const [, rawLibraryId, section, rawId, childSection, rawChildId, itemSection, rawItemId] = path.split("/");
    if (rawLibraryId) {
      const libraryId = decodeURIComponent(rawLibraryId);
      if (section === "item" && rawId) return {
        kind: "library", libraryId, itemId: decodeURIComponent(rawId),
        ...(childSection === "source" && rawChildId ? { sourceId: decodeURIComponent(rawChildId) } : {}),
        ...(childSection === "community" || (childSection === "source" && itemSection === "community")
          ? { step: "community" as const } : {}),
      };
      if (section === "series" && rawId) return {
        kind: "library",
        libraryId,
        seriesId: decodeURIComponent(rawId),
        seasonId: childSection === "season" && rawChildId ? decodeURIComponent(rawChildId) : undefined,
        itemId: childSection === "season" && itemSection === "item" && rawItemId
          ? decodeURIComponent(rawItemId)
          : undefined,
      };
      return { kind: "library", libraryId };
    }
  }
  return { kind: "settings", section: "connections" };
}

export function isRootRoute(hash = window.location.hash): boolean {
  return hash.replace(/^#\/?/, "") === "";
}

export function navigate(route: string) {
  window.location.hash = route;
}

/** Route to an Editarr title, optionally one version or live item, and optionally its Community tab. */
export function editarrItemRoute(titleId: string, versionId?: string, itemId?: string, step?: ItemRouteStep) {
  const base = `title/${encodeURIComponent(titleId)}`;
  const suffix = step ? `/${step}` : "";
  if (versionId) return `${base}/version/${encodeURIComponent(versionId)}${suffix}`;
  if (itemId) return `${base}/item/${encodeURIComponent(itemId)}${suffix}`;
  return base + suffix;
}

/** Route to a library item, optionally opening one of its media sources (versions) and a workflow tab. */
export function libraryItemRoute(libraryId: string, itemId: string, sourceId?: string | null, step?: ItemRouteStep) {
  const base = `library/${encodeURIComponent(libraryId)}/item/${encodeURIComponent(itemId)}`;
  return (sourceId ? `${base}/source/${encodeURIComponent(sourceId)}` : base) + (step ? `/${step}` : "");
}
