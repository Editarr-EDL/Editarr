import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MasterApiError } from "../api/api-community-masters";
import type { SyncStatus } from "../api/api-community-master-types";
import { fetchCommunitySync, runCommunitySync } from "../api/api-community-updates";
import { COMMUNITY_MASTERS_CHANGED_EVENT } from "./community-master-events";
import { RUNNING_POLL_MS, useCommunitySync } from "./use-community-sync";

vi.mock("../api/api-community-updates", () => ({ runCommunitySync: vi.fn(), fetchCommunitySync: vi.fn(), markSyncNotificationsRead: vi.fn() }));
const status = (state: SyncStatus["state"], ran = false): SyncStatus => ({ version: 1, ran, state, lastRunAt: null, nextDueAt: null,
  lastError: null, unreadCount: 0, notifications: [] });
const settled = vi.fn();
beforeEach(() => { vi.useFakeTimers(); window.addEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, settled); });
afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.resetAllMocks(); settled.mockReset();
  window.removeEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, settled);
});

it("polls while a run is in progress, surfaces a failed poll, keeps polling and announces the settled run", async () => {
  vi.mocked(runCommunitySync).mockResolvedValue(status("running"));
  vi.mocked(fetchCommunitySync).mockRejectedValueOnce(new MasterApiError("network_unavailable", true, 0))
    .mockResolvedValueOnce(status("idle"));
  const { result } = renderHook(() => useCommunitySync(true));
  await act(async () => {});
  expect(runCommunitySync).toHaveBeenCalledOnce();
  expect(result.current.status?.state).toBe("running");
  await act(async () => { vi.advanceTimersByTime(RUNNING_POLL_MS); });
  expect(result.current.error).toBe("Community service is offline. Try again.");
  expect(settled).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(RUNNING_POLL_MS); });
  expect(fetchCommunitySync).toHaveBeenCalledTimes(2);
  expect(result.current).toMatchObject({ error: null, status: { state: "idle" } });
  expect(settled).toHaveBeenCalledOnce();
  await act(async () => { vi.advanceTimersByTime(RUNNING_POLL_MS * 3); });
  expect(fetchCommunitySync).toHaveBeenCalledTimes(2);
});

it("only announces a change when the request actually ran a sync", async () => {
  vi.mocked(runCommunitySync).mockResolvedValueOnce(status("idle", false));
  const { unmount } = renderHook(() => useCommunitySync(true));
  await act(async () => {});
  expect(settled).not.toHaveBeenCalled();
  unmount();
  vi.mocked(runCommunitySync).mockResolvedValueOnce(status("idle", true));
  renderHook(() => useCommunitySync(true));
  await act(async () => {});
  expect(settled).toHaveBeenCalledOnce();
  expect(fetchCommunitySync).not.toHaveBeenCalled();
});

