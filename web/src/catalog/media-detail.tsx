import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { primaryImageUrl } from "../api/api-service";
import { deleteScan, fetchAssociatedScans, fetchScanHistory, fetchScanQueue } from "../api/api-scans";
import { deleteExport } from "../api/api-reviews";
import { identifyEditarrVersion, type EditarrItem } from "../api/api-stable-media";
import { ScanVerificationDialog } from "../scans/scan-verification";
import type { JellyfinMediaItem } from "../api/api-media-types";
import type { ExportFormat, ExportStatus } from "../api/api-mcf-types";
import type { Scan } from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import { usePageTitle } from "../shared/display";
import { NewScanDialog } from "../scans/new-scan-dialog";
import { useNewScan } from "../scans/use-new-scan";
import { formatMediaDuration } from "./media-display";
import { useVerificationAudioScan } from "../scans/use-verification-audio-scan";
import { IDLE_SCAN_POLL_MS, scanPollDelay, useScanPolling } from "../scans/use-scan-polling";
import { MediaWorkflowHeader } from "./media-workflow-header";
import { navigate, type ItemRouteStep } from "../routes";
import { ScanDeleteDialog } from "../scans/scan-components";
import { MediaScanHistory } from "./media-scan-history";
import { SavedReviewPanel } from "../review/review-workspace";
import { ManageExportsDialog } from "../review/review-controls";
import { importStatusMessage } from "../review/review-types";
import { legacyExports, normalizedExportStatus, withoutLegacyExport } from "../review/export-status";
import { applicationUrl } from "../shared/runtime-urls";
import { communityReviewScans } from "../review/community-review-scans";
import { useCan } from "../auth/auth-gate";
import { useScanRecovery } from "./use-scan-recovery";
import { CommunityTab } from "../scans/community-tab";

export type Breadcrumb = { label: string; route?: string };

export function LibraryBreadcrumbs({
  crumbs,
  onNavigate = navigate,
}: {
  crumbs: Breadcrumb[];
  onNavigate?: (route: string) => void;
}) {
  return <nav className="library-breadcrumbs" aria-label="Breadcrumb"><ol>{crumbs.map((crumb, index) => <Fragment key={`${crumb.label}-${index}`}>{index > 0 && <li className="breadcrumb-separator" aria-hidden="true">›</li>}<li>{crumb.route ? <button className="breadcrumb-link" onClick={() => onNavigate(crumb.route!)}>{crumb.label}</button> : <span aria-current="page">{crumb.label}</span>}</li></Fragment>)}</ol></nav>;
}

