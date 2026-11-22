import { useState } from "react";
import { fetchSavedReview } from "../api/api-reviews";
import { createReviewDraft } from "./review-draft";
import type { useSavedReviewWorkspace } from "./review-workspace-state";
import { useReviewAccountLabel, useReviewLifetime } from "./review-access";
import { CensoredAudioWord, filterSourceOrigin, severityText, visualFilterListLabel } from "./review-results";

export function ReviewConflict({ workspace }: { workspace: ReturnType<typeof useSavedReviewWorkspace> }) {
  const [busy, setBusy] = useState(false);
  const isCurrent = useReviewLifetime();
  const accountLabel = useReviewAccountLabel(workspace.conflict ? workspace.selected : null);
  const { conflict, selected, draft, setSelected, setDraft, setConflict, setError } = workspace;
  if (!conflict || !selected || !draft) return null;
  const scanById = new Map(selected.scans.map(({ scan }) => [scan.id, scan]));
  const reload = async () => {
    if (!isCurrent() || busy) return;
    setBusy(true);
    try { const next = await fetchSavedReview(selected.id); if (isCurrent()) { setSelected(next); setConflict("ready"); } }
    catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "Unable to read latest review."); }
    finally { if (isCurrent()) setBusy(false); }
  };
  return <section className="review-policy-conflict" aria-label="Review conflict" role="alert">
    <h3>Review changed elsewhere</h3>
    <p>Your unsaved draft is retained. Latest saved review: {selected.name}. Revision {selected.revision}, policy owner {accountLabel(selected.policy_owner_id)}, policy revision {selected.policy_revision}.</p>
    {conflict === "ready" && <details><summary>Latest effective filters</summary><ul>{selected.filters.map((filter, index) => <li key={filter.id}>{filter.source === "audio" ? <CensoredAudioWord word={filter.label || filter.details || `Audio filter ${index + 1}`} context={filter.details} /> : visualFilterListLabel(filter, index, filterSourceOrigin(filter, scanById))}: {filter.selected ? "Selected" : "Not selected"}, {severityText([filter])}, {filter.start_seconds} to {filter.end_seconds ?? "open end"}.</li>)}</ul></details>}
    {conflict === "ready" ? <div className="button-row">
      <button type="button" className="btn-primary" disabled={busy || workspace.busy || !workspace.access.save} onClick={() => void workspace.save(true)}>Save retained draft with latest policy</button>
      <button type="button" className="btn-secondary" disabled={busy || workspace.busy} onClick={() => { setDraft(createReviewDraft(selected)); setConflict(null); setError(null); }}>Discard local changes and use latest review</button>
    </div> : <button type="button" className="btn-secondary" disabled={busy || conflict === "loading"} onClick={() => void reload()}>{conflict === "loading" ? "Reading latest effective review..." : "Read latest effective review"}</button>}
  </section>;
}
