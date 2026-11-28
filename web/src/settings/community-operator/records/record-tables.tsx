import type { ReactNode } from "react";
import type { OperatorRow } from "../../../api/api-community-operator";
import { dayTime, titleCase } from "../shared/format";
import { contributorLink, itemLink, operatorLink, resourceLink } from "../routes";

/** A column: header, cell renderer and optional class (`r` right-aligns). */
export type Column = [string, (row: OperatorRow) => ReactNode, string?];
const text = (value: unknown) => typeof value === "string" ? value : value === null || value === undefined ? "—" : String(value);
const when = (value: unknown) => typeof value === "number" ? dayTime(value) : "—";
const chip = (value: unknown, tones: Record<string, string>) => <span className={`chip ${tones[text(value)] ?? ""}`}>{titleCase(text(value))}</span>;
const statusTones = { confirmed: "ok", candidate: "cand", merged_into: "dim", pending: "cand", approved: "ok", rejected: "bad", open: "cand",
  resolved: "ok", active: "ok", suspended: "bad", completed: "ok", failed: "bad" };
/** Display name of a version row: its label text, alias ID or the default Theatrical alias. */
const versionLabel = (row: OperatorRow) => text(row.labelText ?? row.aliasId ?? "Theatrical (default)");

export const versionColumns: Column[] = [
  ["Version", row => <><a href={itemLink(text(row.workId), text(row.versionId))}>{versionLabel(row)}</a>
    <div className="faint mono">{text(row.versionId)}</div></>],
  ["Status", row => <>{chip(row.status, statusTones)}{row.quarantined === 1 && <span className="chip bad mini">quarantined</span>}
    {typeof row.mergedInto === "string" && <div className="faint small">into <a href={resourceLink("version", row.mergedInto)}>{row.mergedInto}</a></div>}</>],
  ["Trust", row => typeof row.trust === "number" ? row.trust.toFixed(2) : "—", "r"],
  ["Revision", row => text(row.revision), "r"],
  ["Filters", row => `${text(row.servedFilterCount)} of ${text(row.filterCount)}`, "r"],
  ["Contributors", row => text(row.contributorCount), "r"],
  ["Created", row => when(row.createdAt)],
];
export const aliasColumns: Column[] = [
  ["Suggested label", row => <><b>“{text(row.text)}”</b><div className="faint small">normalised “{text(row.normalized)}”</div></>],
  ["State", row => chip(row.state, statusTones)],
  ["Alias", row => <span className="mono">{text(row.aliasId)}</span>],
  ["Submission", row => <a className="mono" href={operatorLink("inspect", { kind: "submission", selected: text(row.submissionId) })}>{text(row.submissionId)}</a>],
  ["Created", row => when(row.createdAt)],
];
export const reportColumns: Column[] = [
  ["Reason", row => chip(row.reason, {})],
  ["Target", row => <>{titleCase(text(row.targetType))}{" "}
    <a className="mono" href={resourceLink(row.targetType === "filter" ? "filter" : "version", text(row.targetId))}>{text(row.targetId)}</a></>],
  ["Reporter", row => <a className="mono" href={contributorLink(text(row.contributorId))}>{text(row.contributorId)}</a>],
  ["Private note", row => <span className="note">{text(row.note)}</span>, "wrap"],
  ["Created", row => when(row.createdAt)],
];
export const contributorColumns: Column[] = [
  ["Contributor", row => <><a href={contributorLink(text(row.contributorId))}>{text(row.displayName)}</a><div className="faint mono">{text(row.contributorId)}</div></>],
  ["State", row => chip(row.state, statusTones)],
  ["Joined", row => when(row.createdAt)],
];
export const operationColumns: Column[] = [
  ["Operation", row => <><b>{titleCase(text(row.kind))}</b><div className="faint mono">{text(row.operationId)}</div></>],
  ["Contributor", row => <a className="mono" href={contributorLink(text(row.contributorId))}>{text(row.contributorId)}</a>],
  ["State", row => chip(row.state, statusTones)],
  ["Started", row => when(row.createdAt)],
  ["Updated", row => when(row.updatedAt)],
];

/** A paged record table with one "Details" link per row; `selected` highlights the open row. */
export function RecordTable({ label, rows, columns, details, selected, idOf }: { label: string; rows: OperatorRow[]; columns: Column[];
  details?: (row: OperatorRow) => string; selected?: string | null; idOf: (row: OperatorRow) => string }) {
  return <div className="tw" role="region" aria-label={`${label} table`} tabIndex={0}><table className="t records">
    <caption className="sr">{label}</caption>
    <thead><tr>{columns.map(([header, , className]) => <th scope="col" key={header} className={className}>{header}</th>)}
      {details && <th scope="col"><span className="sr">Details</span></th>}</tr></thead>
    <tbody>{rows.map(row => <tr key={idOf(row)} className={idOf(row) === selected ? "sel" : undefined}>
      {columns.map(([header, cell, className]) => <td key={header} className={className}>{cell(row)}</td>)}
      {details && <td className="r"><a className="btn sm" href={details(row)} aria-current={idOf(row) === selected ? "true" : undefined}>
        Details<span className="sr"> {idOf(row)}</span></a></td>}</tr>)}
      {!rows.length && <tr><td colSpan={columns.length + (details ? 1 : 0)} className="muted">No records.</td></tr>}</tbody></table></div>;
}
