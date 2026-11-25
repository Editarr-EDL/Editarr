import { fireEvent, screen } from "@testing-library/react";

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
