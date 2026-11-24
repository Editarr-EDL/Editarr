import { useCallback, useEffect, useId, useState } from "react";
import { acceptAllMasterUpdate, decideMasterUpdate, fetchMasterUpdate } from "../api/api-community-updates";
import type { MasterLink, MasterTarget, MasterUpdate, UpdateDecision, UpdateGroup } from "../api/api-community-master-types";
import { acceptAllRows, masterErrorText, updateGroups } from "./community-master-format";
import { MasterAlignment, type AlignableFilter } from "./master-alignment";
import { MasterUpdateRow } from "./master-update-row";
import { FilterClipPlayer, rangePreview, useFilterClip } from "./use-filter-clip";

export const remainingRows = (update: MasterUpdate) => updateGroups.reduce((total, { id }) => total + update.groups[id].length, 0);

/**
 * Update review grouped New / Changed / Removed / Now Low Confidence. Nothing changes until the user
 * decides. Group Accept all skips rows the user edited; fuzzy links align before Accept is enabled.
 */
export function MasterUpdateReview({ link, target, filters, onClose, onSettled }: {
  link: MasterLink; target: MasterTarget; filters: AlignableFilter[]; onClose: () => void; onSettled: () => void;
}) {
  const heading = useId();
  const [update, setUpdate] = useState<MasterUpdate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clips = useFilterClip(target);

  const load = useCallback(async (signal?: AbortSignal) => {
    setError(null);
    try {
      const next = await fetchMasterUpdate(link.linkId, signal);
      if (!signal?.aborted) setUpdate(next);
    } catch (reason) { if (!signal?.aborted) setError(masterErrorText(reason)); }
  }, [link.linkId]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const act = async (action: (current: MasterUpdate) => Promise<MasterUpdate | void>) => {
    if (!update || busy) return;
    setBusy(true); setError(null);
    try {
      const next = await action(update) ?? await fetchMasterUpdate(link.linkId);
      setUpdate(next);
      if (remainingRows(next) === 0) onSettled();
    } catch (reason) {
      setError(masterErrorText(reason));
      if ((reason as { code?: string }).code === "update_changed") void load();
    } finally { setBusy(false); }
  };
  const decide = (rowId: string, decision: UpdateDecision) =>
    void act(async (current) => { await decideMasterUpdate(link.linkId, current.toRevision, [{ rowId, decision }]); });
  const acceptAll = (group: UpdateGroup) =>
    void act((current) => acceptAllMasterUpdate(link.linkId, current.toRevision, group));

  const needsAlignment = !!update?.alignmentRequired && !update.alignmentApproved;
  return <section className="master-update-review" aria-labelledby={heading}>
    <header className="community-results-header">
      <h3 id={heading}>Review filter updates{update && <span className="muted-copy"> · revision {update.fromRevision} → {update.toRevision}</span>}</h3>
      <button type="button" className="btn-secondary compact-button" onClick={onClose}>Close review</button>
    </header>
    {!update && !error && <p role="status">Loading updates…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {update && needsAlignment && <MasterAlignment linkId={link.linkId} target={target} filters={filters}
      forRevision={update.toRevision} prefill={link.alignment} onApproved={() => void load()} />}
    {update && remainingRows(update) === 0 && <p role="status">All updates are reviewed.</p>}
    {update && updateGroups.map(({ id, label }) => {
      const rows = update.groups[id];
      if (!rows.length) return null;
      const acceptable = acceptAllRows(rows);
      const touched = rows.filter((row) => row.touched).length;
      return <section key={id} className="master-update-group" aria-label={label}>
        <div className="master-update-group-heading">
          <h4>{label} <span className="muted-copy">({rows.length})</span></h4>
          <button type="button" className="btn-secondary compact-button" disabled={busy || !acceptable.length}
            onClick={() => acceptAll(id)} aria-label={`Accept all ${label} (${acceptable.length})`}>Accept all ({acceptable.length})</button>
        </div>
        {touched > 0 && <p className="muted-copy">Accept all skips {touched === 1 ? "the filter" : `the ${touched} filters`} you edited. Decide {touched === 1 ? "it" : "them"} one at a time.</p>}
        <ul className="master-update-rows">{rows.map((row) => <MasterUpdateRow key={row.rowId} row={row} group={id} busy={busy}
          clipLoading={clips.loading === row.rowId} onDecide={(decision) => decide(row.rowId, decision)}
          onPlay={(range) => void clips.play(row.rowId, rangePreview(range))} />)}</ul>
      </section>;
    })}
    <FilterClipPlayer clip={clips.clip} error={clips.error} />
  </section>;
}
