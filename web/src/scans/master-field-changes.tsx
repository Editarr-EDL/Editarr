import type { UpdateRow } from "../api/api-community-master-types";
import { categoryLabel } from "./community-result-state";
import { formatMs, percent } from "./community-master-format";

type Values = Record<string, unknown> | null;

export const fieldLabels: Record<string, string> = {
  startMs: "Start", endMs: "End", subcategory: "Subcategory", severity: "Severity", action: "Action",
  confidence: "Confidence", lowConfidence: "Low Confidence", present: "Presence",
};

/** Format one field value for display; missing values show as a dash. */
export function fieldValue(field: string, values: Values) {
  const value = values?.[field];
  if (value === undefined || value === null) return "—";
  if (field === "startMs" || field === "endMs") return typeof value === "number" ? formatMs(value) : "—";
  if (field === "confidence") return typeof value === "number" ? percent(value) : "—";
  if (field === "lowConfidence") return value ? "Low Confidence" : "Normal";
  if (field === "present") return value ? "Present" : "Removed";
  return categoryLabel(String(value));
}

/**
 * Old → new values for each changed field. Rows the user edited add a "Mine" column, so mine, the
 * old master value and the new master value are compared side by side.
 */
export function MasterFieldChanges({ row }: { row: UpdateRow }) {
  if (!row.changedFields.length) return null;
  const mine = row.touched;
  const oldMaster = row.oldMaster as Values;
  const newMaster = row.newMaster as Values;
  return <table className="master-field-changes">
    <caption className="visually-hidden">Changed fields</caption>
    <thead><tr><th scope="col">Field</th>{mine && <th scope="col">Mine</th>}<th scope="col">Old</th>
      <th scope="col"><span className="visually-hidden">changes to</span></th><th scope="col">New</th></tr></thead>
    <tbody>{row.changedFields.map((field) => <tr key={field}>
      <th scope="row">{fieldLabels[field] ?? field}</th>
      {mine && <td>{fieldValue(field, row.mine as Values)}</td>}
      <td>{fieldValue(field, oldMaster)}</td><td aria-hidden="true">→</td><td>{fieldValue(field, newMaster)}</td>
    </tr>)}</tbody>
  </table>;
}
