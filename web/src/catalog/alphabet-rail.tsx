import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import type { JellyfinMediaItem } from "../api/api-media-types";
import type { SortDirection } from "./catalog-types";
import { letterName, railLetters, titleLetter } from "./title-letters";

/**
 * A vertical A–Z rail (toolbar pattern, one tab stop; arrows, Home and End move between letters) that jumps
 * to the first title of a letter. Letters without titles are disabled, and the letter being read is marked
 * current.
 * @param offsets Index of each letter's first item in the listing.
 * @param pending The letter whose titles are still loading for a jump.
 */
export function AlphabetRail({ offsets, direction, current, pending, onJump }: {
  offsets: Record<string, number>;
  direction: SortDirection;
  current: string | null;
  pending: string | null;
  onJump: (letter: string) => void;
}) {
  const letters = direction === "asc" ? railLetters : [...railLetters].reverse();
  const available = letters.filter((letter) => offsets[letter] !== undefined);
  const [focusLetter, setFocusLetter] = useState<string | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const tabStop = [focusLetter, current, available[0]].find((letter) => letter && available.includes(letter)) ?? null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabStop ? available.indexOf(tabStop) : -1;
    const next = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: available.length - 1 }[event.key];
    if (next === undefined || !available.length) return;
    event.preventDefault();
    const letter = available[Math.max(0, Math.min(available.length - 1, next))];
    setFocusLetter(letter);
    buttons.current.get(letter)?.focus();
  };

  return <div className="alphabet-rail" role="toolbar" aria-label="Jump to letter" aria-orientation="vertical"
    aria-busy={pending !== null} onKeyDown={onKeyDown}>
    {letters.map((letter) => {
      const enabled = offsets[letter] !== undefined;
      return <button key={letter} type="button" ref={(node) => { if (node) buttons.current.set(letter, node); else buttons.current.delete(letter); }}
        className={letter === current ? "current" : undefined} aria-current={letter === current ? "true" : undefined}
        aria-label={`Jump to ${letterName(letter)}`} title={`Jump to ${letterName(letter)}`} disabled={!enabled}
        tabIndex={letter === tabStop ? 0 : -1} onFocus={() => setFocusLetter(letter)}
        onClick={() => onJump(letter)}>{letter}</button>;
    })}
  </div>;
}

/**
 * Track the letter of the first title visible below the sticky toolbar while the page scrolls.
 * @param container Element holding the cards or rows marked with `data-catalog-index`.
 * @param startIndex Listing index of `items[0]`.
 * @param topOffset Height covered by the sticky toolbar, in pixels.
 * @param anchorId The item last jumped to; while it is in the first visible row, its letter is current.
 */
export function useCurrentLetter(container: RefObject<HTMLElement | null>, items: JellyfinMediaItem[], startIndex: number,
  enabled: boolean, topOffset: number, anchorId: string | null): string | null {
  const [letter, setLetter] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) { setLetter(null); return; }
    let frame = 0;
    const update = () => {
      frame = 0;
      const entries = [...(container.current?.querySelectorAll<HTMLElement>("[data-catalog-index]") ?? [])];
      // Entries follow the page order, so the first one ending below the toolbar is found by bisection.
      let low = 0;
      let high = entries.length - 1;
      let found = -1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        if (entries[middle].getBoundingClientRect().bottom > topOffset) { found = middle; high = middle - 1; }
        else low = middle + 1;
      }
      const at = (index: number) => items[Number(entries[index].dataset.catalogIndex) - startIndex];
      // Past the last entry (scrolled into the footer), the last one is current.
      if (found < 0) found = entries.length - 1;
      let item = found >= 0 ? at(found) : items[0];
      if (found >= 0 && anchorId) {
        const rowTop = entries[found].getBoundingClientRect().top;
        for (let index = found; index < entries.length && Math.abs(entries[index].getBoundingClientRect().top - rowTop) < 2; index += 1) {
          if (at(index)?.id === anchorId) { item = at(index); break; }
        }
      }
      setLetter(item ? titleLetter(item) : null);
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
  }, [anchorId, container, enabled, items, startIndex, topOffset]);
  return letter;
}
