import { useState } from "react";
import type { FilterStats } from "../../../api/api-community-operator-labels";
import { categoryClass, filterName, filterSpan } from "../shared/format";
import { Votes } from "../shared/ui";

/** Master filters in runtime order; shows a window of rows around the selection until "Show all" is chosen. */
export function FilterTable({ filters, selected, href }: { filters: FilterStats[]; selected: string | null; href: (filterId: string) => string }) {
  const [all, setAll] = useState(false);
  const sorted = [...filters].sort((a, b) => (a.startMs ?? 0) - (b.startMs ?? 0));
  const index = Math.max(0, sorted.findIndex(filter => filter.filterId === selected));
  const start = all || sorted.length <= 12 ? 0 : Math.max(0, Math.min(sorted.length - 12, index - 4));
  const rows = all ? sorted : sorted.slice(start, start + 12);
  return <div className="tw flist" role="region" aria-label="Master filters table" tabIndex={0}><table className="t"><caption className="sr">Master filters</caption>
    <thead><tr><th scope="col">Filter</th><th scope="col">Time</th><th scope="col">Action</th><th scope="col" className="r">Conf.</th>
      <th scope="col" className="r">Votes</th><th scope="col" className="r">Reports</th></tr></thead>
    <tbody>{rows.map(filter => <tr key={filter.filterId} className={filter.filterId === selected ? "sel" : undefined}>
      <td><span className="fdesc"><i className={`dot ${filter.quarantined ? "qt" : categoryClass(filter.category)}`} aria-hidden="true" />
        <a href={href(filter.filterId)} aria-current={filter.filterId === selected ? "true" : undefined}>{filterName(filter)}</a>
        {!!filter.quarantined && <span className="chip mini">quarantined</span>}{filter.served === false && !filter.quarantined && <span className="chip mini dim">not served</span>}</span></td>
      <td className="num mono">{filterSpan(filter)}</td><td>{filter.action ?? "—"}</td>
      <td className="r num">{filter.confidence === null ? "—" : filter.confidence.toFixed(2)}</td>
      <td className="r"><Votes up={filter.upvotes ?? 0} down={filter.downvotes ?? 0} /></td>
      <td className="r">{filter.openReports ? <span className="chip bad">{filter.openReports} open</span> : <span className="rep0"><span aria-hidden="true">—</span><span className="sr">None</span></span>}</td>
    </tr>)}
      {sorted.length > rows.length || all ? <tr><td colSpan={6} className="muted small">
        {all ? `Showing all ${sorted.length} filters` : `Showing filters ${start + 1}–${start + rows.length} of ${sorted.length}${selected ? " around the selection" : ""}`}
        {" · "}<button type="button" className="linkish" onClick={() => setAll(!all)}>{all ? "Show fewer" : "Show all"}</button></td></tr> : null}
      {!sorted.length && <tr><td colSpan={6} className="muted">No master filters at this revision.</td></tr>}
    </tbody></table></div>;
}
