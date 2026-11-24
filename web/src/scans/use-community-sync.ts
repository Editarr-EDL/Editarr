import { useCallback, useEffect, useState } from "react";
import { fetchCommunitySync, markSyncNotificationsRead, runCommunitySync } from "../api/api-community-updates";
import type { SyncStatus } from "../api/api-community-master-types";

const RUNNING_POLL_MS = 15_000;

/**
 * Request the daily community sync once when the app loads. The server decides whether a run is due
 * (at most once per 24 hours) and otherwise just returns the status; while a run is in progress the
 * status is re-read until it settles. Failures leave the status empty and are retried on next load.
 */
export function useCommunitySync(enabled: boolean) {
  const [status, setStatus] = useState<SyncStatus | null>(null);

  useEffect(() => {
    if (!enabled) { setStatus(null); return; }
    const controller = new AbortController();
    let timer: number | undefined;
    const follow = (value: SyncStatus) => {
      if (controller.signal.aborted) return;
      setStatus(value);
      if (value.state === "running") timer = window.setTimeout(() => {
        fetchCommunitySync(controller.signal).then(follow).catch(() => {});
      }, RUNNING_POLL_MS);
    };
    runCommunitySync(controller.signal).then(follow).catch(() => {});
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [enabled]);

  const markRead = useCallback(async (ids: string[] | "all") => {
    setStatus((current) => current && {
      ...current,
      notifications: current.notifications.map((item) => ids === "all" || ids.includes(item.notificationId) ? { ...item, read: true } : item),
      unreadCount: ids === "all" ? 0 : Math.max(0, current.unreadCount
        - current.notifications.filter((item) => !item.read && ids.includes(item.notificationId)).length),
    });
    await markSyncNotificationsRead(ids).catch(() => {});
  }, []);

  return { status, markRead };
}
