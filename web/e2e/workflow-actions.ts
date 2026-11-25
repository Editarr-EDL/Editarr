import type { Page } from "@playwright/test";

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
