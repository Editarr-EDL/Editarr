import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  cancelScanQueueItem,
  fetchScanQueue,
  fetchScanHistory,
  resumeOrphanedScan,
  reorderScanQueue,
} from "../api/api-scans";
import { fetchFingerprintQueue, hasPendingFingerprintWork, type FingerprintQueueItem } from "../api/api-stable-media";
import { OPEN_SCAN_QUEUE_EVENT } from "../shared/scan-events";
import type { Scan, ScanQueueItem } from "../api/api-scan-types";
import {
  scanLabel,
  scanOriginLabel,
} from "../shared/display";
import {
  navigate,
} from "../routes";
import {
  formatScanDuration,
  ScanDuration,
  ScanProgressDetails,
} from "./scan-components";
import { ScanOptionsInfo, type ScanOptionsHost } from "./scan-options-info";
import {
  ModalDialog,
} from "../ui/ModalDialog";
import { ACTIVE_SCAN_POLL_MS, IDLE_SCAN_POLL_MS, scanPollDelay, useScanPolling } from "./use-scan-polling";
import { usePrincipal } from "../auth/auth-gate";
import { can, type Principal } from "../auth/session";
import { scanActionAllowed } from "./scan-access";

/** Track queued and completed scans in a shared expandable panel. */
export function ScansFloatingPanel() {
  const principal = usePrincipal();
  if (!principal || !(mayViewScans(principal) || can(principal, "reviews.import.upload"))) return null;
  return <AuthorizedScansPanel principal={principal} />;
}

function mayViewScans(principal: Principal) {
  return can(principal, "scan_jobs.view.own") || can(principal, "scan_jobs.view.other");
}

