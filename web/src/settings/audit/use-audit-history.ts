import { useEffect, useState } from "react";
import { fetchAuditEntry, fetchAuditPage, type AuditEntry, type AuditFilters, type AuditPage } from "../../api/api-audit";
import { ApiError } from "../../api/api-transport";

type AuditRead =
  | { kind: "loading" }
  | { kind: "page"; value: AuditPage }
  | { kind: "detail"; value: AuditEntry }
  | { kind: "missing" }
  | { kind: "denied" }
  | { kind: "error"; message: string };

export function useAuditHistory(filters: AuditFilters, eventId: string | undefined, refresh: number): AuditRead {
  const [state, setState] = useState<AuditRead>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ kind: "loading" });
    const request = eventId ? fetchAuditEntry(eventId, controller.signal) : fetchAuditPage(filters, controller.signal);
    void request.then(value => {
      if (!active) return;
      setState(eventId ? { kind: "detail", value: value as AuditEntry } : { kind: "page", value: value as AuditPage });
    }, error => {
      if (!active) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) setState({ kind: "denied" });
      else if (error instanceof ApiError && error.status === 404) setState({ kind: "missing" });
      else if (error instanceof ApiError && error.status === 503 && error.code === "audit_history_busy") setState({ kind: "error", message: "Audit history is busy. Try again." });
      else setState({ kind: "error", message: "Unable to load audit history. Try again." });
    });
    return () => { active = false; controller.abort(); };
  }, [filters, eventId, refresh]);
  return state;
}
