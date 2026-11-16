import { useEffect, useRef } from "react";
import { SCANS_CHANGED_EVENT } from "../shared/scan-events";

export { SCANS_CHANGED_EVENT } from "../shared/scan-events";
export const ACTIVE_SCAN_POLL_MS = 1_500;
export const IDLE_SCAN_POLL_MS = 30_000;

export function scanPollDelay(scans: readonly { status: string }[]): number {
  return scans.some((scan) => ["queued", "scanning", "cancelling", "acquiring_model"].includes(scan.status))
    ? ACTIVE_SCAN_POLL_MS : IDLE_SCAN_POLL_MS;
}

/** Poll visible scan views serially, using the delay returned by each refresh. */
export function useScanPolling(refresh: () => Promise<number>, refreshKey: unknown = null) {
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  useEffect(() => {
    let stopped = false;
    let running = false;
    let requested = false;
    let timeout: number | undefined;
    const clear = () => window.clearTimeout(timeout);
    const poll = async () => {
      clear();
      if (stopped || document.hidden) return;
      if (running) { requested = true; return; }
      running = true;
      let delay = IDLE_SCAN_POLL_MS;
      try { delay = await refreshRef.current(); }
      finally {
        running = false;
        if (!stopped && !document.hidden) {
          timeout = window.setTimeout(() => void poll(), requested ? 0 : delay);
        }
        requested = false;
      }
    };
    const wake = () => { void poll(); };
    const visibility = () => { if (document.hidden) clear(); else wake(); };
    void poll();
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener(SCANS_CHANGED_EVENT, wake);
    return () => {
      stopped = true;
      clear();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener(SCANS_CHANGED_EVENT, wake);
    };
  }, [refreshKey]);
}