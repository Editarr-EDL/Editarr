import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import type { JellyfinMediaItem, LibraryJumpGroup } from "../api/api-media-types";
import type { SortDirection, SortKey } from "./catalog-types";
import { railEntries, railName } from "./jump-groups";

/**
 * A vertical rail (toolbar pattern, one tab stop; arrows, Home and End move between entries) that jumps to the
 * first title of a group of the current sort: a letter, decade, rating, length band or type. Under title
 * sorting, letters without titles are disabled. The group being read is marked current.
 * @param groups The server's groups for the listing, in list order.
 * @param current Key of the group in view.
 * @param pending Key of the group whose titles are still loading for a jump.
 */
export function JumpRail({ sort, groups, direction, current, pending, onJump }: {
  sort: SortKey;
  groups: LibraryJumpGroup[];
  direction: SortDirection;
  current: string | null;
  pending: string | null;
  onJump: (group: LibraryJumpGroup) => void;
}) {
  const entries = railEntries(sort, groups, direction);
  const available = entries.filter((entry) => entry.group).map((entry) => entry.key);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const tabStop = [focusKey, current, available[0]].find((key) => key !== null && key !== undefined && available.includes(key)) ?? null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabStop !== null ? available.indexOf(tabStop) : -1;
    const next = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: available.length - 1 }[event.key];
    if (next === undefined || !available.length) return;
    event.preventDefault();
    const key = available[Math.max(0, Math.min(available.length - 1, next))];
    setFocusKey(key);
    buttons.current.get(key)?.focus();
  };

  return <div className="jump-rail" role="toolbar" aria-label={railName(sort)} aria-orientation="vertical"
    aria-busy={pending !== null} onKeyDown={onKeyDown}>
    {entries.map(({ key, text, name, group }) => {
      const isCurrent = key === current;
      return <button key={key} type="button" ref={(node) => { if (node) buttons.current.set(key, node); else buttons.current.delete(key); }}
        className={isCurrent ? "current" : undefined} aria-current={isCurrent ? "true" : undefined}
        aria-label={`Jump to ${name}`} title={`Jump to ${name}`} disabled={!group}
        tabIndex={key === tabStop ? 0 : -1} onFocus={() => setFocusKey(key)}
        onClick={() => { if (group) onJump(group); }}>{text}</button>;
    })}
  </div>;
}

/** The item a jump scrolled to, with the window's scroll position right after the jump. */
export interface JumpAnchor {
  id: string;
  scrollY: number;
}

/**
 * Track the listing index of the first title visible below the sticky toolbar while the page scrolls.
 * @param container Element holding the cards or rows marked with `data-catalog-index`.
 * @param startIndex Listing index of `items[0]`.
 * @param topOffset Height covered by the sticky toolbar, in pixels.
 * @param anchor The item last jumped to and the page's scroll position right after that jump (a new object per
 *   jump). Its index is reported while it is in the first visible row, or while it is in view and the page hasn't
 *   scrolled since the jump (as when the page ends before it reaches the top).
 */
export function useVisibleIndex(container: RefObject<HTMLElement | null>, items: JellyfinMediaItem[], startIndex: number,
  enabled: boolean, topOffset: number, anchor: JumpAnchor | null): number | null {
  const [visible, setVisible] = useState<number | null>(null);
  useEffect(() => {
    if (!enabled) { setVisible(null); return; }
    let frame = 0;
    const update = () => {
      frame = 0;
      const entries = [...(container.current?.querySelectorAll<HTMLElement>("[data-catalog-index]") ?? [])];
      if (!entries.length) { setVisible(null); return; }
      // Entries follow the page order, so the first one ending below the toolbar is found by bisection.
      let low = 0;
      let high = entries.length - 1;
      let found = -1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        if (entries[middle].getBoundingClientRect().bottom > topOffset) { found = middle; high = middle - 1; }
        else low = middle + 1;
      }
      // Past the last entry (scrolled into the footer), the last one is current.
      if (found < 0) found = entries.length - 1;
      const indexOf = (position: number) => Number(entries[position].dataset.catalogIndex);
      let index = indexOf(found);
      const anchorPosition = anchor ? items.findIndex((item) => item.id === anchor.id) : -1;
      if (anchor && anchorPosition >= 0) {
        const anchorIndex = startIndex + anchorPosition;
        const bounds = container.current?.querySelector<HTMLElement>(`[data-catalog-index="${anchorIndex}"]`)?.getBoundingClientRect();
        const rowTop = entries[found].getBoundingClientRect().top;
        const inFirstRow = bounds && Math.abs(bounds.top - rowTop) < 2;
        const unscrolledInView = bounds && Math.abs(window.scrollY - anchor.scrollY) < 2
          && bounds.bottom > topOffset && bounds.top < window.innerHeight;
        if (inFirstRow || unscrolledInView) index = anchorIndex;
      }
      setVisible(index);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [anchor, container, enabled, items, startIndex, topOffset]);
  return visible;
}
