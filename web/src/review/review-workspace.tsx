import { useRef, useState } from "react";
import type { SavedReviewSummary } from "../api/api-review-types";
import type { SavedReviewPanelProps } from "./review-workspace-state";
import { useSavedReviewWorkspace } from "./review-workspace-state";
import { ReviewWorkspaceMetadata } from "./review-workspace-metadata";
import { ReviewImportAction, ReviewWorkspaceList } from "./review-workspace-list";
import { ReviewWorkspaceEditor } from "./review-workspace-editor";
import { ReviewRepairDialog } from "./review-repair-dialog";
import { ReviewDeletionDialog } from "./review-deletion-dialog";
import { ReviewConflict } from "./review-conflict";
import { PublishedFiles } from "./review-published-files";
import { legacyExports } from "./export-status";
import { PublishedSubtitleFiles, ReviewReleaseProvider, ReviewReleaseStep } from "./review-release-workflow";
import { ReviewWorkflowNavigation, SavedReviewPicker } from "./review-workflow-navigation";
import type { GuidedWorkflowProps, WorkflowStep } from "./review-workflow-types";

type VersionWorkspaceProps = SavedReviewPanelProps & { guided?: GuidedWorkflowProps };

/** Render the saved review workspace for one media version. Each version
 * gets a fresh workspace, so reviews, published files, and late responses
 * from previously selected media never appear under another one.
 * @param props Media version, scans, and parent update callbacks.
 */
export function SavedReviewPanel(props: VersionWorkspaceProps) {
  const [step, setStep] = useState<WorkflowStep>("review");
  return <VersionWorkspace key={JSON.stringify([props.item.id, props.mediaSourceId ?? ""])}
    {...props} step={step} onStepChange={setStep} />;
}

