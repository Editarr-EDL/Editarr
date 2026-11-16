import type { useSavedReviewWorkspace } from "./review-workspace-state";
import { CreditsResultDialog } from "./review-import";
import { AdjustCueTimesDialog } from "./review-controls";
import { McfDocumentEditor, McfMetadataSourceSelector, formatMcfTimestamp } from "./review-mcf";
import { applyReviewRetimeResult } from "./review-draft";
import { reviewPolicyToken } from "../api/api-reviews";

type Workspace = ReturnType<typeof useSavedReviewWorkspace>;

/** Edit cue timing and MCF metadata for the current draft.
 * @param workspace Review state and update actions.
 */
export function ReviewWorkspaceMetadata({ workspace }: { workspace: Workspace }) {
  const {
    draft, selected, busy, completedCreditsScans, metadataCandidates,
    setDraft, setError, stageCreditsAcceptance, refreshAfterConflict, access, conflict,
  } = workspace;
  if (!draft) return null;

  return <div className="mcf-review-metadata">
    <div className="review-timing-header">
      <div>
        <strong>Cue synchronization</strong>
        <span>
          {formatMcfTimestamp(draft.synchronization.start_seconds)}–{formatMcfTimestamp(draft.synchronization.end_seconds)}
          {" · "}{draft.synchronization.end_mode === "before_credits" ? "Before credits" : "File end"}
        </span>
        {draft.synchronization.end_mode === "file_end" && <small>
          Canonical MCF END: {formatMcfTimestamp(draft.mcfDocument.material_end_seconds) || "Not set"}
        </small>}
        {draft.acceptedCredits && <small>
          Accepted credits · {formatMcfTimestamp(draft.acceptedCredits.accepted_boundary_seconds)}
          {" · scan "}{draft.acceptedCredits.scan_id}
          {"method" in draft.acceptedCredits
            ? ` · ${draft.acceptedCredits.method.replace(/_/g, " ")} · ${Math.round(draft.acceptedCredits.confidence * 100)}% · ${new Date(draft.acceptedCredits.accepted_at).toLocaleString()}`
            : " · pending save"}
        </small>}
      </div>
      <div className="button-row">
        {selected && <AdjustCueTimesDialog
          reviewId={selected.id}
          draft={draft}
          disabled={busy || Boolean(conflict) || !access.field("timing")}
          onApply={(result) => setDraft((current) => current ? applyReviewRetimeResult(current, result) : current)}
          onConflict={refreshAfterConflict}
        />}
        {selected && <CreditsResultDialog
          scans={completedCreditsScans}
          context={{ kind: "review", review_id: selected.id, revision: draft.revision, ...reviewPolicyToken(draft) }}
          disabled={busy || Boolean(conflict) || !access.field("credits")}
          onAccepted={stageCreditsAcceptance}
          onConflict={refreshAfterConflict}
        />}
      </div>
    </div>
    <fieldset className="review-fieldset" disabled={busy || Boolean(conflict) || !access.field("metadata")}>
    <McfMetadataSourceSelector
      candidates={metadataCandidates}
      value={draft.mcfMetadataSeed}
      onChange={(value) => {
        const candidate = metadataCandidates.find((item) => value.kind === "catalog"
          ? item.kind === "catalog"
          : item.kind === "scan" && item.scan_id === value.scan_id);
        setDraft({
          ...draft,
          mcfMetadataSeed: value,
          mcfDocument: candidate?.mcf_document || draft.mcfDocument,
        });
        setError(null);
      }}
    />
    <McfDocumentEditor
      document={draft.mcfDocument}
      onChange={(mcfDocument) => setDraft({ ...draft, mcfDocument })}
      scans={(selected?.scans || []).map((reference) => reference.scan)}
    />
    </fieldset>
  </div>;
}
