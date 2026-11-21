import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  fetchCapabilities,
  primaryImageUrl,
} from "../api/api-service";
import {
  deleteScan,
  enqueueScan,
  fetchScanHistory,
  fetchScanQueue,
} from "../api/api-scans";
import { deleteExport, startCreditsDetection } from "../api/api-reviews";
import type { ScanCapabilities } from "../api/api-system-types";
import type { JellyfinMediaItem } from "../api/api-media-types";
import type { ExportFormat, ExportStatus } from "../api/api-mcf-types";
import type {
  ScanOptions,
  Scan,
} from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import {
  validateDisplayName,
} from "../shared/name-validation";
import {
  usePageTitle,
} from "../shared/display";
import {
  ScanCapabilityDisclosure,
} from "../scans/scan-capability-disclosure";
import { AudioLanguageHelp, QwenModelField } from "../scans/qwen-model-field";
import { ModelOptions, resolveModelChoice } from "../scans/model-options";
import { AudioTrackField } from "../scans/audio-track-field";
import { useAudioTracks } from "../scans/use-audio-tracks";
import { IDLE_SCAN_POLL_MS, scanPollDelay, useScanPolling } from "../scans/use-scan-polling";
import { useFindFilters } from "../scans/use-find-filters";
import { FindFiltersDialog } from "../scans/find-filters-dialog";
import {
  formatRating,
  MediaDuration,
} from "./media-display";
import { MediaFiles } from "./media-files";
import {
  navigate,
} from "../routes";
import {
  ScanSelectionList,
  ScanDeleteDialog,
} from "../scans/scan-components";
import {
  SavedReviewPanel,
} from "../review/review-workspace";
import {
  ManageExportsDialog,
} from "../review/review-controls";
import {
  importStatusMessage,
} from "../review/review-types";
import {
  legacyExports,
  normalizedExportStatus,
  withoutLegacyExport,
  ExportBadges,
} from "../review/export-status";
import {
  applicationUrl,
} from "../shared/runtime-urls";
import type { LookupInput } from "../api/api-community-lookup";
import { useCommunityDownloads } from "../scans/use-community-downloads";
import { communityReviewScans } from "../review/community-review-scans";
import { useCan } from "../auth/auth-gate";

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
export function MediaDetail({ item, breadcrumbs, onNavigate, seasonNumber, onDirtyChange }: { item: JellyfinMediaItem; breadcrumbs: Breadcrumb[]; onNavigate: (route: string) => void; seasonNumber?: number | null; onDirtyChange: (dirty: boolean) => void }) {
  usePageTitle(item.name);
  const mayScan = useCan("scans.start");
  const mayReadFindings = useCan("scans.findings.read");
  const mayDeleteLegacy = useCan("exports.default.delete");
  const mayViewOwnJobs = useCan("scan_jobs.view.own");
  const mayViewOtherJobs = useCan("scan_jobs.view.other");
  const mayReadHistory = mayReadFindings || mayViewOwnJobs || mayViewOtherJobs;
  const maySearchCommunity = useCan("community.search");
  const imageTag = typeof item.image_tags.Primary === "string" ? item.image_tags.Primary : undefined;
  const imageUrl = item.image_url ? applicationUrl(item.image_url) : (imageTag ? primaryImageUrl(item.id, imageTag) : null);
  const [showNewScan, setShowNewScan] = useState(false);
  const [scanType, setScanType] = useState<"audio" | "video" | "combined" | "credits">("audio");
  const scanAudio = scanType === "audio" || scanType === "combined";
  const scanVideo = scanType === "video" || scanType === "combined";
  const [audioModel, setAudioModel] = useState("whisper");
  const [audioFilter, setAudioFilter] = useState("better_profanity");
  const [videoModel, setVideoModel] = useState("");
  const [coarseScanInterval, setCoarseScanInterval] = useState(5);
  const [coarseScanEnabled, setCoarseScanEnabled] = useState(false);
  const [qwenModel, setQwenModel] = useState("");
  const [qwenSummariesPerSegment, setQwenSummariesPerSegment] = useState(4);
  const [qwenSegmentJoinGapSeconds, setQwenSegmentJoinGapSeconds] = useState(3);
  const [versions, setVersions] = useState(() => item.versions.map((version) => ({ ...version, exports: normalizedExportStatus(version) })));
  const [mediaSourceId, setMediaSourceId] = useState<string | null>(
    () => item.versions.find((version) => version.id)?.id || null,
  );
  const [selectedReviewMediaSourceId, setSelectedReviewMediaSourceId] = useState<string | null>(
    () => item.versions.find((version) => version.id)?.id || null,
  );
  const audioTracks = useAudioTracks({ item_id: item.id, media_source_id: mediaSourceId }, mayScan && showNewScan && scanAudio);
  const [importedReview, setImportedReview] = useState<SavedReview | null>(null);
  const [reviewDirty, setReviewDirty] = useState(false);
  const handleReviewDirtyChange = useCallback((dirty: boolean) => {
    setReviewDirty(dirty);
    onDirtyChange(dirty);
  }, [onDirtyChange]);
  const [capabilities, setCapabilities] = useState<ScanCapabilities | null>(null);
  const [adding, setAdding] = useState(false);
  const [deletingExport, setDeletingExport] = useState<ExportFormat | null>(null);
  const [showManageExports, setShowManageExports] = useState(false);
  const [fallbackExportStatus, setFallbackExportStatus] = useState<ExportStatus>(() => normalizedExportStatus(item));
  const [message, setMessage] = useState<string | null>(null);
  const [findFiltersActive, setFindFiltersActive] = useState(false);
  const scanContext = JSON.stringify([item.id, selectedReviewMediaSourceId]);
  const currentScanContext = useRef(scanContext);
  currentScanContext.current = scanContext;
  const [scanResult, setScanResult] = useState<{ context: string; scans: Scan[]; error: string | null } | null>(null);
  const scans = scanResult?.context === scanContext ? scanResult.scans : [];
  const scansError = scanResult?.context === scanContext ? scanResult.error : null;
  const [scanToDelete, setScanToDelete] = useState<Scan | null>(null);
  const [deletingScan, setDeletingScan] = useState(false);
  const newScanTriggerRef = useRef<HTMLButtonElement>(null);
  const findFiltersTriggerRef = useRef<HTMLButtonElement>(null);
  const scanPopoverRef = useRef<HTMLElement>(null);
  const wasNewScanOpen = useRef(false);
  const selectedReviewVersion = versions.find(
    (version) => version.id === selectedReviewMediaSourceId,
  );
  const lookupTracks = useAudioTracks({ item_id: item.id, media_source_id: selectedReviewMediaSourceId }, findFiltersActive);
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
      const [history, queue] = await Promise.all([
        fetchScanHistory(),
        mayViewOwnJobs || mayViewOtherJobs ? fetchScanQueue() : Promise.resolve([]),
      ]);
      const loaded = [...new Map([...history, ...queue].map((scan) => [scan.id, scan])).values()].filter((scan) => (
        scan.jellyfin_item_id === item.id
        && scan.library_id === item.library_id
        && (!selectedReviewMediaSourceId || scan.media_source_id === selectedReviewMediaSourceId)
      ));
      if (currentScanContext.current !== scanContext) return IDLE_SCAN_POLL_MS;
      setScanResult({ context: scanContext, scans: loaded, error: null });
      return scanPollDelay(loaded);
    }
    catch (error) {
      if (currentScanContext.current === scanContext) setScanResult({ context: scanContext, scans: [], error: error instanceof Error ? error.message : "Unable to load scan history." });
      return IDLE_SCAN_POLL_MS;
    }
  }, [item.id, item.library_id, mayReadHistory, mayViewOwnJobs, mayViewOtherJobs, scanContext, selectedReviewMediaSourceId]);
  const communityInput: LookupInput | null = maySearchCommunity && findFiltersActive ? {
      target: { source: "jellyfin", item_id: item.id, media_source_id: selectedReviewMediaSourceId },
      audio_selection: lookupTracks.ready ? lookupTracks.selection : null,
    } : null;
  const downloads = useCommunityDownloads(communityInput, () => void loadScans());
  const reviewScans = communityReviewScans(scans, { source: "jellyfin", item_id: item.id, media_source_id: selectedReviewMediaSourceId });
  const filters = useFindFilters({
    communityInput,
    target: {
      source: "jellyfin",
      item_id: item.id,
      media_source_id: selectedReviewMediaSourceId,
      library_id: item.library_id,
      item_name: item.name,
      item_type: item.item_type,
      duration_seconds: selectedReviewMediaSourceId
        ? selectedReviewVersion?.duration_seconds || item.duration_seconds
        : item.duration_seconds,
      scene_name: selectedReviewVersion?.scene_name || null,
      release_group: selectedReviewVersion?.release_group || null,
      media_format: selectedReviewVersion?.media_format || item.media_format,
      media_provider_id: item.media_provider_id,
    },
    onMessage: setMessage,
    onAssociated: async () => { setFindFiltersActive(false); await loadScans(); },
  });
  useScanPolling(loadScans, loadScans);
  useEffect(() => {
    if (!mayScan || !showNewScan) return;
    void fetchCapabilities().then((next) => {
      setCapabilities(next);
      setAudioModel((current) => (
        next.audio_models.length > 0 && !next.audio_models.some((model) => model.id === current)
          ? next.audio_models[0].id
          : current
      ));
      setAudioFilter((current) => (
        next.audio_filters.length > 0
        && !next.audio_filters.some((filter) => filter.id === current)
          ? next.audio_filters[0].id
          : current
      ));
      setVideoModel((current) => resolveModelChoice(current, next.video_models));
      setQwenModel((current) => resolveModelChoice(current, next.qwen_models));
    }).catch(() => setMessage("Unable to load scan options."));
  }, [mayScan, showNewScan]);
  useEffect(() => {
    setVersions(item.versions.map((version) => ({ ...version, exports: normalizedExportStatus(version) })));
    setMediaSourceId(item.versions.find((version) => version.id)?.id || null);
    setSelectedReviewMediaSourceId(item.versions.find((version) => version.id)?.id || null);
  }, [item.id, item.versions]);
  useEffect(() => {
    const wasOpen = wasNewScanOpen.current;
    wasNewScanOpen.current = showNewScan;
    if (showNewScan) {
      window.requestAnimationFrame(() => scanPopoverRef.current?.querySelector<HTMLElement>("input, select")?.focus());
    } else if (wasOpen) {
      newScanTriggerRef.current?.focus();
    }
  }, [showNewScan]);
  const addToQueue = async () => {
    if (!mayScan) return;
    const scanName = validateDisplayName(scanPopoverRef.current?.querySelector<HTMLInputElement>('input[aria-label="Scan name (optional)"]')?.value || "", false);
    if (scanName.error) { setMessage(scanName.error); return; }
    setAdding(true); setMessage(null);
    if (scanType === "credits") {
      try {
        const result = await startCreditsDetection({
          target: { source: "jellyfin", item_id: item.id, media_source_id: mediaSourceId },
          qwen_model: null,
        });
        setShowNewScan(false);
        setMessage(`Credits scan ${result.scan.scan_id} added to the queue.`);
        await loadScans();
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Unable to add credits scan to queue.");
      } finally {
        setAdding(false);
      }
      return;
    }
    const options: ScanOptions = {
      name: scanName.normalized,
      scan_audio: scanAudio,
      audio_model: scanAudio ? audioModel : null,
      audio_filter: scanAudio ? audioFilter : null,
      scan_video: scanVideo,
      ...(scanVideo ? (videoModel ? { video_model: videoModel } : {}) : { video_model: null }),
      coarse_scan_interval: scanVideo && coarseScanEnabled ? coarseScanInterval : undefined,
      coarse_scan_enabled: scanVideo && coarseScanEnabled,
      ...(qwenModel ? { qwen_model: qwenModel } : {}),
      qwen_summaries_per_segment: qwenSummariesPerSegment,
      qwen_segment_join_gap_seconds: qwenSegmentJoinGapSeconds,
    };
    try {
      if (scanAudio) options.audio_selection = await audioTracks.prepare();
      const scan = await enqueueScan(item.id, options, mediaSourceId);
      setShowNewScan(false);
      setMessage(`Scan ${scan.scan_id} added to the queue.`);
      await loadScans();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Unable to add scan to queue."); }
    finally { setAdding(false); }
  };
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
  const leaveDetail = (route: string) => onNavigate(route);
  const selectReviewVersion = (sourceId: string | null) => {
    if (reviewDirty && !window.confirm("Discard unsaved review changes and switch versions?")) return;
    setImportedReview(null);
    setSelectedReviewMediaSourceId(sourceId);
  };
  const trapNewScanFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !adding) {
      event.preventDefault();
      setShowNewScan(false);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(scanPopoverRef.current?.querySelectorAll<HTMLElement>(
      "input:not([disabled]), select:not([disabled]), button:not([disabled])",
    ) || []);
    if (!focusable.length) return;
    const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const nextIndex = event.shiftKey
      ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
      : (currentIndex === focusable.length - 1 ? 0 : currentIndex + 1);
    event.preventDefault();
    focusable[nextIndex].focus();
  };
  return <div className="content-page media-detail">
    <LibraryBreadcrumbs crumbs={breadcrumbs} onNavigate={leaveDetail} />
    <div className="detail-summary"><div className="detail-poster">{imageUrl ? <img src={imageUrl} alt="" /> : <span>{item.name.slice(0, 1).toUpperCase()}</span>}</div><div><p className="eyebrow">{item.item_type}</p><h1>{item.name}</h1><p className="detail-meta">{item.year || "Year unavailable"} · Rating {formatRating(item)}</p><ExportBadges status={activeExportStatus} /><p>{item.overview || "No overview is available from Jellyfin."}</p><dl className="detail-metadata"><div><dt>Resolution</dt><dd>{item.resolution || "Unavailable"}</dd></div><div><dt>Format</dt><dd>{item.media_format || "Unavailable"}</dd></div><div><dt>Length</dt><dd><MediaDuration seconds={item.duration_seconds} /></dd></div><div><dt>Files</dt><dd>{item.media_source_count ?? item.versions.length}</dd></div></dl></div></div>
    <MediaFiles versions={versions} />
    <div className="detail-actions">{mayScan && <button className="btn-primary" ref={newScanTriggerRef} onClick={() => setShowNewScan(true)}>New Scan</button>}{mayReadFindings && <button className="btn-secondary" ref={findFiltersTriggerRef} onClick={() => { setFindFiltersActive(true); void filters.find(false); }} disabled={filters.finding}>{filters.finding ? "Finding Filters…" : "Find Filters"}</button>}{mayDeleteLegacy && Object.values(legacyExports(activeExportStatus)).some((status) => status.present) && <button className="btn-secondary" onClick={() => setShowManageExports(true)}>Manage exports</button>}</div>
    {message && <p className="form-status" role="status">{message}</p>}
    {filters.open && <FindFiltersDialog candidates={filters.candidates} results={filters.results} selectedIds={filters.selectedIds} associating={filters.associating} onSelect={filters.select} onAssociate={() => void filters.associate()} onClose={() => { filters.close(); setFindFiltersActive(false); }} returnFocusRef={findFiltersTriggerRef}
      community={{ lookup: filters.community, downloads, tracks: { inventory: lookupTracks.inventory, selection: lookupTracks.selection,
        loading: lookupTracks.loading, error: lookupTracks.error, disabled: filters.associating,
        onChange: lookupTracks.onChange, onRetry: lookupTracks.onRetry } }} />}
    <section className="settings-card"><div className="section-heading"><div><h2>Scans</h2><p>Immutable scan evidence available to saved reviews.</p></div></div>{scansError ? <div className="load-error" role="alert"><span>{scansError}</span><button className="btn-secondary" onClick={() => void loadScans()}>Retry</button></div> : scans.length ? <ScanSelectionList scans={scans} onDeleteScan={setScanToDelete} onRenamed={() => void loadScans()} /> : <p className="muted-copy">No scans yet. Create a new scan to begin.</p>}</section>
    {versions.length > 1 && <label className="field review-version-selector"><span>Review version</span><select aria-label="Review version" value={selectedReviewMediaSourceId || ""} onChange={(event) => selectReviewVersion(event.target.value || null)}>{versions.map((version, index) => <option key={version.id || `${version.name || "version"}-${index}`} value={version.id || ""} disabled={!version.id}>{version.name || `Version ${index + 1}`}</option>)}</select></label>}
    <SavedReviewPanel key={selectedReviewMediaSourceId || "default"} item={item} seasonNumber={seasonNumber} mediaSourceId={selectedReviewMediaSourceId} scans={reviewScans} importedReview={importedReview} exportStatus={activeExportStatus} onExportStatusChange={updateActiveExportStatus} onDirtyChange={handleReviewDirtyChange} onSaved={() => void loadScans()} onImported={(result) => { setImportedReview(result.review); setMessage(importStatusMessage(result)); void loadScans(); }} />
    {mayScan && showNewScan && <div className="modal-backdrop" role="presentation"><section className="scan-popover catalog-scan-dialog" ref={scanPopoverRef} onKeyDown={trapNewScanFocus} role="dialog" aria-modal="true" aria-labelledby="new-scan-heading">
      <h2 id="new-scan-heading">New Scan</h2>
      {versions.length > 0 && <label className="field"><span>Version</span><select aria-label="Version" value={mediaSourceId || ""} onChange={(event) => setMediaSourceId(event.target.value || null)}>{versions.map((version, index) => <option key={version.id || `${version.name || "version"}-${index}`} value={version.id || ""} disabled={!version.id}>{version.name || `Version ${index + 1}`}{version.resolution ? ` · ${version.resolution}` : ""}{version.media_format ? ` · ${version.media_format}` : ""}</option>)}</select></label>}
      <label className="field"><span>Scan type</span><select aria-label="Scan type" value={scanType} onChange={(event) => setScanType(event.target.value as typeof scanType)}><option value="audio">Audio</option><option value="video">Video</option><option value="combined">Audio and video</option><option value="credits">Credits</option></select></label>
      {scanAudio && <><AudioTrackField {...audioTracks} disabled={adding} /><label className="field"><span>Transcriber</span><select aria-label="Transcriber" value={audioModel} onChange={(event) => setAudioModel(event.target.value)}>{(capabilities?.audio_models || []).map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label><label className="field"><span>Filter</span><select aria-label="Filter" value={audioFilter} onChange={(event) => setAudioFilter(event.target.value)}>{(capabilities?.audio_filters || []).map((filter) => <option key={filter.id} value={filter.id}>{filter.display_name}</option>)}</select></label><AudioLanguageHelp /></>}
      {scanVideo && <><label className="field"><span>Video model</span><select aria-label="Video model" value={videoModel} onChange={(event) => setVideoModel(event.target.value)}><ModelOptions models={capabilities?.video_models} /></select></label><label><input type="checkbox" checked={coarseScanEnabled} onChange={(event) => setCoarseScanEnabled(event.target.checked)} /> Enable coarse scan</label>{coarseScanEnabled && <label className="field"><span>Coarse scan interval (seconds)</span><input type="number" min="0.1" step="0.1" value={coarseScanInterval} onChange={(event) => setCoarseScanInterval(Number(event.target.value))} /></label>}<label className="field"><span>Representative frames per flagged segment</span><input aria-label="Representative frames per flagged segment" type="number" min="1" step="1" value={qwenSummariesPerSegment} onChange={(event) => setQwenSummariesPerSegment(Number(event.target.value))} /></label><label className="field"><span>Join flagged segments within (seconds)</span><input aria-label="Join flagged segments within (seconds)" type="number" min="0" step="0.1" value={qwenSegmentJoinGapSeconds} onChange={(event) => setQwenSegmentJoinGapSeconds(Number(event.target.value))} /></label><p className="muted-copy">Fine scans analyze every source frame. Qwen receives evenly spaced representative flagged frames together after the scene scan; short clean gaps can be joined into one segment.</p></>}
      {scanType === "credits" ? <p className="muted-copy">Detect the start of closing credits using local chapter and visual analysis.</p> : <QwenModelField capabilities={capabilities} value={qwenModel} onChange={setQwenModel} />}
      {scanAudio && <ScanCapabilityDisclosure capabilities={capabilities} backendId={audioModel} scanType="audio" />}
      {scanVideo && <ScanCapabilityDisclosure capabilities={capabilities} backendId={videoModel} scanType="video" includeScanName={!scanAudio} />}
      <div className="button-row"><button className="btn-secondary" onClick={() => setShowNewScan(false)} disabled={adding}>Cancel</button><button className="btn-primary" onClick={() => void addToQueue()} disabled={adding || (scanAudio && !audioTracks.ready) || (scanVideo && (coarseScanEnabled && coarseScanInterval <= 0 || qwenSummariesPerSegment < 1 || qwenSegmentJoinGapSeconds < 0))}>{adding ? "Adding…" : "Add to Queue"}</button></div>
    </section></div>}
    {showManageExports && <ManageExportsDialog status={activeExportStatus} subject={selectedReviewVersion?.name || item.name} deleting={deletingExport} onDelete={(format) => void deleteManagedExport(format)} onClose={() => setShowManageExports(false)} />}
    {scanToDelete && <ScanDeleteDialog scan={scanToDelete} deleting={deletingScan} onClose={() => setScanToDelete(null)} onDelete={() => void deleteSelectedScan()} />}
  </div>;
}
