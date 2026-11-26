import { useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { primaryImageUrl } from "../api/api-service";
import type { JellyfinMediaItem } from "../api/api-media-types";
import { exportProvenance, normalizedExportStatus } from "../review/export-status";
import { applicationUrl } from "../shared/runtime-urls";

/** "Episode 3 · 2025 · PG-13": the details shown under a poster. */
export function posterMeta(item: JellyfinMediaItem): string {
  return [
    item.item_type === "Episode" && item.index_number != null ? `Episode ${item.index_number}` : null,
    item.year ? String(item.year) : null,
    item.official_rating,
  ].filter(Boolean).join(" · ") || item.item_type;
}

/** Height of a card's title and details below its poster: two fixed lines and their gaps (see library.css). */
const cardTextHeight = 48;

/**
 * Responsive poster grid; `size` is the narrowest column width in pixels. Cards skip rendering off screen
 * (`content-visibility: auto`), so the grid sets their exact height, `--card-block-size`, from its column
 * width; scroll positions and jumps then stay accurate before those cards render.
 */
export function PosterGrid({ size, wide = false, startIndex = 0, children, label }: {
  size: number;
  wide?: boolean;
  /** Listing index of the first card; cards keep the column they have in the full listing. */
  startIndex?: number;
  children: ReactNode;
  label: string;
}) {
  const grid = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(1);
  useLayoutEffect(() => {
    const element = grid.current;
    if (!element) return;
    const measure = () => {
      const tracks = getComputedStyle(element).gridTemplateColumns;
      const column = parseFloat(tracks);
      setColumns(Math.max(1, tracks.split(" ").filter(Boolean).length));
      if (column > 0) element.style.setProperty("--card-block-size", `${column * (wide ? 9 / 16 : 3 / 2) + cardTextHeight}px`);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [size, wide]);
  return <div ref={grid} className={`poster-grid catalog-poster-grid${wide ? " wide-posters" : ""}`} role="list" aria-label={label}
    style={{ "--poster-size": `${wide ? Math.round(size * 1.6) : size}px` } as CSSProperties}>
    {/* Empty cells before a window that starts part-way, so loading earlier titles never reflows the columns. */}
    {startIndex % columns > 0 && <div className="poster-grid-offset" role="presentation" aria-hidden="true" style={{ gridColumn: `span ${startIndex % columns}` }} />}
    {children}
  </div>;
}

/**
 * A poster card opening one item. The EDL status shows top-left (a colored dot on phones), the resolution
 * top-right and a series label bottom-right; the title and "year · rating" sit below.
 * @param index Position in the listing, used to scroll to the card.
 * @param wide Use a landscape frame, as for episode stills.
 */
export function MediaCard({ item, onOpen, index, wide = false }: { item: JellyfinMediaItem; onOpen: () => void; index?: number; wide?: boolean }) {
  const id = useId();
  const imageTag = typeof item.image_tags.Primary === "string" ? item.image_tags.Primary : undefined;
  const imageUrl = item.image_url ? applicationUrl(item.image_url) : (imageTag ? primaryImageUrl(item.id, imageTag) : null);
  const edl = normalizedExportStatus(item).exports.edl;
  const edlState = edl.outdated ? "Outdated" : "Current";
  const described = [edl.present && `${id}-edl`, item.resolution && `${id}-res`, item.item_type === "Series" && `${id}-type`, `${id}-meta`].filter(Boolean).join(" ");
  return <div role="listitem" className="media-card-item">
    <button type="button" className="media-card" onClick={onOpen} aria-label={`Open ${item.name}`} aria-describedby={described}
      data-catalog-index={index}>
      <span className={`poster-frame${wide ? " wide" : ""}`}>
        {imageUrl ? <img src={imageUrl} alt="" loading="lazy" /> : <span className="poster-fallback" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span>}
        {edl.present && <span id={`${id}-edl`} className={`poster-badge poster-edl ${edlState.toLowerCase()}`}
          title={`EDL ${edlState.toLowerCase()}${edl.exported_at ? ` · ${new Date(edl.exported_at).toLocaleString()}` : ""} · ${exportProvenance(edl.exported_scans)}`}>
          <span className="poster-edl-text">EDL · {edlState}</span>
        </span>}
        {item.resolution && <span id={`${id}-res`} className="poster-badge poster-resolution">{item.resolution}</span>}
        {item.item_type === "Series" && <span id={`${id}-type`} className="poster-badge poster-type">Series</span>}
      </span>
      <span className="media-title">{item.name}</span>
      <span id={`${id}-meta`} className="media-meta">{posterMeta(item)}</span>
    </button>
  </div>;
}
