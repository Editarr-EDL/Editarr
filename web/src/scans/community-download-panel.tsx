import { useEffect, useRef, useState } from "react";
import type { RemoteFilterCandidate } from "../api/api-community-lookup";
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

/** Report verified download outcomes separately from local filter association. */
export function CommunityDownloadPanel({ downloads, candidates, targetKey, disabled }: {
  downloads: ReturnType<typeof useCommunityDownloads>; candidates: RemoteFilterCandidate[];
  targetKey: string | null; disabled: boolean;
}) {
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const controller = useRef(downloads);
  controller.current = downloads;
  const catalogKey = JSON.stringify([targetKey, candidates.map((candidate) => [candidate.contributionId, candidate.revisionId, candidate.artifactDigest])]);
  useEffect(() => {
    if (!disabled && targetKey && candidates.length) void controller.current.reconcile(candidates, targetKey);
  }, [catalogKey, disabled]);
  if (!downloads.allowed || !downloads.running && !downloads.checking && !downloads.statusError && !downloads.entries.length) return null;
  return <section className="community-download-status" aria-label="Community downloads">
    {downloads.statusError && <p role="alert">Download status is unavailable. Committed work has not been discarded.</p>}
    {(downloads.running || downloads.checking) && <p role="status">
      {downloads.running ? "Downloading filters…" : "Checking committed imports..."}</p>}
    <div className="button-row">
      {downloads.running && <button className="btn-secondary" type="button" onClick={downloads.cancel}>Cancel downloads</button>}
      {(downloads.statusError || downloads.entries.length > 0) && <button className="btn-secondary" type="button"
        disabled={disabled || !targetKey || downloads.running || downloads.checking}
        onClick={() => targetKey && void downloads.reconcile(candidates, targetKey)}>Check download status</button>}
    </div>
    {downloads.entries.length > 0 && <ul className="community-download-outcomes" aria-live="polite">{downloads.entries.map((entry) => {
      const retryable = !disabled && !downloads.running && !downloads.checking && entry.targetKey === targetKey
        && candidates.some((item) => item.contributionId === entry.choice.contributionId && item.revisionId === entry.choice.revisionId
          && item.artifactDigest === entry.choice.artifactDigest);
      const needsReview = entry.error === "explicit_review_required" && !entry.confirmed;
      return <li key={entry.id}>
        <strong>{entry.label}</strong> ({entry.choice.channels.join(", ")})
        <span>{entry.status === "unknown" ? "Outcome unknown; check download status." : entry.status === "imported" ? "Downloaded."
          : entry.status === "reused" ? "Already imported; reused." : entry.status === "verifying" ? "Fetching and verifying…"
            : entry.status === "queued" ? "Queued." : entry.status === "cancelled" ? "Not imported by this attempt."
              : errors[entry.error || ""] || "Download failed."}</span>
        {entry.result && <span>Receipt {entry.result.receiptState}.{entry.result.reviewRequired && " Review required."}
          {entry.result.pendingChannels.length > 0 && ` Needs verification (${entry.result.pendingChannels.join(", ")}).`}
          {entry.result.reviewed && " Used in a saved review."}</span>}
        {entry.targetKey !== targetKey && <span>Previous target snapshot.</span>}
        {(entry.status === "failed" || entry.status === "cancelled") && <>
          {needsReview && <label className="community-download-confirm"><input type="checkbox" checked={confirmed === entry.id}
            disabled={!retryable} onChange={(event) => setConfirmed(event.target.checked ? entry.id : null)} />I approve downloading with the original timing.</label>}
          <button className="btn-secondary" type="button" disabled={!retryable || needsReview && confirmed !== entry.id}
            onClick={() => {
              const approved = confirmed === entry.id;
              setConfirmed(null);
              void downloads.retry(entry, approved);
            }}>Retry {entry.label}</button>
        </>}
      </li>;
    })}</ul>}
  </section>;
}
