import { hasExportEvidence } from "./review-community";
import { createContext, useContext, useState, type ReactNode } from "react";
import type { ExportFormat } from "../api/api-mcf-types";
import { usePrincipal } from "../auth/auth-gate";
import { ShareAction } from "../scans/sharing-dialog";
import { canDownload, canExportSavedReview, EXPORT_FORMATS } from "./export-model";
import { ExportDialog } from "./review-export-dialog";
import { isReviewDraftDirty } from "./review-draft";
import type { GuidedReviewWorkspace, WorkflowStep } from "./review-workflow-types";
import { ReviewSubtitlePanel, SubtitlePlaybackNotice, subtitleSourceDetails } from "./review-subtitle-panel";
import { useReviewSubtitles, type ReviewSubtitles } from "./use-review-subtitles";
import { VideoSkipExportDialog } from "./review-videoskip-export-dialog";
import "../styles/subtitle-workflow.css";

type ReleaseStep = "subtitles" | "publish";
type ReleaseExportFormat = ExportFormat | "videoskip";
type ReleaseFormat = "subtitles" | ReleaseExportFormat;
const formatLabels: Record<ReleaseFormat, string> = { subtitles: "Filtered subtitles", edl: "EDL", mcf: "MCF", videoskip: "VideoSkip (.skp)" };
const subtitlesContext = createContext<ReviewSubtitles | null>(null);

/** Keep subtitle preparation and publication state available in both workflow steps. */
export function ReviewReleaseProvider({ workspace, step, children }: {
  workspace: GuidedReviewWorkspace; step: WorkflowStep; children: ReactNode;
}) {
  const me = usePrincipal()?.userId ?? null;
  const review = workspace.selected;
  const mayExport = Boolean(review && canExportSavedReview(review.owner_id, me, workspace.access.allowed));
  const subtitles = useReviewSubtitles(review, mayExport, workspace.refreshAfterConflict, {
    target: workspace.reviewTarget, discover: step === "subtitles" || step === "publish",
  });
  return <subtitlesContext.Provider value={subtitles}>{children}</subtitlesContext.Provider>;
}

export function PublishedSubtitleFiles() {
  const subtitles = useContext(subtitlesContext);
  if (!subtitles?.published.length) return null;
  return <ul className="published-subtitle-files" aria-label="Published subtitle files">
    {subtitles.published.map((track) => <li key={track.filename}>
      <strong>{track.label}</strong>
      <small>{track.format.toUpperCase()}{track.forced ? " · Forced" : ""}{track.owned_by_viewer ? " · Your track" : ""} · Shared</small>
    </li>)}
  </ul>;
}

