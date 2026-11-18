import { readUserValue } from "./shared/user-storage";
import type { FilterTab } from "./settings/filters/filters-page";
import type { SettingsSection } from "./settings/settings-view";

export type Route =
  | { kind: "settings"; section: SettingsSection; deleteAccountId?: number; filterTab?: FilterTab; eventId?: string; openBackups?: boolean }
  | { kind: "library"; libraryId: string; itemId?: string; seriesId?: string; seasonId?: string };

export const AUDIT_SETTINGS_ROUTE = "settings/audit";
export const BACKUP_SETTINGS_ROUTE = "settings/general/backups";

export function libraryLastRoute(libraryId: string): string {
  const root = `library/${encodeURIComponent(libraryId)}`;
  try {
    const saved = readUserValue(`library-last-route.${libraryId}`);
    if (!saved) return root;
    const route = parseRoute(`#${saved}`);
    return route.kind === "library" && route.libraryId === libraryId ? saved : root;
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
  if (path.startsWith("library/")) {
    const [, rawLibraryId, section, rawId, childSection, rawChildId, itemSection, rawItemId] = path.split("/");
    if (rawLibraryId) {
      const libraryId = decodeURIComponent(rawLibraryId);
      if (section === "item" && rawId) return { kind: "library", libraryId, itemId: decodeURIComponent(rawId) };
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
