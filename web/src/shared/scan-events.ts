export const SCANS_CHANGED_EVENT = "editarr:scans-changed";

export function notifyScansChanged() {
  window.dispatchEvent(new Event(SCANS_CHANGED_EVENT));
}