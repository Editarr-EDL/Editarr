import { useEffect, useRef, useState } from "react";
import type { RemoteFilterCandidate } from "../api/api-community-lookup";
import type { RemoteSelection } from "./community-result-state";
import type { useCommunityDownloads } from "./use-community-downloads";

const errors: Record<string, string> = {
  service_unavailable: "The community service is unavailable.",
  downloads_not_configured: "Download limits are not configured.", not_connected: "Community account is not connected.",
  candidate_changed: "The candidate changed. Refresh the lookup.",
  candidate_unavailable: "The contribution is no longer available.", explicit_review_required: "Deliberate review confirmation is required.",
  incompatible_artifact: "A selected channel is incompatible.", target_changed: "The target changed. Refresh the lookup.",
  file_changed: "The selected file changed.", invalid_artifact: "Artifact verification failed.",
  target_association_conflict: "The previously imported target evidence differs.",
};

/** Explicit commands and per-item outcomes, separate from local association/apply. */
export function CommunityDownloadPanel({ downloads, candidates, selections, targetKey, disabled }: {
  downloads: ReturnType<typeof useCommunityDownloads>; candidates: RemoteFilterCandidate[];
  selections: RemoteSelection[]; targetKey: string | null; disabled: boolean;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const controller = useRef(downloads); controller.current = downloads;
  const catalogKey = JSON.stringify([targetKey, candidates.map((candidate) => [candidate.contributionId, candidate.revisionId, candidate.artifactDigest])]);
  const selectionKey = JSON.stringify(selections);
  useEffect(() => { setConfirmed(false); }, [selectionKey]);
  useEffect(() => {
    if (!disabled && targetKey && candidates.length) void controller.current.reconcile(candidates, targetKey);
  }, [catalogKey, disabled]);
  const reviewRequired = selections.some((selection) => selection.reviewRequired.length)
    || downloads.entries.some((entry) => entry.error === "explicit_review_required");
  if (!downloads.allowed) return null;
  return <section aria-label="Community downloads">
    {reviewRequired && <label className="checkbox-label"><input type="checkbox" checked={confirmed} disabled={disabled || downloads.running}
      onChange={(event) => setConfirmed(event.target.checked)} />I reviewed the selected uncertain channels and approve importing their original timing.</label>}
    <div className="button-row">
      <button className="btn-primary" type="button" disabled={disabled || downloads.running || downloads.checking || !selections.length || reviewRequired && !confirmed}
        onClick={() => void downloads.start(candidates, selections, confirmed)}>Download selected</button>
      {downloads.running && <button className="btn-secondary" type="button" onClick={downloads.cancel}>Cancel downloads</button>}
      {downloads.entries.length > 0 && <button className="btn-secondary" type="button" disabled={!targetKey || downloads.running || downloads.checking}
        onClick={() => targetKey && void downloads.reconcile(candidates, targetKey)}>Check download status</button>}
    </div>
    {downloads.statusError && <p role="alert">Download status is unavailable. Committed work has not been discarded.</p>}
    <p role="status" aria-live="polite">{downloads.running ? "Downloading selected contributions..." : downloads.checking ? "Checking committed imports..." : "Downloads idle."}</p>
    {downloads.entries.length > 0 && <ul className="community-download-outcomes">{downloads.entries.map((entry) => <li key={entry.id}>
      <strong>{entry.label}</strong> ({entry.choice.channels.join(", ")})
      <span>{entry.status === "unknown" ? "Outcome unknown; check download status." : entry.status === "imported" ? "Downloaded."
        : entry.status === "reused" ? "Already imported; reused." : entry.status === "verifying" ? "Fetching and verifying..." : entry.status === "queued" ? "Queued."
          : entry.status === "cancelled" ? "Not imported by this attempt." : errors[entry.error || ""] || "Download failed."}</span>
      {entry.result && <span>{entry.result.reviewed === true ? "Used in a saved review." : entry.result.reviewed === false ? "Not yet in a saved review." : "Review state unknown."}
        {" "}Receipt {entry.result.receiptState}.{entry.result.reviewRequired && " Review required."}</span>}
      {entry.targetKey !== targetKey && <span>Previous target snapshot.</span>}
      {(entry.status === "failed" || entry.status === "cancelled") && <button className="btn-secondary" type="button"
        disabled={disabled || downloads.running || entry.targetKey !== targetKey || entry.error === "explicit_review_required" && !confirmed}
        onClick={() => void downloads.retry(entry, confirmed)}>Retry {entry.label}</button>}
    </li>)}</ul>}
  </section>;
}