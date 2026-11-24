import { useId, useState } from "react";
import { checkMasterUpdates } from "../api/api-community-updates";
import type { MasterLinkSummary, MasterTarget } from "../api/api-community-master-types";
import { useCan } from "../auth/auth-gate";
import { MatchPercent, UnconfirmedLabel } from "./community-master-badges";
import { bannerText, masterErrorText, updateTotal } from "./community-master-format";
import { MasterAlignment, type AlignableFilter } from "./master-alignment";
import { MasterFilterList, MasterPreferenceControls } from "./master-filter-list";
import { MasterUpdateReview } from "./master-update-review";
import { useMasterView } from "./use-master-view";

/**
 * One linked community version on the item page: summary, the "Check for filter updates" button,
 * the update banner and review, the first-import alignment gate and the filter list.
 */
export function MasterLinkCard({ summary, target, onChanged }: {
  summary: MasterLinkSummary; target: MasterTarget; onChanged: () => void;
}) {
  const heading = useId();
  const listId = useId();
  const mayDownload = useCan("community.download");
  const mayVote = useCan("community.feedback.submit");
  const master = useMasterView(summary.linkId);
  const [expanded, setExpanded] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState<string | null>(null);
  const link = master.view?.link ?? summary;
  const pending = link.pendingUpdate;
  const alignable: AlignableFilter[] = (master.view?.filters ?? []).map((filter) => ({
    filterId: filter.filterId, category: filter.category, subcategory: filter.master.subcategory,
    startMs: filter.master.startMs, endMs: filter.master.endMs, action: filter.master.action,
  }));

  const check = async () => {
    setChecking(true); setCheckMessage(null);
    try {
      const result = await checkMasterUpdates(link.linkId);
      setCheckMessage(result.changed ? null : "These community filters are up to date.");
      if (result.changed) await master.reload();
    } catch (reason) { setCheckMessage(masterErrorText(reason)); }
    finally { setChecking(false); }
  };
  const settled = () => { void master.reload(); onChanged(); };

  return <section className="master-link" aria-labelledby={heading}>
    <header className="master-link-header">
      <div className="master-link-title">
        <h3 id={heading}>{link.label}</h3>
        <MatchPercent value={link.matchPercent} />
        {link.unconfirmed && <UnconfirmedLabel />}
        <span className="muted-copy">Revision {link.appliedRevision}</span>
      </div>
      <div className="button-row">
        {mayDownload && <button type="button" className="btn-secondary compact-button" disabled={checking}
          onClick={() => void check()}>{checking ? "Checking…" : "Check for filter updates"}</button>}
        <button type="button" className="btn-secondary compact-button" aria-expanded={expanded} aria-controls={listId}
          onClick={() => setExpanded(!expanded)}>{expanded ? "Hide filters" : "Show filters"}</button>
      </div>
    </header>
    {checkMessage && <p role="status" className="form-status">{checkMessage}</p>}
    {pending && updateTotal(pending) > 0 && !reviewing && <div className="master-update-banner" role="status">
      <span>{bannerText(pending)}</span>
      {mayDownload && <button type="button" className="btn-primary compact-button" onClick={() => setReviewing(true)}>Review</button>}
    </div>}
    {reviewing && <MasterUpdateReview link={link} target={target} filters={alignable}
      onClose={() => setReviewing(false)} onSettled={settled} />}
    {master.error && <p className="form-error" role="alert">{master.error}{" "}
      <button type="button" className="btn-secondary compact-button" onClick={() => void master.reload()}>Retry</button></p>}
    {mayDownload && link.alignmentState === "required" && master.view && <MasterAlignment linkId={link.linkId} target={target}
      filters={alignable} forRevision={link.appliedRevision} prefill={link.alignment} onApproved={settled} />}
    <div id={listId} hidden={!expanded}>
      {expanded && master.view && master.preferences && <>
        <MasterPreferenceControls preferences={master.preferences} onChange={master.changePreferences} />
        {master.saveError && <p className="form-error" role="alert">{master.saveError}</p>}
        <MasterFilterList view={master.view} preferences={master.preferences} canVote={mayVote} canDecide={mayDownload}
          onFilter={master.replaceFilter} />
      </>}
      {expanded && !master.view && !master.error && <p role="status">Loading community filters…</p>}
    </div>
  </section>;
}
