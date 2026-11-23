import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { completeScan, reviewFilter } from "../test/app-test-fixtures";
import { SavedReviewOverview } from "./review-overview";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("shows a name or distinct contributing scan count and discloses their details", () => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(20, 20, 200, 160));
  const scans = [
    completeScan({ id: "first", name: "First pass" }),
    completeScan({ id: "second", scan_id: 8, name: "Second pass", origin: "imported",
      imported_at: "2026-09-15T12:00:00Z", import_source_name: "Community" }),
    completeScan({ id: "third", scan_id: 9, name: null }),
  ];
  const filters = ["first", "first", "second", "third"].map((id, index) => ({
    ...reviewFilter(String(index), "visual"), source_scan_id: id,
  }));
  filters.push({ ...filters[0], id: "later", start_seconds: 40, end_seconds: 45 });

  render(<SavedReviewOverview filters={filters} memberships={[]} scans={scans} busy={false}
    defaultSort="time" onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);
  const multiple = screen.getByRole("button", { name: "Show scan details for 3 scans" });
  expect(multiple.closest("td")).toHaveTextContent("3 scans");
  expect(screen.getByRole("button", { name: "Show scan details for First pass" }).closest("td")).toHaveTextContent("First pass");
  expect(screen.queryByText(/Scan #7/)).not.toBeInTheDocument();
  fireEvent.mouseEnter(multiple);
  const details = screen.getByRole("tooltip");
  expect(within(details).getAllByText(/First pass · Scan #7/)).toHaveLength(1);
  expect(details).toHaveTextContent("Second pass · Scan #8");
  expect(details).toHaveTextContent("Imported from Community");
  expect(details).toHaveTextContent("Scan #9");
  expect(details).toHaveTextContent(new Date(scans[1].imported_at!).toLocaleString());
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});
