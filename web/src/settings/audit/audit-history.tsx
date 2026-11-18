import { useState } from "react";
import type { AuditEntry, AuditFilters } from "../../api/api-audit";
import { usePrincipal } from "../../auth/auth-gate";
import { can } from "../../auth/session";
import { AUDIT_SETTINGS_ROUTE, navigate } from "../../routes";
import { usePageTitle } from "../../shared/display";
import { AuditFilterForm, firstAuditPage } from "./audit-filters";
import { useAuditHistory } from "./use-audit-history";

const account = (id: number | null) => id === null ? "None" : `Account #${id}`;
const when = (value: string) => new Date(value).toLocaleString();
const actor = (entry: AuditEntry) => entry.actor_id === null ? entry.actor_kind : account(entry.actor_id);

export function AuditHistory({ eventId }: { eventId?: string }) {
  usePageTitle("Settings — Audit History");
  const principal = usePrincipal();
  if (!principal || principal.role === "user" || !can(principal, "audit.read")) return <p role="alert">Audit history is unavailable for this account.</p>;
  return <PermittedAuditHistory eventId={eventId} />;
}

function PermittedAuditHistory({ eventId }: { eventId?: string }) {
  const [filters, setFilters] = useState<AuditFilters>(firstAuditPage);
  const [refresh, setRefresh] = useState(0);
  return <div className="content-page audit-page">
    <header className="page-header"><div><p className="eyebrow">Settings</p><h1>Audit History</h1></div><button className="btn-secondary" onClick={() => setRefresh(value => value + 1)}>Refresh</button></header>
    {eventId ? <a href={`#/${AUDIT_SETTINGS_ROUTE}`}>Back to audit history</a> : <AuditFilterForm onApply={setFilters} />}
    <AuditResults key={`${eventId || "list"}:${JSON.stringify(filters)}:${refresh}`} filters={filters} eventId={eventId} refresh={refresh} onPage={page => setFilters({ ...filters, page })} />
  </div>;
}

function AuditResults({ filters, eventId, refresh, onPage }: { filters: AuditFilters; eventId?: string; refresh: number; onPage: (page: number) => void }) {
  const state = useAuditHistory(filters, eventId, refresh);
  if (state.kind === "loading") return <p role="status" aria-busy="true">Loading audit history...</p>;
  if (state.kind === "denied") return <p role="alert">Audit history is unavailable. Your permissions may have changed.</p>;
  if (state.kind === "missing") return <p role="status">Event no longer available. It may have expired or be outside your current permissions.</p>;
  if (state.kind === "error") return <p role="alert">{state.message}</p>;
  if (state.kind === "detail") return <AuditDetail entry={state.value} />;
  const page = state.value;
  return <section aria-label="Audit events" className="audit-results">
    <div className="audit-summary"><p role="status">{page.total} {page.total === 1 ? "event" : "events"}</p><p>Retained from <time dateTime={page.retained_since}>{when(page.retained_since)}</time> ({page.retention_days} days)</p></div>
    {page.events.length === 0 ? <p>No audit events in this selection.</p> : <div className="audit-table-scroll" role="region" aria-label="Audit event table" tabIndex={0}><table>
      <thead><tr><th scope="col">Time</th><th scope="col">Actor</th><th scope="col">Target</th><th scope="col">Action</th><th scope="col">Outcome</th></tr></thead>
      <tbody>{page.events.map(entry => <tr key={entry.event_id}>
        <td><time dateTime={entry.occurred_at}>{when(entry.occurred_at)}</time></td><td>{actor(entry)}</td><td>{account(entry.target_user_id)}{entry.media_key && <span className="audit-media">Media {entry.media_key}</span>}</td>
        <td><a href={`#/${AUDIT_SETTINGS_ROUTE}/${encodeURIComponent(entry.event_id)}`}>{entry.action}</a></td><td>{entry.outcome}</td>
      </tr>)}</tbody>
    </table></div>}
    <nav className="button-row audit-pagination" aria-label="Audit pages">
      <button className="btn-secondary" aria-label="Previous page" disabled={page.page <= 1} onClick={() => onPage(page.page - 1)}>Previous</button>
      <span>Page {page.page} of {Math.max(1, page.pages)}</span>
      <button className="btn-secondary" aria-label="Next page" disabled={page.page >= page.pages} onClick={() => onPage(page.page + 1)}>Next</button>
    </nav>
  </section>;
}

function AuditDetail({ entry }: { entry: AuditEntry }) {
  return <section aria-labelledby="audit-event-heading">
    <h2 id="audit-event-heading">{entry.action}</h2>
    <dl className="audit-detail">
      <dt>Time</dt><dd><time dateTime={entry.occurred_at}>{when(entry.occurred_at)}</time></dd>
      <dt>Actor</dt><dd>{actor(entry)}</dd><dt>Target</dt><dd>{account(entry.target_user_id)}</dd>
      <dt>Outcome</dt><dd>{entry.outcome}</dd><dt>Category</dt><dd>{entry.category}</dd>
      <dt>Credential type</dt><dd>{entry.credential || "None"}</dd>
      {entry.media_key && <><dt>Media ID</dt><dd>{entry.media_key}</dd></>}
      {entry.resource && <><dt>Resource ID</dt><dd>{entry.resource}</dd></>}
      {Object.entries(entry.details).map(([name, value]) => <div className="audit-detail-field" key={name}><dt>{name.replace(/_/g, " ")}</dt><dd>{value === null ? "None" : String(value)}</dd></div>)}
    </dl>
    <button className="btn-secondary" onClick={() => navigate(AUDIT_SETTINGS_ROUTE)}>Close event</button>
  </section>;
}
