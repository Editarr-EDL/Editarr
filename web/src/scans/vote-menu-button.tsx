import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { MyVote, VoteValue } from "../api/api-community-master-types";

const choices: Array<{ value: VoteValue; label: string; glyph: string }> = [
  { value: "up", label: "Thumbs up", glyph: "👍" },
  { value: "down", label: "Thumbs down", glyph: "👎" },
  { value: "none", label: "No vote", glyph: "‒" },
];
const stateLabel = (vote: MyVote) => vote === "up" ? "Thumbs up" : vote === "down" ? "Thumbs down" : "none";
const menuGap = 6;

/** The pill's nearest scrolling ancestor, whose visible bounds it must stay in for the menu to stay open. */
function scrollingAncestor(element: Element): Element | null {
  for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
    const { overflowX, overflowY } = getComputedStyle(node);
    if (/(auto|scroll|hidden)/.test(`${overflowX} ${overflowY}`)) return node;
  }
  return null;
}
const viewportInset = 8;

/**
 * A compact vote control: a status pill showing the current vote ("Vote ▾" dashed when none, a green 👍 or
 * red 👎 tint otherwise) that opens a menu of 👍, 👎 and ‒ (no vote). The menu renders in a portal so a
 * scrolling table can't clip it, and follows the pill as the page scrolls; it closes on choosing, Escape, Tab and
 * outside clicks.
 * @param name Describes the filter, so each pill's accessible name is unique.
 * @param busy Shows the pill as saving and blocks it; it keeps focus so keyboard users stay in place.
 * @param unavailable Why voting isn't possible; the pill stays focusable so the reason can be read.
 * @param onChoose Called with a vote different from the current one.
 */
export function VoteMenuButton({ value, name, busy = false, unavailable = null, onChoose }: {
  value: MyVote; name: string; busy?: boolean; unavailable?: string | null; onChoose: (vote: VoteValue) => void;
}) {
  const [open, setOpenState] = useState(false);
  const [host, setHost] = useState<Element | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const reasonId = useId();
  const current: VoteValue = value ?? "none";

  // The menu renders inside the pill's landmark or dialog, so it stays in the page's regions and any focus trap.
  const setOpen = (next: boolean | ((was: boolean) => boolean)) => {
    setHost(trigger.current?.closest("dialog, [role='dialog'], main, [role='main']") ?? document.body);
    setOpenState(next);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return;
    /** Close once the pill can't be seen, keeping keyboard focus on the pill rather than losing it. */
    const dismiss = () => {
      setOpenState(false);
      if (menu.current?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
    };
    /** Keep the menu under the pill (or above it near the bottom); close it once the pill scrolls out of view. */
    const place = () => {
      const button = trigger.current;
      const popup = menu.current;
      if (!button || !popup) return;
      const anchor = button.getBoundingClientRect();
      const viewWidth = document.documentElement.clientWidth;
      const measured = anchor.width > 0 || anchor.height > 0;
      if (measured && (anchor.bottom <= 0 || anchor.top >= window.innerHeight || anchor.right <= 0 || anchor.left >= viewWidth)) {
        dismiss();
        return;
      }
      const { width, height } = popup.getBoundingClientRect();
      const below = anchor.bottom + menuGap;
      const above = anchor.top - menuGap - height;
      popup.style.top = `${below + height > window.innerHeight - viewportInset && above >= viewportInset ? above : below}px`;
      popup.style.left = `${Math.max(viewportInset, Math.min(anchor.right - width, viewWidth - width - viewportInset))}px`;
      popup.style.visibility = "visible";
    };
    place();
    const items = menu.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]');
    (menu.current?.querySelector<HTMLElement>('[aria-checked="true"]') ?? items?.[0])?.focus({ preventScroll: true });
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element;
      if (menu.current?.contains(target) || trigger.current?.contains(target)) return;
      // Clicking another control moves focus there; clicking elsewhere returns it to the pill.
      setOpen(false);
      if (!target.closest?.("button, a, input, select, textarea, [tabindex]")) trigger.current?.focus();
    };
    // A pill scrolled out of its table or list is hidden even while still inside the window.
    const observer = typeof IntersectionObserver === "undefined" || !trigger.current ? null
      : new IntersectionObserver((records) => { if (records.some((record) => !record.isIntersecting)) dismiss(); },
        { root: scrollingAncestor(trigger.current) });
    if (observer && trigger.current) observer.observe(trigger.current);
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      observer?.disconnect();
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => { if (busy || unavailable) setOpen(false); }, [busy, unavailable]);

  const choose = (vote: VoteValue) => {
    close();
    if (vote !== current) onChoose(vote);
  };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])];
    const index = items.indexOf(document.activeElement as HTMLElement);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (event.key === "Escape") { event.preventDefault(); close(); }
    // Tabbing continues from the pill, so focus doesn't jump from the portaled menu to the end of the page.
    else if (event.key === "Tab") close();
    else if (step) { event.preventDefault(); items[(index + step + items.length) % items.length]?.focus(); }
    else if (event.key === "Home" || event.key === "End") { event.preventDefault(); items[event.key === "Home" ? 0 : items.length - 1]?.focus(); }
  };
  const blocked = busy || !!unavailable;
  const toggle = () => { if (!blocked) setOpen((was) => !was); };

  return <>
    <button ref={trigger} type="button" className="vote-menu-pill" data-vote={current}
      aria-label={`Your vote: ${stateLabel(value)}. ${busy ? "Saving vote" : "Change vote"} for ${name}`}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
      aria-disabled={blocked || undefined} aria-describedby={unavailable ? reasonId : undefined}
      aria-busy={busy || undefined} title={unavailable ?? undefined}
      onClick={toggle}
      onKeyDown={(event) => {
        if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !open && !blocked) { event.preventDefault(); setOpen(true); }
      }}>
      {busy ? <span className="vote-menu-spinner" aria-hidden="true" /> : <span aria-hidden="true">{value === "up" ? "👍" : value === "down" ? "👎" : "Vote"}</span>}
      <span className="vote-menu-caret" aria-hidden="true">▾</span>
    </button>
    {unavailable && <span id={reasonId} className="visually-hidden">{unavailable}</span>}
    {open && createPortal(<div ref={menu} id={menuId} className="vote-menu" role="menu" aria-orientation="horizontal" aria-label={`Vote for ${name}`}
      onKeyDown={onMenuKeyDown}>
      {choices.map((choice) => <button key={choice.value} type="button" role="menuitemradio" tabIndex={-1}
        aria-checked={current === choice.value} aria-label={choice.label} title={choice.label}
        onClick={() => choose(choice.value)}><span aria-hidden="true">{choice.glyph}</span></button>)}
    </div>, host ?? document.body)}
  </>;
}
