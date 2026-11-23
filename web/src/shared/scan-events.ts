export const SCANS_CHANGED_EVENT = "editarr:scans-changed";

export function notifyScansChanged() {
  window.dispatchEvent(new Event(SCANS_CHANGED_EVENT));
}
export const OPEN_SCAN_QUEUE_EVENT = "editarr:open-scan-queue";

export function openScanQueue() {
  window.dispatchEvent(new Event(OPEN_SCAN_QUEUE_EVENT));
}