/** Manage one catalog item, its scan history, versions, and saved reviews. */
export function MediaDetail({ item, breadcrumbs, onNavigate, seasonNumber, onDirtyChange, stableIdentity, initialSourceId, initialStep }: { item: JellyfinMediaItem; breadcrumbs: Breadcrumb[]; onNavigate: (route: string) => void; seasonNumber?: number | null; onDirtyChange: (dirty: boolean) => void; stableIdentity?: EditarrItem; initialSourceId?: string | null; initialStep?: ItemRouteStep }) {
  usePageTitle(item.name);
  const mayScan = useCan("scans.start");
  const mayVerify = useCan("reviews.import.upload");
  const [scanToVerify, setScanToVerify] = useState<Scan | null>(null);
  const mayReadFindings = useCan("scans.findings.read");
  const mayDeleteLegacy = useCan("exports.default.delete");
  const mayViewOwnJobs = useCan("scan_jobs.view.own");
  const mayViewOtherJobs = useCan("scan_jobs.view.other");
  const mayReadHistory = mayReadFindings || mayViewOwnJobs || mayViewOtherJobs;
  const imageTag = typeof item.image_tags.Primary === "string" ? item.image_tags.Primary : undefined;
  const imageUrl = item.image_url ? applicationUrl(item.image_url) : (imageTag ? primaryImageUrl(item.id, imageTag) : null);
  const initialMediaSourceId = initialSourceId ?? item.versions.find((version) => version.id)?.id ?? null;
  const [versions, setVersions] = useState(() => item.versions.map((version) => ({ ...version, exports: normalizedExportStatus(version) })));
  const [mediaSourceId, setMediaSourceId] = useState(initialMediaSourceId);
  const [selectedReviewMediaSourceId, setSelectedReviewMediaSourceId] = useState(initialMediaSourceId);
  const verificationAudio = useVerificationAudioScan();
  const verificationSession = useRef(0);
  const openVerification = (scan: Scan | null) => {
    verificationSession.current++;
    verificationAudio.reset();
    if (scan?.finding_count === 0) {
      setScanToVerify(null);
      setMessage("This scan has no filters to verify.");
      return;
    }
    setScanToVerify(scan);
  };
  const activeVerificationSession = verificationSession.current;
  const [importedReview, setImportedReview] = useState<SavedReview | null>(null);
  const [reviewDirty, setReviewDirty] = useState(false);
  const handleReviewDirtyChange = useCallback((dirty: boolean) => {
    setReviewDirty(dirty);
    onDirtyChange(dirty);
  }, [onDirtyChange]);
  const [deletingExport, setDeletingExport] = useState<ExportFormat | null>(null);
  const [showManageExports, setShowManageExports] = useState(false);
  const [fallbackExportStatus, setFallbackExportStatus] = useState<ExportStatus>(() => normalizedExportStatus(item));
  const [message, setMessage] = useState<string | null>(null);
  const mayReadLinkedScans = mayReadFindings && !!stableIdentity;
  const scanContext = JSON.stringify([item.id, selectedReviewMediaSourceId]);
  const currentScanContext = useRef(scanContext);
  currentScanContext.current = scanContext;
  const [scanResult, setScanResult] = useState<{ context: string; scans: Scan[]; error: string | null; linked?: Scan[]; linkedError?: string; otherVersionScans?: boolean } | null>(null);
  const scans = scanResult?.context === scanContext ? scanResult.scans : [];
  const linkedScans = scanResult?.context === scanContext ? scanResult.linked || [] : [];
  const linkedError = scanResult?.context === scanContext ? scanResult.linkedError : undefined;
  const scansError = scanResult?.context === scanContext ? scanResult.error : null;
  const [scanToDelete, setScanToDelete] = useState<Scan | null>(null);
  const [deletingScan, setDeletingScan] = useState(false);
  const newScanTriggerRef = useRef<HTMLButtonElement>(null);
  const wasNewScanOpen = useRef(false);
  const selectedReviewVersion = versions.find(
    (version) => version.id === selectedReviewMediaSourceId,
  );
  const activeExportStatus = selectedReviewVersion?.exports || fallbackExportStatus;
  const updateActiveExportStatus = useCallback((status: ExportStatus) => {
    if (selectedReviewMediaSourceId) {
      setVersions((current) => current.map((version) => (
        version.id === selectedReviewMediaSourceId ? { ...version, exports: status } : version
      )));
    } else {
      setFallbackExportStatus(status);
    }
  }, [selectedReviewMediaSourceId]);
  const loadScans = useCallback(async () => {
    if (!mayReadHistory) return IDLE_SCAN_POLL_MS;
    try {
      const linkedRequest = mayReadLinkedScans
        ? fetchAssociatedScans(item.id).then((linked) => ({ linked })).catch((reason: unknown) => ({
          linkedError: reason instanceof Error ? reason.message : "Unable to load linked scans.",
        }))
        : Promise.resolve({});
      const [history, queue, links] = await Promise.all([
        fetchScanHistory(),
        mayViewOwnJobs || mayViewOtherJobs ? fetchScanQueue() : Promise.resolve([]),
        linkedRequest,
      ]);
      const loaded = [...new Map([...history, ...queue].map((scan) => [scan.id, scan])).values()].filter((scan) => (
        (scan.jellyfin_item_id === item.id
          && scan.library_id === item.library_id
          && (!selectedReviewMediaSourceId || scan.media_source_id === selectedReviewMediaSourceId))
        || scan.community_pending_targets?.some((target) =>
          target.target_item_id === item.id && target.media_source_id === selectedReviewMediaSourceId)
      ));
      if (currentScanContext.current !== scanContext) return IDLE_SCAN_POLL_MS;
      // This item's completed scans of its other versions are what Recover Scans can match locally.
      const otherVersionScans = history.some((scan) => scan.status === "complete" && scan.jellyfin_item_id === item.id
        && (scan.media_source_id ?? null) !== selectedReviewMediaSourceId);
      setScanResult({ context: scanContext, scans: loaded, error: null, otherVersionScans, ...links });
      return scanPollDelay(loaded);
    }
    catch (error) {
      if (currentScanContext.current === scanContext) setScanResult({ context: scanContext, scans: [], error: error instanceof Error ? error.message : "Unable to load scan history." });
      return IDLE_SCAN_POLL_MS;
    }
  }, [item.id, item.library_id, mayReadHistory, mayReadLinkedScans, mayViewOwnJobs, mayViewOtherJobs, scanContext, selectedReviewMediaSourceId]);
  const [identified, setIdentified] = useState<{ source: string; id: string } | null>(null);
  const currentVersion = identified?.source === scanContext ? identified.id
    : stableIdentity?.current_versions?.find((version) => version.item_id === item.id
        && version.media_source_id === selectedReviewMediaSourceId)?.version_id;
  const verifyCurrent = !!stableIdentity && !currentVersion && stableIdentity.versions.some((version) =>
    version.jellyfin_item_id === item.id && version.media_source_id === selectedReviewMediaSourceId
      && version.scans.some((scan) => scan.stable_version_id === version.id));
  const completedScans = scans.filter((scan) => scan.status === "complete").map((scan) => scan.id).sort().join(",");
  useEffect(() => {
    const title = stableIdentity?.title_id;
    if (!title || !verifyCurrent) return;
    let active = true;
    void identifyEditarrVersion(title, { source: "jellyfin", item_id: item.id, media_source_id: selectedReviewMediaSourceId })
      .then((result) => { if (active && result.version_id) setIdentified({ source: scanContext, id: result.version_id }); })
      .catch(() => {});
    return () => { active = false; };
  }, [stableIdentity?.title_id, verifyCurrent, item.id, selectedReviewMediaSourceId, scanContext, completedScans]);
  const previousVersions = stableIdentity?.versions.filter((version) =>
    version.id !== currentVersion && (version.jellyfin_item_id !== item.id
      || version.media_source_id !== selectedReviewMediaSourceId || !version.available)) || [];
  const previousIds = new Set(previousVersions.flatMap((version) => version.scans.map((scan) => scan.id)));
  const isPending = (scan: Scan) => !!scan.reuse_pending || !!scan.community_pending_targets?.some((target) =>
    target.target_item_id === item.id && target.media_source_id === selectedReviewMediaSourceId);
  const versionScans = scanResult?.context === scanContext ? scans
    : stableIdentity?.versions.find((version) => version.id === currentVersion)?.scans ?? [];
  const pendingImports = versionScans.filter(isPending);
  const readyScans = versionScans.filter((scan) => !isPending(scan) && !previousIds.has(scan.id));
  const reviewScans = communityReviewScans(readyScans, { source: "jellyfin", item_id: item.id, media_source_id: selectedReviewMediaSourceId });
  const recovery = useScanRecovery({ item, sourceId: selectedReviewMediaSourceId, version: selectedReviewVersion,
    verifyImports: !!stableIdentity, loadScans, onMessage: setMessage,
    localRecoverable: pendingImports.length > 0 || linkedScans.length > 0
      || !!(scanResult?.context === scanContext && scanResult.otherVersionScans)
      || previousVersions.some((previous) => previous.scans.length > 0) });
  useScanPolling(loadScans, loadScans);
  const newScan = useNewScan({ itemId: item.id, mediaSourceId, mayScan, verificationAudio, onMessage: setMessage, onQueued: loadScans });
  const showNewScan = newScan.open;
  useEffect(() => {
    setVersions(item.versions.map((version) => ({ ...version, exports: normalizedExportStatus(version) })));
    const keepAvailableSource = (sourceId: string | null) =>
      sourceId && item.versions.some((version) => version.id === sourceId) ? sourceId : initialMediaSourceId;
    setMediaSourceId(keepAvailableSource);
    setSelectedReviewMediaSourceId(keepAvailableSource);
  }, [item.versions, initialMediaSourceId]);
  useEffect(() => {
    const wasOpen = wasNewScanOpen.current;
    wasNewScanOpen.current = showNewScan;
    if (!showNewScan && wasOpen && !scanToVerify) newScanTriggerRef.current?.focus();
  }, [showNewScan, scanToVerify]);
  const deleteManagedExport = async (format: ExportFormat) => {
    setDeletingExport(format); setMessage(null);
    try {
      await deleteExport(item.id, selectedReviewMediaSourceId, format);
      updateActiveExportStatus(withoutLegacyExport(activeExportStatus, format));
      setShowManageExports(false);
      setMessage(`${format.toUpperCase()} deleted.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : `Unable to delete ${format.toUpperCase()}.`); }
    finally { setDeletingExport(null); }
  };
  const deleteSelectedScan = async () => {
    if (!scanToDelete) return;
    setDeletingScan(true); setMessage(null);
    try { await deleteScan(scanToDelete.id); setMessage(`Scan #${scanToDelete.scan_id} deleted.`); setScanToDelete(null); await loadScans(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Unable to delete scan."); }
    finally { setDeletingScan(false); }
  };
  const openNewScan = (audio = false) => {
    if (!mayScan) return;
    if (audio) setMediaSourceId(selectedReviewMediaSourceId);
    newScan.openForm(audio);
  };
  const leaveDetail = (route: string) => onNavigate(route);
  const scanDuration = versions.find((version) => version.id === mediaSourceId)?.duration_seconds ?? item.duration_seconds;
  const newScanMeta = [item.item_type, item.year, scanDuration != null && formatMediaDuration(scanDuration)].filter(Boolean).join(" · ");
  const selectReviewVersion = (sourceId: string | null) => {
    if (reviewDirty && !window.confirm("Discard unsaved review changes and switch versions?")) return;
    setImportedReview(null);
    openVerification(null);
    setMediaSourceId(sourceId);
    setSelectedReviewMediaSourceId(sourceId);
  };
  return <div className="content-page media-detail">
    <LibraryBreadcrumbs crumbs={breadcrumbs} onNavigate={leaveDetail} />
    <MediaWorkflowHeader item={item} versions={versions} sourceId={selectedReviewMediaSourceId} imageUrl={imageUrl}
      onSourceChange={selectReviewVersion} onManageLegacyExports={mayDeleteLegacy && Object.values(legacyExports(activeExportStatus)).some((status) => status.present)
        ? () => setShowManageExports(true) : undefined} />
    {message && <p className="form-status" role="status">{message}</p>}
    {recovery.dialog}
    <SavedReviewPanel initialStep={initialStep} item={item} seasonNumber={seasonNumber} mediaSourceId={selectedReviewMediaSourceId} stableVersionId={currentVersion} scans={reviewScans}
      importedReview={importedReview} exportStatus={activeExportStatus} onExportStatusChange={updateActiveExportStatus}
      onDirtyChange={handleReviewDirtyChange} onSaved={() => void loadScans()}
      onImported={(result) => {
        setImportedReview(result.review);
        setMessage(importStatusMessage(result));
        if (!result.review && stableIdentity) openVerification(result.scan);
        void loadScans();
      }}
      guided={{
        scanStart: mayScan && <button type="button" className="btn-primary" ref={newScanTriggerRef}
          onClick={() => openNewScan()}>New Scan</button>,
        recoverScans: recovery.button,
        community: <CommunityTab target={{ source: "jellyfin", item_id: item.id, media_source_id: selectedReviewMediaSourceId }} />,
        scanHistory: <MediaScanHistory error={scansError} readyScans={readyScans} previousVersions={previousVersions}
          pendingImports={pendingImports} linkedScans={linkedScans} linkedError={linkedError} currentVersion={currentVersion}
          canVerify={mayVerify} onRetry={() => void loadScans()} onDeleteScan={setScanToDelete} onVerify={openVerification} />,
        onStartAudioScan: () => openNewScan(true),
        stageImportsForVerification: !!stableIdentity,
      }} />
    {mayScan && showNewScan && <NewScanDialog form={newScan} title={item.name} meta={newScanMeta}
      versions={versions} mediaSourceId={mediaSourceId} onVersionChange={setMediaSourceId} />}
    {stableIdentity && scanToVerify && mayVerify && !showNewScan && <ScanVerificationDialog
      key={`${scanContext}/${scanToVerify.id}`} scan={scanToVerify} itemName={item.name}
      target={{ source: "jellyfin", item_id: item.id, media_source_id: selectedReviewMediaSourceId }}
      originalName={scanToVerify.media_source_name || previousVersions.find((version) => version.scans.some((scan) => scan.id === scanToVerify.id))?.label || scanToVerify.import_source_name || ""}
      targetName={[selectedReviewVersion?.name || item.name, selectedReviewVersion?.resolution].filter(Boolean).join(" · ")}
      mayStartAudioScan={mayScan}
      currentScans={scans} submittedAudioScan={verificationAudio.submitted} initialChannels={verificationAudio.channels}
      onStartAudioScan={(channels, selection) => {
        verificationAudio.start(channels, selection);
        openNewScan(true);
      }}
      onClose={() => openVerification(null)}
      onApprovalError={(failure) => {
        if (currentScanContext.current === scanContext) setMessage(failure);
      }}
      onApproved={(approved) => {
        if (currentScanContext.current !== scanContext) return;
        if (approved.stable_version_id) setIdentified({ source: scanContext, id: approved.stable_version_id });
        if (verificationSession.current === activeVerificationSession) setScanToVerify(null);
        setMessage("Verified cues are now available in Review for this version.");
        void loadScans();
      }} />}
    {showManageExports && <ManageExportsDialog status={activeExportStatus} subject={selectedReviewVersion?.name || item.name} deleting={deletingExport} onDelete={(format) => void deleteManagedExport(format)} onClose={() => setShowManageExports(false)} />}
    {scanToDelete && <ScanDeleteDialog scan={scanToDelete} deleting={deletingScan} onClose={() => setScanToDelete(null)} onDelete={() => void deleteSelectedScan()} />}
  </div>;
}
