import { auditCategories, auditOutcomes, type AuditCategory, type AuditFilters, type AuditOutcome } from "../../api/api-audit";

export const firstAuditPage: AuditFilters = { page: 1, page_size: 25 };

export function AuditFilterForm({ onApply }: { onApply: (filters: AuditFilters) => void }) {
  return <form className="audit-filters" aria-label="Audit filters" onSubmit={event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (key: string) => String(data.get(key) || "").trim() || undefined;
    const id = (key: string) => text(key) ? Number(text(key)) : undefined;
    const time = (key: string) => text(key) ? new Date(text(key)!).toISOString() : undefined;
    onApply({ ...firstAuditPage, category: text("category") as AuditCategory | undefined,
      outcome: text("outcome") as AuditOutcome | undefined, action: text("action"), actor_id: id("actor_id"),
      target_user_id: id("target_user_id"), media_key: text("media_key"), since: time("since"), until: time("until") });
  }}>
    <label className="field"><span>Category</span><select name="category" aria-label="Category"><option value="">All categories</option>{auditCategories.map(value => <option key={value}>{value}</option>)}</select></label>
    <label className="field"><span>Outcome</span><select name="outcome" aria-label="Outcome"><option value="">All outcomes</option>{auditOutcomes.map(value => <option key={value}>{value}</option>)}</select></label>
    <label className="field"><span>Action</span><input name="action" maxLength={128} /></label>
    <label className="field"><span>Actor account ID</span><input name="actor_id" type="number" min="1" step="1" /></label>
    <label className="field"><span>Target account ID</span><input name="target_user_id" type="number" min="1" step="1" /></label>
    <label className="field"><span>Media ID</span><input name="media_key" maxLength={128} pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*" /></label>
    <label className="field"><span>From</span><input name="since" type="datetime-local" /></label>
    <label className="field"><span>Through</span><input name="until" type="datetime-local" /></label>
    <div className="button-row"><button className="btn-primary" type="submit">Apply filters</button><button className="btn-secondary" type="reset" onClick={() => onApply({ ...firstAuditPage })}>Clear</button></div>
  </form>;
}
