import type { ScanQueueItem } from "../api/api-scan-types";
import type { Principal } from "../auth/session";
import { navigate } from "../routes";
import { scanLabel, scanOriginLabel } from "../shared/display";
import { scanActionAllowed } from "./scan-access";
import { ScanDuration, ScanProgressDetails } from "./scan-components";
import { ScanOptionsInfo, type ScanOptionsHost } from "./scan-options-info";

export type QueueRunAction = "pause" | "resume";

export function queueRunAction(item: ScanQueueItem): QueueRunAction | null {
  if (item.status === "queued" || item.status === "scanning") return "pause";
  if (item.status === "paused" || item.status === "orphaned") return "resume";
  return null;
}

export function queueCancelLabel(item: ScanQueueItem): string {
  if (item.status === "orphaned") return "Delete";
  if (["scanning", "pausing", "paused", "cancelling"].includes(item.status)) return "Cancel";
  return "Remove";
}

export function ScanQueueTable({ items, principal, now, updating, mayReorder, host, onNavigate, onReorder, onRunAction, onCancel }: {
  items: ScanQueueItem[];
  principal: Principal;
  now: number;
  updating: boolean;
  mayReorder: boolean;
  host: ScanOptionsHost;
  onNavigate: () => void;
  onReorder: (queueId: number, offset: number) => void;
  onRunAction: (item: ScanQueueItem, action: QueueRunAction) => void;
  onCancel: (queueId: number) => void;
}) {
  return <div className="library-table-wrap scans-floating-table-wrap" tabIndex={0} aria-label="Queued scans table">
    <table className="library-table scans-floating-table">
      <thead><tr><th>Item</th><th>Status</th><th>Duration</th><th><span className="visually-hidden">Queue actions</span></th></tr></thead>
      <tbody>{items.map((item, index) => {
        const action = queueRunAction(item);
        return <tr key={item.id}>
          <td><span className="scan-item-name">
            {item.library_id ? <button className="item-link" onClick={() => {
              navigate(`library/${encodeURIComponent(item.library_id!)}/item/${encodeURIComponent(item.jellyfin_item_id)}`);
              onNavigate();
            }}>{item.item_name}</button> : item.item_name}
            <ScanOptionsInfo scan={item} host={host} />
          </span><small>{scanLabel(item)} · {scanOriginLabel(item)}</small></td>
          <td><strong aria-live="polite">{item.status === "paused" ? "Paused" : item.status === "pausing" ? "Pausing…" : item.status}</strong>
            <ScanProgressDetails scan={item} now={now} /></td>
          <td><ScanDuration scan={item} now={now} /></td>
          <td><div className="queue-actions">
            {mayReorder && <>
              <button className="icon-button" aria-label={`Move ${scanLabel(item)} up`} onClick={() => onReorder(item.queue_id, -1)} disabled={updating || index === 0}>↑</button>
              <button className="icon-button" aria-label={`Move ${scanLabel(item)} down`} onClick={() => onReorder(item.queue_id, 1)} disabled={updating || index === items.length - 1}>↓</button>
            </>}
            {action && scanActionAllowed(principal, item, "resume") && <button className="btn-secondary compact-button"
              onClick={() => onRunAction(item, action)} disabled={updating}>{action === "pause" ? "Pause" : "Resume"}</button>}
            {scanActionAllowed(principal, item, "cancel") && <button className="danger-button compact-button"
              onClick={() => onCancel(item.queue_id)} disabled={updating || item.status === "cancelling"}>{queueCancelLabel(item)}</button>}
          </div></td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}