function AuthorizedScansPanel({ principal }: { principal: Principal }) {
  const showScans = mayViewScans(principal);
  const showFingerprints = can(principal, "reviews.import.upload");
  const mayReorder = can(principal, "scan_jobs.reorder");
  const [expanded, setExpanded] = useState(false);
  const [queueItems, setQueueItems] = useState<ScanQueueItem[]>([]);
  const [fingerprints, setFingerprints] = useState<FingerprintQueueItem[]>([]);
  const [historyItems, setHistoryItems] = useState<Scan[]>([]);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [itemToCancelQueueId, setItemToCancelQueueId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const activeOptionsDismiss = useRef<(() => void) | null>(null);
  const optionsHost = { panelRef: containerRef, activeDismiss: activeOptionsDismiss };
  const queueLoadingRef = useRef(false);
  const [now, setNow] = useState(() => Date.now());
  const [serverClockOffset, setServerClockOffset] = useState<number | null>(null);
  const serverNow = now + (serverClockOffset ?? 0);
  const showActivity = expanded && queueItems.some(
    (item) => item.status === "scanning" || (item.status === "cancelling" && item.scan_duration_as_of),
  );

  useEffect(() => {
    if (!showActivity) return;
    let interval: number | undefined;
    const sync = () => {
      window.clearInterval(interval);
      if (!document.hidden) {
        setNow(Date.now());
        interval = window.setInterval(() => setNow(Date.now()), 1_000);
      }
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [showActivity]);

  const loadQueue = useCallback(async () => {
    if (queueLoadingRef.current) return IDLE_SCAN_POLL_MS;
    queueLoadingRef.current = true;
    try {
      const requestedAt = Date.now();
      const loaded = (await fetchScanQueue()).filter((item) => scanActionAllowed(principal, item, "view"));
      const receivedAt = Date.now();
      const sample = loaded.find((item) => item.scan_duration_as_of)?.scan_duration_as_of;
      setNow(receivedAt);
      // Calibrate once while a timer is running so polling latency cannot reset its clock.
      setServerClockOffset((offset) => sample
        ? offset ?? Date.parse(sample) - (requestedAt + receivedAt) / 2
        : null);
      setQueueItems(loaded);
      setQueueError(null);
      return scanPollDelay(loaded);
    } catch (reason) {
      setQueueError(reason instanceof Error ? reason.message : "Unable to load Editarr queue.");
      return IDLE_SCAN_POLL_MS;
    } finally {
      queueLoadingRef.current = false;
    }
  }, [principal]);

  const loadHistory = useCallback(async () => {
    try {
      setHistoryItems(await fetchScanHistory());
      setHistoryError(null);
    } catch (reason) {
      setHistoryError(reason instanceof Error ? reason.message : "Unable to load Editarr history.");
    }
  }, []);

  useScanPolling(async () => {
    const [delay, activeFingerprints] = await Promise.all([
      showScans ? loadQueue() : Promise.resolve(IDLE_SCAN_POLL_MS),
      showFingerprints ? fetchFingerprintQueue().catch(() => []) : Promise.resolve([]),
    ]);
    setFingerprints(activeFingerprints);
    if (expanded && showScans) await loadHistory();
    return hasPendingFingerprintWork() || activeFingerprints.length ? ACTIVE_SCAN_POLL_MS : delay;
  }, expanded);

  useEffect(() => {
    const openQueue = () => setExpanded(true);
    window.addEventListener(OPEN_SCAN_QUEUE_EVENT, openQueue);
    return () => window.removeEventListener(OPEN_SCAN_QUEUE_EVENT, openQueue);
  }, []);

  useEffect(() => {
    if (!expanded) return;
    const closeWhenOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setExpanded(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) setExpanded(false);
    };
    document.addEventListener("mousedown", closeWhenOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeWhenOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [expanded]);

  const activeItem = queueItems.find((item) => item.status === "scanning") || queueItems[0];
  const progress = activeItem?.status === "scanning"
    ? Math.max(0, Math.min(activeItem.progress_percent || 0, 100))
    : 0;
  const itemToCancel = itemToCancelQueueId === null
    ? null
    : queueItems.find((item) => item.queue_id === itemToCancelQueueId) ?? null;

  const reorder = async (queueId: number, offset: number) => {
    if (!mayReorder) return;
    const index = queueItems.findIndex((item) => item.queue_id === queueId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= queueItems.length) return;
    const reordered = [...queueItems];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    setUpdating(true);
    try {
      await reorderScanQueue(reordered.map((item) => item.queue_id));
      setQueueItems(reordered);
    } catch (reason) {
      setQueueError(reason instanceof Error ? reason.message : "Unable to reorder Editarr queue.");
    } finally {
      setUpdating(false);
    }
  };

  const cancel = async () => {
    if (!itemToCancel || !scanActionAllowed(principal, itemToCancel, "cancel")) return;
    setUpdating(true);
    try {
      await cancelScanQueueItem(itemToCancel.queue_id);
      setItemToCancelQueueId(null);
      await Promise.all([loadQueue(), loadHistory()]);
    } catch (reason) {
      setQueueError(reason instanceof Error ? reason.message : "Unable to cancel Editarr scan.");
    } finally {
      setUpdating(false);
    }
  };

  const resume = async (queueId: number) => {
    const item = queueItems.find((candidate) => candidate.queue_id === queueId);
    if (!item || !scanActionAllowed(principal, item, "resume")) return;
    setUpdating(true);
    try {
      await resumeOrphanedScan(queueId);
      await loadQueue();
    } catch (reason) {
      setQueueError(reason instanceof Error ? reason.message : "Unable to resume Editarr scan.");
    } finally {
      setUpdating(false);
    }
  };

  return <div className="scans-floating-control" ref={containerRef}>
    <button
      type="button"
      className={`scans-floating-button ${expanded ? "expanded" : ""}`}
      style={{ "--queue-progress": `${progress}%` } as CSSProperties}
      onClick={() => setExpanded((value) => !value)}
      aria-label={expanded ? "Close scans queue and history" : "Open scans queue and history"}
      aria-expanded={expanded}
      aria-controls="scans-floating-popover"
      title="Scans queue and history"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h9" /><path d="M17 16.5 19.5 19 23 15.5" /></svg>
      <span className="scans-floating-badge" aria-hidden="true">{queueItems.length + fingerprints.length}</span>
    </button>
    {expanded && <section className="scans-floating-popover" id="scans-floating-popover" role="region" aria-label="Scans queue and history">
      <header className="scans-floating-heading"><div><p className="eyebrow">Scans</p><h2>Queue</h2></div><button className="icon-button" type="button" onClick={() => setExpanded(false)} aria-label="Collapse scans panel">×</button></header>
      {!!fingerprints.length && <FingerprintQueue items={fingerprints} />}
      {showScans && <section className="scans-floating-section" aria-label="Scan queue">
        {queueError ? <div className="load-error compact-load-error" role="alert"><span>{queueError}</span><button className="btn-secondary compact-button" onClick={() => void loadQueue()}>Retry</button></div> : queueItems.length ? <div className="library-table-wrap scans-floating-table-wrap" tabIndex={0} aria-label="Queued scans table"><table className="library-table scans-floating-table"><thead><tr><th>Item</th><th>Status</th><th>Duration</th><th><span className="visually-hidden">Queue actions</span></th></tr></thead><tbody>{queueItems.map((item, index) => <tr key={item.id}><td><span className="scan-item-name">{item.library_id ? <button className="item-link" onClick={() => { navigate(`library/${encodeURIComponent(item.library_id!)}/item/${encodeURIComponent(item.jellyfin_item_id)}`); setExpanded(false); }}>{item.item_name}</button> : item.item_name}<ScanOptionsInfo scan={item} host={optionsHost} /></span><small>{scanLabel(item)} · {scanOriginLabel(item)}</small></td><td><strong>{item.status}</strong><ScanProgressDetails scan={item} now={serverNow} /></td><td><ScanDuration scan={item} now={serverNow} /></td><td><div className="queue-actions">{mayReorder && <><button className="icon-button" aria-label={`Move ${scanLabel(item)} up`} onClick={() => void reorder(item.queue_id, -1)} disabled={updating || index === 0}>↑</button><button className="icon-button" aria-label={`Move ${scanLabel(item)} down`} onClick={() => void reorder(item.queue_id, 1)} disabled={updating || index === queueItems.length - 1}>↓</button></>}{item.status === "orphaned" && scanActionAllowed(principal, item, "resume") && <button className="btn-secondary compact-button" onClick={() => void resume(item.queue_id)} disabled={updating}>Resume</button>}{scanActionAllowed(principal, item, "cancel") && <button className="danger-button compact-button" onClick={() => setItemToCancelQueueId(item.queue_id)} disabled={updating || item.status === "cancelling"}>{item.status === "scanning" ? "Cancel" : item.status === "orphaned" ? "Delete" : "Remove"}</button>}</div></td></tr>)}</tbody></table></div> : <p className="muted-copy">No queued scans.</p>}
      </section>}
      {showScans && <section className="scans-floating-section scans-history-section" aria-label="Scan history"><div className="scans-floating-section-heading"><h3>History</h3><span>{historyItems.length} scans</span></div>{historyError ? <div className="load-error compact-load-error" role="alert"><span>{historyError}</span><button className="btn-secondary compact-button" onClick={() => void loadHistory()}>Retry</button></div> : historyItems.length ? <div className="library-table-wrap scans-floating-table-wrap" tabIndex={0} aria-label="Completed scans table"><ScanTable items={historyItems.slice(0, 12)} host={optionsHost} /></div> : <p className="muted-copy">No completed scans yet.</p>}</section>}
    </section>}
    {itemToCancel && <ModalDialog labelledBy="floating-cancel-scan-heading" className="confirmation-dialog" onClose={updating ? undefined : () => setItemToCancelQueueId(null)}><h2 id="floating-cancel-scan-heading">{itemToCancel.status === "scanning" ? "Cancel" : itemToCancel.status === "orphaned" ? "Delete" : "Remove"} {scanLabel(itemToCancel)}?</h2><p>{itemToCancel.status === "scanning" ? "Editarr will stop this scan as soon as its current scanner step can be safely cancelled." : itemToCancel.status === "orphaned" ? "This removes the orphaned scan from the queue." : "This queued scan will be removed and retained as cancelled history."}</p><div className="button-row"><button className="btn-secondary" onClick={() => setItemToCancelQueueId(null)} disabled={updating}>Keep scan</button><button className="danger-button" onClick={() => void cancel()} disabled={updating}>{updating ? "Working…" : "Confirm"}</button></div></ModalDialog>}
  </div>;
}


function FingerprintQueue({ items }: { items: FingerprintQueueItem[] }) {
  return <section className="scans-floating-section" aria-label="File fingerprints">
    <div className="scans-floating-section-heading"><h3>File fingerprints</h3></div>
    <ul className="fingerprint-queue">
      {items.map((item) => {
        const percent = item.bytes_total > 0 ? Math.floor(100 * item.bytes_read / item.bytes_total) : 0;
        return <li key={item.id}>
          <strong>{item.item_name}</strong>
          {item.version_name && <small>{item.version_name}</small>}
          <span>{item.reading ? `Calculating fingerprint · ${percent}%` : "Waiting to calculate fingerprint"}</span>
          <progress aria-label={`Fingerprint progress for ${item.item_name}`} max={100} value={percent} />
        </li>;
      })}
    </ul>
  </section>;
}

function ScanTable({ items, host }: {
  items: Scan[];
  host: ScanOptionsHost;
}) {
  return <table className="library-table">
    <thead><tr><th>Item</th><th>Scan</th><th>Status</th><th>Filters</th><th>Duration</th></tr></thead>
    <tbody>{items.map((item) => <tr key={item.id}>
      <td><span className="scan-item-name">
        {item.library_id
          ? <button className="item-link" onClick={() => navigate(`library/${encodeURIComponent(item.library_id!)}/item/${encodeURIComponent(item.jellyfin_item_id)}`)}>{item.item_name}</button>
          : item.item_name}
        <ScanOptionsInfo scan={item} host={host} />
      </span></td>
      <td>{scanLabel(item)}<small>{scanOriginLabel(item)}</small></td>
      <td>{item.status}{item.error_message ? ` · ${item.error_message}` : ""}</td>
      <td>{item.finding_count ?? 0}</td>
      <td><time dateTime={item.scan_started_at || undefined}>{formatScanDuration(item)}</time></td>
    </tr>)}</tbody>
  </table>;
}
