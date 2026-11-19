/** Settings pages addressable by route. */
export type SettingsSection = "connections" | "filters" | "models" | "general" | "users" | "profile" | "audit";
export function settingsSectionTitle(section: SettingsSection): string {
  if (section === "users") return "Users";
  if (section === "profile") return "My Profile";
  if (section === "filters") return "Filters";
  if (section === "models") return "Models";
  if (section === "general") return "General";
  if (section === "audit") return "Audit History";
  return "Connections";
}
