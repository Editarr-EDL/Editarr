const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** New Scan type radio names by the API scan mode they submit. */
export const SCAN_TYPES = { audio: "Audio", video: "Video", combined: "Audio and video", credits: "Credits" };

/** A model picker's Change button, named "Change <label>: <selected model>". */
export const modelPicker = (dialog, label) => dialog.getByRole("button", { name: new RegExp(`^Change ${escape(label)}: `) });

/** A picker listbox option by backend model ID; each option's DOM ID ends in `-option-<id>`. */
export const modelOption = (listbox, id) => listbox.locator(`[role="option"][id$="-option-${id.replace(/[^\w-]/g, "_")}"]`);

/** Show one tab ("Scans", "Review", ...) of the item page's workflow; Scans holds Scan history and New Scan. */
export async function openWorkflowStep(page, name, expect) {
  const tab = page.getByRole("tablist", { name: "Library item workflow", exact: true }).getByRole("tab", { name, exact: true });
  if (await tab.getAttribute("aria-selected") !== "true") await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
}

/** Select a scan type by pressing its radio card, as a pointer user does; the radio itself is visually hidden. */
export async function chooseScanType(dialog, mode, expect) {
  const group = dialog.getByRole("group", { name: "Scan type", exact: true });
  const radio = group.getByRole("radio", { name: SCAN_TYPES[mode], exact: true });
  await group.locator("label").filter({ has: dialog.page().getByRole("radio", { name: SCAN_TYPES[mode], exact: true }) }).click();
  await expect(radio).toBeChecked();
}

/** Open a model picker with its Change button and return its listbox. */
export async function openModelPicker(dialog, label, expect) {
  const trigger = modelPicker(dialog, label);
  await expect(trigger).toBeEnabled();
  await trigger.click();
  const listbox = dialog.page().getByRole("listbox", { name: label, exact: true });
  await expect(listbox).toBeFocused();
  return listbox;
}

/** Dismiss an open picker with Escape; focus returns to its Change button. */
export async function closeModelPicker(dialog, label, listbox, expect) {
  await listbox.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(modelPicker(dialog, label)).toBeFocused();
}

/** Choose an available model by backend ID and require the Change button to name it. */
export async function chooseModel(dialog, label, id, expect) {
  const listbox = await openModelPicker(dialog, label, expect);
  const option = modelOption(listbox, id);
  await expect(option).not.toHaveAttribute("aria-disabled", "true");
  const name = (await option.locator(".model-picker-name").first().textContent()).trim();
  await option.click();
  await expect(listbox).toHaveCount(0);
  await expect(modelPicker(dialog, label)).toHaveAccessibleName(`Change ${label}: ${name}`);
}

/** Click an aria-disabled model and require the picker to stay open with its selection unchanged. */
export async function refuseUnavailableModel(dialog, label, id, expect) {
  const before = await modelPicker(dialog, label).getAttribute("aria-label");
  const listbox = await openModelPicker(dialog, label, expect);
  const option = modelOption(listbox, id);
  await expect(option).toHaveAttribute("aria-disabled", "true");
  await option.click({ force: true });
  await expect(listbox).toBeVisible();
  await closeModelPicker(dialog, label, listbox, expect);
  await expect(modelPicker(dialog, label)).toHaveAttribute("aria-label", before);
}

/** Expand the collapsed "Advanced video settings" disclosure that holds the frame and join-gap inputs. */
export async function openAdvancedVideoSettings(dialog, expect) {
  const frames = dialog.getByLabel("Representative frames per flagged segment", { exact: true });
  await expect(frames).toBeHidden();
  await dialog.locator("summary").filter({ hasText: "Advanced video settings" }).click();
  await expect(frames).toBeVisible();
}
