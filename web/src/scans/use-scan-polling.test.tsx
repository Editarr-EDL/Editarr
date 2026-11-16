import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ACTIVE_SCAN_POLL_MS, IDLE_SCAN_POLL_MS, SCANS_CHANGED_EVENT, scanPollDelay, useScanPolling } from "./use-scan-polling";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it("only polls quickly for work that can progress without user input", () => {
  for (const status of ["queued", "scanning", "cancelling", "acquiring_model"]) {
    expect(scanPollDelay([{ status }])).toBe(ACTIVE_SCAN_POLL_MS);
  }
  for (const status of ["complete", "failed", "orphaned", "awaiting_model_consent"]) {
    expect(scanPollDelay([{ status }])).toBe(IDLE_SCAN_POLL_MS);
  }
  expect(scanPollDelay([])).toBe(IDLE_SCAN_POLL_MS);
});

it("backs off while idle and returns to fast polling while active", async () => {
  vi.useFakeTimers();
  const refresh = vi.fn().mockResolvedValue(IDLE_SCAN_POLL_MS);
  renderHook(() => useScanPolling(refresh));
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(29_999); });
  expect(refresh).toHaveBeenCalledTimes(1);
  refresh.mockResolvedValue(ACTIVE_SCAN_POLL_MS);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(refresh).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVE_SCAN_POLL_MS); });
  expect(refresh).toHaveBeenCalledTimes(3);
});

it("pauses hidden tabs and refreshes immediately on return or a scan change", async () => {
  vi.useFakeTimers();
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const refresh = vi.fn().mockResolvedValue(IDLE_SCAN_POLL_MS);
  renderHook(() => useScanPolling(refresh));
  await act(async () => {});
  hidden.mockReturnValue(true);
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(refresh).toHaveBeenCalledTimes(1);
  hidden.mockReturnValue(false);
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(refresh).toHaveBeenCalledTimes(2);
  await act(async () => { window.dispatchEvent(new Event(SCANS_CHANGED_EVENT)); });
  expect(refresh).toHaveBeenCalledTimes(3);
});

it("does not overlap requests and cleans up on unmount", async () => {
  vi.useFakeTimers();
  let finish!: (delay: number) => void;
  const refresh = vi.fn().mockImplementationOnce(() => new Promise<number>((resolve) => { finish = resolve; }))
    .mockResolvedValue(IDLE_SCAN_POLL_MS);
  const { unmount } = renderHook(() => useScanPolling(refresh));
  act(() => window.dispatchEvent(new Event(SCANS_CHANGED_EVENT)));
  expect(refresh).toHaveBeenCalledTimes(1);
  await act(async () => { finish(IDLE_SCAN_POLL_MS); await vi.advanceTimersByTimeAsync(0); });
  expect(refresh).toHaveBeenCalledTimes(2);
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(refresh).toHaveBeenCalledTimes(2);
});