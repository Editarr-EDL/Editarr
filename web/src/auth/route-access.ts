import type { Route } from "../routes";
import type { SettingsSection } from "../settings/settings-view";
import type { Capability } from "./capabilities";
import { can, type Principal } from "./session";

const SETTINGS: Record<SettingsSection, Capability | null> = {
  connections: "connections.view",
  filters: "filters.preferences.edit.own",
  models: "models.inventory.view",
  general: null,
  users: "accounts.list",
  profile: "profile.view.own",
};

/** The capability a page needs before any of its content renders; null means every account. */
export function routeCapability(route: Route): Capability | null {
  switch (route.kind) {
    case "audit": return "audit.read";
    case "settings": return SETTINGS[route.section];
    case "library": return "media.browse";
    case "archive": return "archive.view";
  }
}

export function mayOpen(principal: Principal, route: Route): boolean {
  if (route.kind === "audit" && principal.role === "user") return false;
  if (route.kind === "settings" && route.section === "models") {
    return can(principal, "models.inventory.view") || can(principal, "models.download");
  }
  if (route.kind === "settings" && route.section === "filters" && route.filterTab === "server") {
    return can(principal, "filters.instance_defaults.edit") || can(principal, "filters.instance_mode.edit");
  }
  if (route.kind === "settings" && route.section === "filters") {
    return can(principal, "filters.preferences.edit.own") || can(principal, "filters.mode.choose.own")
      || can(principal, "filters.instance_defaults.edit") || can(principal, "filters.instance_mode.edit");
  }
  const capability = routeCapability(route);
  return capability === null || can(principal, capability);
}

export function maySeeSettings(principal: Principal, section: SettingsSection): boolean {
  return mayOpen(principal, { kind: "settings", section });
}

/** Where an account lands when the address names no page it may open. */
export function homeRoute(principal: Principal): string {
  return maySeeSettings(principal, "connections") ? "settings/connections" : "settings/general";
}
