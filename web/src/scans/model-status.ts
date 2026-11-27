import { formatBytes } from "../shared/display";
import type { ModelStatus } from "./model-catalog";

export type StatusTone = "ready" | "need" | "busy" | "error" | "off";

export interface StatusView {
  tone: StatusTone;
  /** Short status line, e.g. "Downloads first". */
  text: string;
  /** Size or source line shown above the status in rows, e.g. "5.4 GB" or "Built in". */
  size: string | null;
  /** One-line chip text for the summary card. */
  chip: string;
  /** Download progress from 0 to 100 when known. */
  percent: number | null;
}

const size = (bytes: number | null) => (bytes === null ? null : formatBytes(bytes));

/** Present a model's download or availability state for the picker card and rows. */
export function statusView(status: ModelStatus): StatusView {
  switch (status.kind) {
    case "built_in":
      return { tone: "ready", text: "No download", size: "Built in", chip: "Built in · no download", percent: null };
    case "ready": {
      const total = size(status.bytesTotal);
      return { tone: "ready", text: "Downloaded", size: total, chip: total ? `Ready · ${total}` : "Ready", percent: null };
    }
    case "download": {
      const total = size(status.bytesTotal);
      return { tone: "need", text: "Downloads first", size: total, chip: total ? `Downloads ${total} first` : "Downloads first", percent: null };
    }
    case "downloading": {
      const { bytesDownloaded: done, bytesTotal: total } = status;
      const percent = done !== null && total ? Math.min(100, Math.round((100 * done) / total)) : null;
      const text = percent === null ? "Downloading" : `Downloading ${percent}%`;
      return { tone: "busy", text, size: done !== null && total ? `${formatBytes(done)} of ${formatBytes(total)}` : size(total), chip: text, percent };
    }
    case "failed":
      return { tone: "error", text: status.detail ? `Download needs attention: ${status.detail}` : "Download needs attention",
        size: null, chip: "Download needs attention", percent: null };
    case "unavailable":
      return { tone: "off", text: status.reason, size: null, chip: "Unavailable", percent: null };
  }
}
