import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { ScanQueueItem } from "../api/api-scan-types";
import { completeScan } from "../test/app-test-fixtures";
import { ScanDuration } from "./scan-components";

afterEach(cleanup);

const start = Date.parse("2026-11-17T12:00:00Z");

function queueItem(overrides: Partial<ScanQueueItem>): ScanQueueItem {
  return {
    ...completeScan({ scan_started_at: new Date(start).toISOString() }),
    queue_id: 1,
    sort_order: 0,
    progress_percent: 0,
    progress_stage: null,
    progress_message: null,
    ...overrides,
  };
}

it("keeps a steady timer across polls that sample the same attempt at different times", () => {
  const scanning = queueItem({
    status: "scanning",
    scan_duration_seconds: 5.1,
    scan_duration_as_of: new Date(start + 5_100).toISOString(),
  });
  const { rerender } = render(<ScanDuration scan={scanning} now={start + 7_200} />);
  expect(screen.getByText("00:00:07")).toBeInTheDocument();

  const refreshed = {
    ...scanning,
    scan_duration_seconds: 6.8,
    scan_duration_as_of: new Date(start + 6_800).toISOString(),
  };
  rerender(<ScanDuration scan={refreshed} now={start + 7_200} />);
  expect(screen.getByText("00:00:07")).toBeInTheDocument();
  rerender(<ScanDuration scan={refreshed} now={start + 8_200} />);
  expect(screen.getByText("00:00:08")).toBeInTheDocument();
});

it("holds duration while paused or waiting for a retry worker", () => {
  const paused = queueItem({ status: "paused", scan_duration_seconds: 8 });
  const { rerender } = render(<ScanDuration scan={paused} now={start + 3_600_000} />);
  expect(screen.getByText("00:00:08")).toBeInTheDocument();

  rerender(<ScanDuration scan={{ ...paused, status: "queued" }} now={start + 3_630_000} />);
  expect(screen.getByText("00:00:08")).toBeInTheDocument();

  rerender(<ScanDuration scan={{ ...paused, status: "scanning" }} now={start + 3_631_000} />);
  expect(screen.getByText("00:00:08")).toBeInTheDocument();

  const running = {
    ...paused,
    status: "scanning",
    scan_duration_seconds: 10,
    scan_duration_as_of: new Date(start + 3_634_000).toISOString(),
  };
  rerender(<ScanDuration scan={running} now={start + 3_636_000} />);
  expect(screen.getByText("00:00:12")).toBeInTheDocument();
});

it("keeps accumulating during pausing and rolls over the hour", () => {
  const scan = queueItem({ status: "pausing", scan_duration_seconds: 3599,
    scan_duration_as_of: new Date(start + 3_599_000).toISOString() });
  const { rerender } = render(<ScanDuration scan={scan} now={start + 3_599_000} />);
  expect(screen.getByText("00:59:59")).toBeInTheDocument();
  rerender(<ScanDuration scan={scan} now={start + 3_600_000} />);
  expect(screen.getByText("01:00:00")).toBeInTheDocument();
});
