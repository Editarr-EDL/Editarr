import { fireEvent, screen, waitFor, within } from "@testing-library/react";

type WorkflowLabel = "Community" | "Scans" | "Review" | "Subtitles" | "Publish";

export async function openWorkflowStep(name: WorkflowLabel) {
  fireEvent.click(await screen.findByRole("tab", { name: new RegExp(`^${name}$`) }));
}

export async function exportAction(format: "EDL" | "MCF") {
  await openWorkflowStep("Publish");
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${format}$`) }));
  return screen.findByRole("button", { name: `Export ${format}` });
}

export function revealMediaFiles() {
  const details = screen.getByRole("button", { name: /^(Show|Hide) Details$/ });
  if (details.getAttribute("aria-expanded") === "false") fireEvent.click(details);
}

export async function useCategorySort() {
  fireEvent.change(await screen.findByRole("combobox", { name: "Sort" }), { target: { value: "category" } });
}

/** Open the Filters toolbar's Sources popover (unless it's open) and return it. */
export async function openReviewSources() {
  const open = screen.queryByRole("dialog", { name: "Included scans" });
  if (open) return open;
  fireEvent.click(await screen.findByRole("button", { name: /^Sources · / }));
  return screen.getByRole("dialog", { name: "Included scans" });
}

/**
 * Open the "⋯" menu of the filter row holding a menu item (e.g. "Edit Fight at 0:20") and return the
 * item, leaving its menu open; null when no row in `scope` has it.
 */
export function queryFilterAction(name: string | RegExp, scope: HTMLElement = document.body): HTMLElement | null {
  for (const button of within(scope).queryAllByRole("button", { name: /^More actions for / })) {
    fireEvent.click(button);
    const item = screen.queryByRole("menuitem", { name });
    if (item) return item;
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
  }
  return null;
}

/** {@link queryFilterAction}, waiting until a row has the item. */
export function findFilterAction(name: string | RegExp, scope?: HTMLElement): Promise<HTMLElement> {
  return waitFor(() => {
    const item = queryFilterAction(name, scope);
    if (!item) throw new Error(`No filter row offers ${String(name)}`);
    return item;
  });
}
