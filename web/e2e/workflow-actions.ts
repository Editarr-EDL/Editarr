import type { Locator, Page } from "@playwright/test";

type WorkflowStep = "Community" | "Scans" | "Review" | "Subtitles" | "Publish";

export function workflowStep(page: Page, name: WorkflowStep) {
  return page.getByRole("tablist", { name: "Library item workflow" }).getByRole("tab", { name, exact: true });
}

export function chooseWorkflowStep(page: Page, name: WorkflowStep) {
  return workflowStep(page, name).click();
}

export async function chooseExportFormat(page: Page, format: "EDL" | "MCF") {
  await chooseWorkflowStep(page, "Publish");
  await page.getByRole("button", { name: format, exact: true }).click();
  return page.getByRole("button", { name: `Export ${format}`, exact: true });
}

/** Open the Review Filters toolbar's Sources popover (unless it's open) and return it. */
export async function openReviewSources(page: Page) {
  const sources = page.getByRole("dialog", { name: "Included scans" });
  if (!await sources.isVisible()) await page.getByRole("button", { name: /^Sources · / }).click();
  return sources;
}

/** Include or exclude a scan or community filter set through the Sources popover, then close it. */
export async function setReviewSource(page: Page, name: string, included = true) {
  const sources = await openReviewSources(page);
  await sources.getByRole("checkbox", { name, exact: true }).setChecked(included);
  await sources.getByRole("button", { name: "Done" }).click();
}

/** Choose an action (e.g. /^Edit filter/) from the "⋯" menu of the first filter row in `scope` offering it. */
export async function chooseFilterAction(page: Page, scope: Locator, name: string | RegExp) {
  const menus = scope.getByRole("button", { name: /^More actions for / });
  for (let index = 0; index < await menus.count(); index += 1) {
    await menus.nth(index).click();
    const item = page.getByRole("menu").getByRole("menuitem", { name });
    if (await item.count()) return item.first().click();
    await page.keyboard.press("Escape");
  }
  throw new Error(`No filter row offers ${String(name)}`);
}
