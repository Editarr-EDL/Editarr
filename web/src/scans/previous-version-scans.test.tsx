import { render, screen } from "@testing-library/react";
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
