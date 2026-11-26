import type { JellyfinMediaItem } from "../api/api-media-types";
import { ExportBadges, normalizedExportStatus } from "../review/export-status";
import type { SortDirection, SortKey } from "./catalog-types";
import { formatRating, MediaDuration } from "./media-display";

/** A column header that sorts by its column, showing the active direction. */
export function SortableHeader({ label, active, direction, onClick }: { label: string; active: boolean; direction: SortDirection; onClick: () => void }) {
  return <th aria-sort={active ? direction === "asc" ? "ascending" : "descending" : "none"}><button className="sort-button" onClick={onClick}>{label}{active && <span aria-hidden="true"> {direction === "asc" ? "↑" : "↓"}</span>}</button></th>;
}

/** The library listing as a sortable table; each row's title opens its item. */
export function LibraryTable({ items, startIndex, sort, direction, onSort, onOpen }: {
  items: JellyfinMediaItem[];
  /** Listing index of `items[0]`. */
  startIndex: number;
  sort: SortKey;
  direction: SortDirection;
  onSort: (sort: SortKey) => void;
  onOpen: (item: JellyfinMediaItem) => void;
}) {
  const header = (label: string, key: SortKey) => <SortableHeader label={label} active={sort === key} direction={direction} onClick={() => onSort(key)} />;
  return <div className="library-table-wrap"><table className="library-table">
    <thead><tr>{header("Title", "title")}{header("Year", "year")}<th>Resolution</th>{header("Length", "duration")}{header("Rating", "rating")}{header("Type", "type")}<th>Exports</th></tr></thead>
    <tbody>{items.map((item, index) => <tr key={item.id}>
      <td><button className="item-link" data-catalog-index={startIndex + index} onClick={() => onOpen(item)}>{item.name}</button></td>
      <td>{item.year || "—"}</td><td>{item.resolution || "—"}</td><td><MediaDuration seconds={item.duration_seconds} /></td>
      <td>{formatRating(item)}</td><td>{item.item_type}</td><td><ExportBadges status={normalizedExportStatus(item)} /></td>
    </tr>)}</tbody>
  </table></div>;
}
