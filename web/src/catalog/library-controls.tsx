import { useEffect, useLayoutEffect, useState, type CSSProperties, type RefObject } from "react";
import { readUserValue, writeUserValue } from "../shared/user-storage";
import { ChoiceMenu } from "../ui/ChoiceMenu";
import type { LibraryView, SortDirection } from "./catalog-types";

const posterSizeKey = "library-poster-size";
export const posterSizeRange = { min: 120, max: 260, initial: 170 };

/** The poster width chosen with the size slider, shared by every library, series and season page. */
export function usePosterSize(): [number, (size: number) => void] {
  const [size, setSize] = useState(() => {
    const saved = Number(readUserValue(posterSizeKey));
    return saved >= posterSizeRange.min && saved <= posterSizeRange.max ? saved : posterSizeRange.initial;
  });
  useEffect(() => { writeUserValue(posterSizeKey, String(size)); }, [size]);
  return [size, setSize];
}

/**
 * Measure a sticky toolbar so content below can sit under it, and how far it reaches under the fixed scans
 * control, which it must leave clear once stuck to the top. Returns the height and the page style setting
 * `--catalog-sticky-offset` and `--catalog-toolbar-clearance`.
 */
export function useStickyToolbar(toolbar: RefObject<HTMLElement | null>): { height: number; style: CSSProperties } {
  const [size, setSize] = useState({ height: 0, clearance: 0 });
  useLayoutEffect(() => {
    const element = toolbar.current;
    if (!element) return;
    const measure = () => {
      const bounds = element.getBoundingClientRect();
      const control = document.querySelector(".scans-floating-control")?.getBoundingClientRect();
      const clearance = control && control.width ? Math.max(0, Math.ceil(bounds.right - control.left + 8)) : 0;
      setSize((previous) => previous.height === Math.ceil(bounds.height) && previous.clearance === clearance
        ? previous : { height: Math.ceil(bounds.height), clearance });
    };
    measure();
    // The scans control can appear after the page; scrolling re-measures before the toolbar sticks.
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); window.removeEventListener("scroll", measure); };
  }, [toolbar]);
  return { height: size.height, style: {
    "--catalog-sticky-offset": `${size.height}px`, "--catalog-toolbar-clearance": `${size.clearance}px`,
  } as CSSProperties };
}

/** "⟳" button reloading a catalog listing. */
export function RefreshButton({ label, busy, onRefresh }: { label: string; busy: boolean; onRefresh: () => void }) {
  return <button type="button" className="icon-button catalog-icon-button catalog-refresh" aria-label={label} title={label}
    disabled={busy} aria-busy={busy} onClick={onRefresh}><span aria-hidden="true">⟳</span></button>;
}

/** Poster and list view buttons. */
export function ViewToggle({ view, onChange }: { view: LibraryView; onChange: (view: LibraryView) => void }) {
  return <div className="catalog-view-toggle" role="group" aria-label="View">
    <button type="button" aria-label="Posters" title="Posters" aria-pressed={view === "icon"} onClick={() => onChange("icon")}><span aria-hidden="true">▦</span></button>
    <button type="button" aria-label="List" title="List" aria-pressed={view === "list"} onClick={() => onChange("list")}><span aria-hidden="true">☰</span></button>
  </div>;
}

/** Poster width slider; hidden on phones, where the grid uses a fixed size. */
export function PosterSizeControl({ size, onChange }: { size: number; onChange: (size: number) => void }) {
  return <label className="poster-size-control"><span>Poster size</span>
    <input type="range" min={posterSizeRange.min} max={posterSizeRange.max} step={10} value={size}
      onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

/**
 * One "Title ↑ ▾" menu button choosing a sort key and direction. Choosing a different key sorts it ascending.
 * @param options Sort keys with their visible labels.
 */
export function SortMenu<Key extends string>({ options, sort, direction, onChange }: {
  options: Array<{ key: Key; label: string }>;
  sort: Key;
  direction: SortDirection;
  onChange: (sort: Key, direction: SortDirection) => void;
}) {
  const current = options.find((option) => option.key === sort)?.label ?? sort;
  return <ChoiceMenu className="catalog-sort-menu" label={`Sort by ${current}, ${direction === "asc" ? "ascending" : "descending"}`} groups={[
    { label: "Sort by", options: options.map((option) => ({
      key: option.key, label: option.label, checked: option.key === sort,
      onSelect: () => onChange(option.key, option.key === sort ? direction : "asc"),
    })) },
    { label: "Order", options: (["asc", "desc"] as const).map((value) => ({
      key: value, label: value === "asc" ? "Ascending" : "Descending", checked: value === direction,
      onSelect: () => onChange(sort, value),
    })) },
  ]}>{current} <span aria-hidden="true">{direction === "asc" ? "↑" : "↓"}</span></ChoiceMenu>;
}
