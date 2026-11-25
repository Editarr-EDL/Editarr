import type { RefObject } from "react";
import { ModalDialog } from "../ui/ModalDialog";
import type { FilterCandidate, FilterResult } from "./use-find-filters";
import { audioTrackLabel } from "./audio-track-field";

/** Local scans that match this item, for the Scans tab's Recover Scans action. */
export function FindFiltersDialog({
  candidates,
  selectedIds,
  associating,
  onSelect,
  onAssociate,
  onClose,
  returnFocusRef,
  results,
}: {
  candidates: FilterCandidate[];
  selectedIds: string[];
  associating: boolean;
  onSelect: (id: string, checked: boolean) => void;
  onAssociate: () => void;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
  results?: FilterResult[];
}) {
  return <ModalDialog labelledBy="filter-candidates-heading" className="find-filters-dialog" onClose={associating ? undefined : onClose} returnFocusRef={returnFocusRef}>
    <h2 id="filter-candidates-heading">Recover Scans</h2>
    {candidates.length ? <div className="bounded-scan-list" role="list" aria-label="Recoverable scans">
      {candidates.map((candidate) => <div role="listitem" key={candidate.id}><label
        className={`scan-history-row candidate-scan${candidate.alreadyAssociated ? " associated-scan" : ""}`}
      >
        <input
          type="checkbox"
          aria-label={`Associate ${candidate.label}`}
          checked={candidate.alreadyAssociated || selectedIds.includes(candidate.id)}
          disabled={candidate.alreadyAssociated || associating}
          onChange={(event) => onSelect(candidate.id, event.target.checked)}
        />
        <div>
          <strong>{candidate.label} · {candidate.origin}</strong>
          <p>{candidate.options}<br /><span className="muted-copy">{candidate.matchDescription}</span></p>
        </div>
      </label>
      </div>)}
    </div> : <p className="muted-copy">No recoverable scans matched this file.</p>}
    {results?.map((entry) => <details key={entry.candidate.id}>
      <summary tabIndex={0}>Local provenance: {entry.candidate.label}</summary>
      <p>Created: {entry.match.scan.created_at}; filters: {entry.match.scan.finding_count}</p>
      <p>Editarr: {entry.match.scan.release_version || "Unknown"}; scanner: {entry.match.scan.scanner_behavior_version || "Unknown"}</p>
      <p>Coverage: {entry.match.scan.stage_coverage?.map((stage) => `${stage.stage}: ${stage.status}`).join("; ") || "Unknown"}</p>
      <p>Analyzed audio: {entry.match.scan.analyzed_audio_selection ? audioTrackLabel(entry.match.scan.analyzed_audio_selection.track) : "Unknown analyzed track"}</p>
      {entry.match.scan.stage_coverage?.map((stage) => <div key={stage.execution_id}>
        <p>{stage.stage}: {stage.model_id || "Unknown model"}; revision: {stage.model_revision || "Unknown"}</p>
        <p>Effective settings: {stage.effective_settings ? JSON.stringify(stage.effective_settings) : "Unknown"}</p>
      </div>)}
    </details>)}
    <div className="button-row">
      <button className="btn-secondary" onClick={onClose} disabled={associating}>Cancel</button>
      <button className="btn-primary" onClick={onAssociate} disabled={associating || !selectedIds.length}>
        {associating ? "Associating…" : "Associate Selected"}
      </button>
    </div>
  </ModalDialog>;
}