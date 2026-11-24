import { useCallback, useEffect, useState } from "react";
import { fetchCommunitySync, markSyncNotificationsRead, runCommunitySync } from "../api/api-community-updates";
import type { SyncStatus } from "../api/api-community-master-types";
import { notifyCommunityMastersChanged } from "./community-master-events";
import { masterErrorText } from "./community-master-format";

export const RUNNING_POLL_MS = 15_000;

/**
 * Request the daily community sync once when the app loads. The server decides whether a run is due
 * (at most once per 24 hours) and otherwise just returns the status. While a run is in progress the
 * status is re-read; a failed re-read is surfaced as `error` and retried. When a run finishes (or one ran during
 * the request) open item pages are told to refresh their update banners.
 */
export function useCommunitySync(enabled: boolean) {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) { setStatus(null); setError(null); return; }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      timer = setTimeout(() => {
        fetchCommunitySync(controller.signal).then((next) => follow(next, true)).catch((reason) => {
          if (controller.signal.aborted) return;
          setError(masterErrorText(reason));
          poll();
        });
      }, RUNNING_POLL_MS);
    };
    const follow = (value: SyncStatus, wasRunning: boolean) => {
      if (controller.signal.aborted) return;
      setStatus(value);
      setError(null);
      if (value.state === "running") poll();
      else if (wasRunning || value.ran) notifyCommunityMastersChanged();
    };
    // A failed initial request stays quiet (for example when the service isn't configured); it is retried on next load.
    runCommunitySync(controller.signal).then((value) => follow(value, false)).catch(() => {});
    return () => { controller.abort(); clearTimeout(timer); };
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

  return { status, error, markRead };
}
