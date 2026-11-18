import { useEffect, useRef } from "react";

type SerialPoll<T> = {
  /** Poll only while true. */
  enabled: boolean;
  /** Restart polling when this value changes, for example a job id. */
  key?: unknown;
  request: () => Promise<T>;
  onResult: (value: T) => void;
  onError?: (reason: unknown) => void;
  intervalMs?: number;
};

/**
 * Poll `request` one call at a time, waiting `intervalMs` after each response before the next.
 * Responses that settle after polling stops or restarts are ignored, so stale jobs never resurface.
 * Callbacks always use the latest render's closures.
 */
export function useSerialPoll<T>({ enabled, key, request, onResult, onError, intervalMs = 1000 }: SerialPoll<T>) {
  const latest = useRef({ request, onResult, onError });
  useEffect(() => { latest.current = { request, onResult, onError }; });
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const value = await latest.current.request();
        if (active) latest.current.onResult(value);
      } catch (reason) {
        if (active) latest.current.onError?.(reason);
      }
      if (active) timer = window.setTimeout(() => void poll(), intervalMs);
    };
    timer = window.setTimeout(() => void poll(), intervalMs);
    return () => { active = false; window.clearTimeout(timer); };
  }, [enabled, key, intervalMs]);
}
