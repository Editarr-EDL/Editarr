import { useEffect, useState } from "react";

import { ApiError } from "../api/api-transport";

/** Keep a server retry window in memory for the lifetime of its credential form. */
export function useRetryAfter() {
  const [deadline, setDeadline] = useState<number | null>(null);
  const [announced, setAnnounced] = useState(0);
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (deadline === null) return;
    const timer = window.setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= deadline) setDeadline(null);
    }, 250);
    return () => window.clearInterval(timer);
  }, [deadline]);
  const remaining = deadline === null ? 0 : Math.max(0, Math.ceil((deadline - now) / 1000));
  return {
    remaining,
    announced,
    waitFor(reason: unknown) {
      if (!(reason instanceof ApiError) || !reason.retryAfterSeconds) return;
      const current = Date.now();
      setNow(current);
      setDeadline(current + reason.retryAfterSeconds * 1000);
      setAnnounced(reason.retryAfterSeconds);
    },
  };
}

/** "Try again in …" in whole seconds under a minute, else in whole minutes rounded up. */
export function retryAfterMessage(seconds: number): string {
  const [count, unit] = seconds < 60 ? [seconds, "second"] : [Math.ceil(seconds / 60), "minute"];
  return `Try again in ${count} ${unit}${count === 1 ? "" : "s"}.`;
}
/**
 * The wait inside an alert. Screen readers hear the wait once, as it was when the
 * refusal arrived; only the visible text counts down, so the alert is not re-announced each second.
 */
export function RetryAfterNotice({ retry }: { retry: ReturnType<typeof useRetryAfter> }) {
  if (retry.remaining <= 0) return null;
  return <>
    {" "}<span aria-hidden="true">{retryAfterMessage(retry.remaining)}</span>
    <span className="visually-hidden">{retryAfterMessage(retry.announced)}</span>
  </>;
}
