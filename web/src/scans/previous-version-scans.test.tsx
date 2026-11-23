import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { completeScan } from "../test/app-test-fixtures";
import { PreviousVersionScans } from "./previous-version-scans";

it("combines visible copies by original finding and channel", () => {
  const source = completeScan({ id: "source", name: "Original scan", scan_audio: true, scan_video: true });
  const approved = (id: string, audio: number[], video: number[]) => completeScan({
    id,
    verified_from_scan_id: source.id,
    verification_coverage: {
      source_counts: { audio: 1, video: 2 },
      covered_finding_ids: { audio, video },
    },
  });
  const common = { versions: [], imports: [source], canVerify: true, onVerify: vi.fn() };
  const first = approved("first", [10], [10]);
  const duplicate = approved("duplicate", [], [10]);
  const second = approved("second", [], [11]);
  const { rerender } = render(<PreviousVersionScans {...common} verifiedCopies={[first, duplicate]} />);

  expect(screen.getByText(/Video needs verification for this version/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Verify for this version" })).toBeVisible();
  rerender(<PreviousVersionScans {...common} verifiedCopies={[first, duplicate, second]} />);
  expect(screen.queryByRole("region", { name: "Previous version scans" })).not.toBeInTheDocument();
});

it("shows a clean historical scan as history and offers verification for linked scans with findings", () => {
  const clean = completeScan({ id: "clean", name: "Clean previous scan", finding_count: 0 });
  const linked = completeScan({ id: "linked", name: "Linked scan with cues", finding_count: 2 });
  const unknown = completeScan({ id: "unknown", name: "Scan without reported cue count" });
  Reflect.deleteProperty(unknown, "finding_count");
  render(<PreviousVersionScans
    versions={[{ id: "original", jellyfin_item_id: "old", media_source_id: null,
      label: "Original cut", available: false, scans: [clean], reviews: [] }]}
    linkedScans={[linked, unknown]} verifiedCopies={[]} canVerify onVerify={vi.fn()}
  />);
  const cleanRow = screen.getByText(/Clean previous scan/).closest("li")!;
  expect(within(cleanRow).getByText(/No filters to verify/)).toBeVisible();
  expect(within(cleanRow).queryByRole("button", { name: "Verify for this version" })).not.toBeInTheDocument();
  const linkedRow = screen.getByText(/Linked scan with cues/).closest("li")!;
  expect(within(linkedRow).getByRole("button", { name: "Verify for this version" })).toBeVisible();
  const unknownRow = screen.getByText(/Scan without reported cue count/).closest("li")!;
  expect(within(unknownRow).getByRole("button", { name: "Verify for this version" })).toBeVisible();
});
