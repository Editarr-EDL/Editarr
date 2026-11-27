import { expect, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

export type ScanTypeName = "Audio" | "Video" | "Audio and video" | "Credits";

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The New Scan dialog's scan type radio. */
export function scanTypeRadio(dialog: HTMLElement, name: ScanTypeName) {
  return within(dialog).getByRole("radio", { name });
}

/** Choose the New Scan dialog's scan type. */
export function chooseScanType(dialog: HTMLElement, name: ScanTypeName) {
  fireEvent.click(scanTypeRadio(dialog, name));
}

/** A model picker's Change button, named "Change <label>: <selected model>". */
export function modelPicker(dialog: HTMLElement, label: string) {
  return within(dialog).getByRole("button", { name: new RegExp(`^Change ${escape(label)}: `) });
}

/** Wait for a picker to show `name` as its selection. */
export async function expectSelectedModel(dialog: HTMLElement, label: string, name: string) {
  await waitFor(() => expect(modelPicker(dialog, label)).toHaveAccessibleName(`Change ${label}: ${name}`));
}

/** Open a model picker once its options load and choose the option whose name starts with `name`. */
export async function chooseModel(dialog: HTMLElement, label: string, name: string) {
  await waitFor(() => expect(modelPicker(dialog, label)).toBeEnabled());
  fireEvent.click(modelPicker(dialog, label));
  const listbox = await screen.findByRole("listbox", { name: label });
  fireEvent.click(within(listbox).getByRole("option", { name: new RegExp(`^${escape(name)}(\\s|$)`) }));
  await expectSelectedModel(dialog, label, name);
}

/** Open an InfoButton's help inside the dialog and return its tooltip text. */
export function infoText(dialog: HTMLElement, label: string) {
  // InfoButton closes tooltips whose anchor is off screen, so give the anchor a real box.
  const width = vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(800);
  const box = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(20, 20, 120, 24));
  const trigger = within(dialog).getByRole("button", { name: label });
  fireEvent.focus(trigger);
  const text = screen.getByRole("tooltip").textContent;
  fireEvent.blur(trigger);
  width.mockRestore(); box.mockRestore();
  return text;
}
