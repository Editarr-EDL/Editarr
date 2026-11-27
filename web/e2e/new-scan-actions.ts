import { expect, type Locator } from "@playwright/test";

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A New Scan model picker's Change button, named "<label>: <selected model>". */
export function modelPicker(dialog: Locator, label: string) {
  return dialog.getByRole("button", { name: new RegExp(`^${escape(label)}: `) });
}

/** Choose a model by its visible name through the picker's listbox. */
export async function chooseModel(dialog: Locator, label: string, name: string) {
  await modelPicker(dialog, label).click();
  const listbox = dialog.page().getByRole("listbox", { name: label, exact: true });
  await listbox.getByRole("option", { name: new RegExp(`^${escape(name)}(\\s|$)`) }).click();
  await expect(modelPicker(dialog, label)).toHaveAccessibleName(`${label}: ${name}`);
}

/** Choose the New Scan scan type by its radio name ("Audio", "Video", "Audio and video" or "Credits"). */
export async function chooseScanType(dialog: Locator, name: "Audio" | "Video" | "Audio and video" | "Credits") {
  const radio = dialog.getByRole("radio", { name, exact: true });
  // The radio is visually hidden inside its card, so press the card the way a user would.
  await dialog.locator("label").filter({ has: dialog.page().getByRole("radio", { name, exact: true }) }).click();
  await expect(radio).toBeChecked();
}