function ReviewPublishPanel({ workspace, subtitles, onStepChange }: {
  workspace: GuidedReviewWorkspace;
  subtitles: ReviewSubtitles;
  onStepChange: (step: WorkflowStep) => void;
}) {
  const me = usePrincipal()?.userId ?? null;
  const selected = workspace.selected;
  const [destination, setDestination] = useState<"files" | "community">("files");
  const [format, setFormat] = useState<ReleaseFormat>();
  const [exportFormat, setExportFormat] = useState<ReleaseExportFormat | null>(null);
  if (!selected) return null;

  const allowed = workspace.access.allowed;
  const mayExport = canExportSavedReview(selected.owner_id, me, allowed);
  const mayUseCommunity = allowed("community.publish.own_review") || allowed("community.publish.other_review")
    || allowed("community.publications.check.own") || allowed("community.publications.check.other");
  const dirty = Boolean(workspace.draft && isReviewDraftDirty(workspace.draft));
  const busy = workspace.busy || Boolean(workspace.conflict) || dirty;
  const formats: ReleaseFormat[] = mayExport ? ["subtitles", ...EXPORT_FORMATS] : [];
  const mayDownloadVideoSkip = canDownload(selected.owner_id, me, allowed);
  if (mayDownloadVideoSkip) formats.push("videoskip");
  const exportAvailable = hasExportEvidence(selected);
  const chosen = format && formats.includes(format) ? format : formats[0];
  const preferredDestination = !formats.length && mayUseCommunity ? "community" : destination;
  const prepared = subtitles.prepared;
  const replacing = prepared?.source.published_filename;

  return <div className="workflow-release">
    {formats.length > 0 && mayUseCommunity && <div className="button-row" role="group" aria-label="Export and sharing">
      <button type="button" className="btn-secondary" aria-pressed={preferredDestination === "files"} onClick={() => setDestination("files")}>Files</button>
      <button type="button" className="btn-secondary" aria-pressed={preferredDestination === "community"} onClick={() => setDestination("community")}>Community</button>
    </div>}
    {dirty && <p className="muted-copy">Save or discard review changes before exporting.</p>}
    {preferredDestination === "community" && mayUseCommunity
      ? <section className="workflow-release-panel" aria-labelledby="workflow-community-heading">
        <h3 id="workflow-community-heading">Share reviewed filters</h3>
        <p className="muted-copy">Contribute the saved timing, categories, and filter actions to the Editarr community.</p>
        <div><ShareAction source={{ kind: "review", id: selected.id }} name={selected.name} disabled={busy} /></div>
      </section>
      : chosen ? <div className="workflow-release-grid">
        <section className="workflow-release-panel" aria-labelledby="workflow-formats-heading">
          <h3 id="workflow-formats-heading">Export format</h3>
          <div className="workflow-release-options">
            {formats.map((candidate) => <button key={candidate} type="button" className="btn-secondary workflow-release-option"
              aria-pressed={chosen === candidate} onClick={() => setFormat(candidate)}>
              <strong>{formatLabels[candidate]}</strong>
            </button>)}
          </div>
        </section>
        {chosen === "videoskip" ? <section className="workflow-release-panel" aria-labelledby="workflow-videoskip-heading">
          <h3 id="workflow-videoskip-heading">VideoSkip file download</h3>
          {!exportAvailable && <p className="muted-copy">Include at least one scan or community filter before exporting.</p>}
          <div><button type="button" className="btn-primary" disabled={busy || !exportAvailable} onClick={() => setExportFormat(chosen)}>
            Export VideoSkip
          </button></div>
        </section> : <section className="workflow-release-panel" aria-labelledby="workflow-jellyfin-heading">
          <h3 id="workflow-jellyfin-heading">Publish to Jellyfin</h3>
          {chosen === "subtitles" && subtitles.publicationStatus && <p className={subtitles.publicationStatus.error ? "load-error" : "form-status"}
            role={subtitles.publicationStatus.error ? "alert" : "status"}>{subtitles.publicationStatus.message}</p>}
          {chosen === "subtitles" ? subtitles.publicationBlockReason
            ? <p className="load-error" role="alert">{subtitles.publicationBlockReason}</p>
            : prepared ? <>
            <div>
              <p className="workflow-subtitle-title">Jellyfin track title <strong>{prepared.track_title}</strong></p>
              <p className="muted-copy">{subtitleSourceDetails(prepared.source)}</p>
            </div>
            <SubtitlePlaybackNotice forced={prepared.source.forced} />
            {replacing && <p className="muted-copy">Publishing replaces your previous copy for this review and source.</p>}
            <div><button type="button" className="btn-primary" disabled={busy || subtitles.publishing}
              onClick={() => void subtitles.publish()}>{subtitles.publishing ? "Publishing…" : "Publish filtered subtitles"}</button></div>
          </> : <>
            <p className="muted-copy">Choose a subtitle source and preview how the saved review changes it.</p>
            <div><button type="button" className="btn-primary" onClick={() => onStepChange("subtitles")}>Go to Subtitles</button></div>
          </> : <>
            <p className="muted-copy">Choose a destination or download a copy of the saved review.</p>
            <div><button type="button" className="btn-primary" disabled={busy} onClick={() => setExportFormat(chosen)}>
              Export {chosen.toUpperCase()}
            </button></div>
          </>}
        </section>}
      </div> : <p className="muted-copy">Publishing is unavailable for this review.</p>}
    {exportFormat === "videoskip" && mayDownloadVideoSkip && <VideoSkipExportDialog
      key={`${selected.revision}/${selected.policy_owner_id}/${selected.policy_revision}`} review={selected}
      onConflict={workspace.refreshAfterConflict} onClose={() => setExportFormat(null)} />}
    {exportFormat && exportFormat !== "videoskip" && <ExportDialog review={selected} reviews={workspace.reviews} exports={workspace.exports} allowed={allowed}
      formats={[exportFormat]} exportAvailable={exportAvailable}
      onMcfIssues={(issues) => { workspace.setMcfPreflightIssues(issues); onStepChange("review"); }}
      onConflict={workspace.refreshAfterConflict} onPublished={() => void workspace.afterPublish()} onClose={() => setExportFormat(null)} />}
  </div>;
}

/** Render the selected release step below the common version and review picker. */
export function ReviewReleaseStep({ step, workspace, onStepChange, onStartAudioScan }: {
  step: ReleaseStep;
  workspace: GuidedReviewWorkspace;
  onStepChange: (step: WorkflowStep) => void;
  onStartAudioScan: () => void;
}) {
  const me = usePrincipal()?.userId ?? null;
  const subtitles = useContext(subtitlesContext);
  const selected = workspace.selected;
  if (!selected) return <p className="muted-copy">Choose a saved review to continue.</p>;
  if (selected.repair) return <p className="muted-copy">Resolve this review in Review before continuing.</p>;
  if (!subtitles) return null;
  if (step === "publish") return <ReviewPublishPanel key={selected.id} workspace={workspace} subtitles={subtitles} onStepChange={onStepChange} />;
  if (!canExportSavedReview(selected.owner_id, me, workspace.access.allowed)) {
    return <p className="muted-copy">You do not have access to export this review.</p>;
  }
  return <ReviewSubtitlePanel subtitles={subtitles} onStartAudioScan={workspace.access.allowed("scans.start") ? onStartAudioScan : undefined}
    onContinue={() => { subtitles.prepare(); onStepChange("publish"); }} />;
}