function VersionWorkspace(props: VersionWorkspaceProps & {
  step: WorkflowStep;
  onStepChange: (step: WorkflowStep) => void;
}) {
  const workspace = useSavedReviewWorkspace(props);
  const { error, applyStatus, mcfPreflightIssues, focusPreflightIssue, selected } = workspace;
  const [resolvingReview, setResolvingReview] = useState<SavedReviewSummary | null>(null);
  const [deletingReview, setDeletingReview] = useState<SavedReviewSummary | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const openRepair = (review: SavedReviewSummary, opener: HTMLButtonElement) => {
    returnFocusRef.current = opener;
    setResolvingReview(review);
  };
  const resolved = () => {
    const reviewId = resolvingReview?.id;
    setResolvingReview(null);
    if (selected?.id === reviewId) { workspace.setSelected(null); workspace.setDraft(null); }
    void workspace.refreshReviews().catch((reason) => workspace.setError(reason instanceof Error ? reason.message : "Unable to load saved reviews."));
    props.onSaved?.();
  };

  const { guided, step, onStepChange } = props;
  const published = <PublishedFiles exports={workspace.exports} reviews={workspace.reviews} selectedId={selected?.id ?? null}
    allowed={workspace.access.allowed} additionalFiles={guided && <PublishedSubtitleFiles />}
    legacy={guided && props.exportStatus && legacyExports(props.exportStatus)} />;
  const alerts = <>
    {error && <p className="form-error" role="alert">{error}</p>}
    <ReviewConflict workspace={workspace} />
    {applyStatus && <p className="form-status" role="status">{applyStatus}</p>}
  </>;
  const reviewPanel = <section className="settings-card saved-review-panel" aria-labelledby="saved-reviews-heading">
    <div className="section-heading">
      <div>
        <h2 id="saved-reviews-heading">Saved reviews</h2>
        <p>Named review snapshots for this version.</p>
      </div>
    </div>
    {!guided && alerts}
    {mcfPreflightIssues.length > 0 && <section className="mcf-preflight-issues" aria-labelledby="mcf-preflight-heading" role="alert">
      <h3 id="mcf-preflight-heading">MCF export needs attention</h3>
      <p>Fix these saved review fields, save, then export again.</p>
      <ul>{mcfPreflightIssues.map((issue, index) => <li key={`${issue.code}:${issue.filter_id || issue.field}:${index}`}>
        <button type="button" onClick={() => focusPreflightIssue(issue)}>{issue.message}</button>
      </li>)}</ul>
    </section>}
    {!guided && !selected?.repair && <fieldset className="review-fieldset" disabled={Boolean(workspace.conflict)}><ReviewWorkspaceMetadata workspace={workspace} /></fieldset>}
    <ReviewWorkspaceList workspace={workspace} onResolve={openRepair} showImport={!guided} />
    {!guided && published}
    {selected?.repair ? <>
      <div className="review-repair-alert" role="alert">
        <span>{selected.repair.message}</span>
        <button type="button" className="btn-secondary" onClick={(event) => openRepair(selected, event.currentTarget)}>Resolve</button>
      </div>
      <fieldset className="review-repair-readonly" disabled aria-label="Review awaiting repair">
        <ReviewWorkspaceMetadata workspace={workspace} />
        <ReviewWorkspaceEditor workspace={workspace} guided={Boolean(guided)} />
      </fieldset>
    </> : <fieldset className="review-fieldset" disabled={Boolean(workspace.conflict)}>
      <ReviewWorkspaceEditor workspace={workspace} guided={Boolean(guided)} />
      {guided && <ReviewWorkspaceMetadata workspace={workspace} />}
    </fieldset>}
    {resolvingReview && <ReviewRepairDialog key={resolvingReview.id} review={resolvingReview}
      onClose={() => setResolvingReview(null)} onResolved={resolved} returnFocusRef={returnFocusRef}
      onDelete={() => { setDeletingReview(resolvingReview); setResolvingReview(null); }} />}
    {deletingReview && <ReviewDeletionDialog review={deletingReview} onClose={() => setDeletingReview(null)}
      onDeleted={(result) => { setDeletingReview(null); workspace.forgetDeletedReview(result.review_id, result.retained.length, result.removed.length); props.onSaved?.(); }} />}
  </section>;

  if (!guided) return reviewPanel;
  const startAudioScan = () => {
    onStepChange("scans");
    guided.onStartAudioScan();
  };
  return <ReviewReleaseProvider workspace={workspace} step={step}>
    <section className="review-workflow" aria-label="Item workflow">
      {published}
      <ReviewWorkflowNavigation step={step} onStepChange={onStepChange} />
      {alerts}
      <div id="workflow-scans-panel" role="tabpanel" aria-labelledby="workflow-scans-tab" hidden={step !== "scans"}>
        {step === "scans" && <section className="settings-card">
          <div className="section-heading">
            <h2>Scan history</h2>
            {guided.scanStart}
          </div>
          {guided.scanHistory}
          <div className="button-row workflow-scan-actions">
            {guided.findFilters}
            <ReviewImportAction workspace={workspace} stageForVerification={guided.stageImportsForVerification}
              onComplete={() => onStepChange("review")} />
          </div>
        </section>}
      </div>
      <div id="workflow-review-panel" role="tabpanel" aria-labelledby="workflow-review-tab" hidden={step !== "review"}>
        {reviewPanel}
      </div>
      {(["subtitles", "publish"] as const).map((releaseStep) => <div key={releaseStep} id={`workflow-${releaseStep}-panel`}
        role="tabpanel" aria-labelledby={`workflow-${releaseStep}-tab`} hidden={step !== releaseStep}>
        {step === releaseStep && <section className="settings-card workflow-release-panel">
          <SavedReviewPicker workspace={workspace} />
          <ReviewReleaseStep step={releaseStep} workspace={workspace} onStepChange={onStepChange} onStartAudioScan={startAudioScan} />
        </section>}
      </div>)}
    </section>
  </ReviewReleaseProvider>;
}
