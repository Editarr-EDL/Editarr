const modeNames = {
  mine: { live: "Keep me in sync", copy: "Use my own copy" },
  server: { live: "Keep them in sync", copy: "Give them their own copy" },
};

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Open the Filters page on My filters, or on Server defaults when `tab` is "server". */
export async function openFilters(page, origin, tab = "mine") {
  await page.goto(`${origin}/#/settings/filters${tab === "server" ? "/server" : ""}`);
}

/** The Filters page tab button, "My filters" or "Server defaults". */
export function filtersTab(page, name) {
  return page.getByRole("tab", { name, exact: true });
}

/** The My filters heading every account with personal filter capabilities sees. */
export function myFiltersHeading(page) {
  return page.getByRole("heading", { name: "When the server defaults change", exact: true });
}

/** The Server defaults heading for the new-account mode. */
export function newAccountModeHeading(page) {
  return page.getByRole("heading", { name: "For new accounts", exact: true });
}

/** The Server defaults heading for default severities and rules. */
export function defaultFiltersHeading(page) {
  return page.getByRole("heading", { name: "Default rules", exact: true });
}

/** A mode radio card. `tab` is "mine" or "server" and `mode` is "live" or "copy". */
export function modeRadio(page, tab, mode) {
  return page.getByRole("radio", { name: modeNames[tab][mode], exact: true });
}

/** A category chip in the visible severity heat map, by its registry label. */
export function severityChip(page, label) {
  return page.getByRole("button", { name: new RegExp(`^${escapeRegExp(label.replace(/ \(General\)$/, " (all)"))}: `) });
}

/** Set a category's level through its chip popover, then close the popover. */
export async function chooseSeverity(page, label, level) {
  await severityChip(page, label).click();
  await page.getByRole("radio", { name: level[0].toUpperCase() + level.slice(1), exact: true }).click();
  await page.keyboard.press("Escape");
}

/** The visible save bar's Save button. */
export function saveFiltersButton(page) {
  return page.getByRole("group", { name: "Save changes", exact: true }).getByRole("button", { name: "Save", exact: true });
}
