import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { QueueCounts } from "../../../api/api-community-operator-community";
import { accessFailureStatus } from "../../../api/api-community-operator";
import { OperatorAccessError } from "../runtime";

/** App-wide operator services: access-failure handling, queue badge refresh and the topbar freshness clock. */
export interface OperatorServices { subject: string; counts: QueueCounts | null; onAccessFailure: (status: number) => void;
  refreshCounts: () => void; setObservedAt: (value: number | null) => void }
export const OperatorContext = createContext<OperatorServices>({ subject: "", counts: null, onAccessFailure: () => undefined,
  refreshCounts: () => undefined, setObservedAt: () => undefined });
export const useOperator = () => useContext(OperatorContext);

export type DataStatus = "loading" | "fresh" | "stale" | "missing" | "unavailable";
/**
 * Load operator data for `key`, aborting obsolete reads. Access failures (401/403) end the session, 400/404 mark
 * the resource missing and other failures keep the previous snapshot as stale. `refresh` reloads the same key.
 */
export function useOperatorData<T>(key: string, load: (signal: AbortSignal) => Promise<T>, enabled = true) {
  const { onAccessFailure, setObservedAt } = useOperator();
  const [state, setState] = useState<{ key: string; data: T | null; status: DataStatus }>({ key, data: null, status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState(current => ({ key, data: current.key === key ? current.data : null, status: "loading" }));
    void Promise.resolve().then(() => loader.current(controller.signal)).then(data => {
      if (controller.signal.aborted) return;
      setState({ key, data, status: "fresh" });
      const observed = data && typeof data === "object" && "observedAt" in data ? (data as { observedAt: unknown }).observedAt : null;
      if (typeof observed === "number") setObservedAt(observed);
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      const denied = accessFailureStatus(error);
      if (denied) { onAccessFailure(denied); return; }
      const missing = error instanceof OperatorAccessError && [400, 404].includes(error.status);
      setState(current => ({ key, data: missing || current.key !== key ? null : current.data,
        status: missing ? "missing" : current.key === key && current.data ? "stale" : "unavailable" }));
    });
    return () => controller.abort();
  }, [key, attempt, enabled]);
  const refresh = useCallback(() => setAttempt(value => value + 1), []);
  const current = state.key === key ? state : { key, data: null, status: "loading" as const };
  return { data: current.data, status: current.status, refresh };
}

/** Resolve an optional read to null when it fails, but rethrow access failures so the session ends as for any read. */
export function optionalRead<T>(read: Promise<T>): Promise<T | null> {
  return read.catch((error: unknown) => { if (accessFailureStatus(error)) throw error; return null; });
}

const messages: Record<DataStatus, string> = { loading: "Loading service data...", fresh: "Service snapshot loaded",
  stale: "Stale snapshot. Refresh unavailable.", missing: "Selected resource not found", unavailable: "Service data unavailable" };
/** Announces load progress; failures are alerts with a retry. */
export function DataState({ status, onRetry }: { status: DataStatus; onRetry?: () => void }) {
  if (status === "loading" || status === "fresh")
    return <p role="status" className="sr">{messages[status]}</p>;
  return <div className="banner bad"><p role="alert">{messages[status]}</p>
    {onRetry && status !== "missing" && <button className="btn sm" onClick={onRetry}>Retry</button>}</div>;
}
