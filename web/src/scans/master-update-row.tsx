import type { FilterValues, UpdateDecision, UpdateGroup, UpdateRow } from "../api/api-community-master-types";
import { filterName, formatMs } from "./community-master-format";
import { MasterFieldChanges } from "./master-field-changes";
import { MiniTimeline } from "./master-timeline";

const decisionLabels: Record<UpdateDecision, string> = { accept: "Accept", keep_mine: "Keep mine", dismiss: "Dismiss" };

/** Note shown for rows the user touched; they are decided one at a time. */
function touchedNote(group: UpdateGroup) {
  return group === "removed" ? "You kept this" : "You edited this";
}

/**
 * One update row: category, a mini timeline (mine/old/new), old → new values for every changed field
 * (with mine for edited rows), a clip preview of the
 * new (or old, when removed) bounds, and the decisions the server allows. Accept stays disabled
 * until `canAccept` (fuzzy links need alignment first).
 */
export function MasterUpdateRow({ row, group, busy, clipLoading, onDecide, onPlay }: {
  row: UpdateRow; group: UpdateGroup; busy: boolean; clipLoading: boolean;
  onDecide: (decision: UpdateDecision) => void; onPlay: (range: FilterValues) => void;
}) {
  const reference = row.newMaster ?? row.oldMaster ?? row.mine;
  const name = filterName({ category: row.category, subcategory: reference?.subcategory ?? null });
  const label = reference ? `${name} at ${formatMs(reference.startMs)}` : name;
  const clipRange = group === "removed" ? row.oldMaster ?? row.mine : row.newMaster;
  return <li className={`master-update-row${row.touched ? " master-update-touched" : ""}`}>
    <div className="master-update-heading">
      <strong>{name}</strong>
      {row.touched && <span className="master-badge master-touched">{touchedNote(group)}</span>}
      {row.mergedInto && <span className="muted-copy">Merged into another filter</span>}
    </div>
    <MiniTimeline mine={row.touched ? row.mine : null} oldMaster={row.oldMaster} newMaster={row.newMaster} />
    <MasterFieldChanges row={row} label={label} />
    <div className="button-row master-update-actions">
      {clipRange && <button type="button" className="btn-secondary compact-button" disabled={clipLoading}
        aria-label={`Preview clip for ${label}`} onClick={() => onPlay(clipRange)}>{clipLoading ? "Loading…" : "▶ Preview"}</button>}
      {(["accept", "keep_mine", "dismiss"] as const).filter((decision) => row.allowed.includes(decision)).map((decision) =>
        <button key={decision} type="button" className={decision === "accept" ? "btn-primary compact-button" : "btn-secondary compact-button"}
          disabled={busy || (decision === "accept" && !row.canAccept)} aria-label={`${decisionLabels[decision]} ${label}`}
          onClick={() => onDecide(decision)}>{decisionLabels[decision]}</button>)}
    </div>
  </li>;
}
