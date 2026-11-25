import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { popupHost, useAnchoredPopup } from "../ui/use-anchored-popup";
import type { MyVote, VoteValue } from "../api/api-community-master-types";

const choices: Array<{ value: VoteValue; label: string; glyph: string }> = [
  { value: "up", label: "Thumbs up", glyph: "👍" },
  { value: "down", label: "Thumbs down", glyph: "👎" },
  { value: "none", label: "No vote", glyph: "‒" },
];
const stateLabel = (vote: MyVote) => vote === "up" ? "Thumbs up" : vote === "down" ? "Thumbs down" : "none";

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
    setHost(popupHost(trigger.current));
    setOpenState(next);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  useAnchoredPopup(open, trigger, menu, {
    // Close once the pill can't be seen, keeping keyboard focus on the pill rather than losing it.
    onHide: () => {
      setOpenState(false);
      if (menu.current?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
    },
    onOutside: (target) => {
      // Clicking another control moves focus there; clicking elsewhere returns it to the pill.
      setOpen(false);
      if (!target.closest?.("button, a, input, select, textarea, [tabindex]")) trigger.current?.focus();
    },
  });
  useLayoutEffect(() => {
    if (!open) return;
    const items = menu.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]');
    (menu.current?.querySelector<HTMLElement>('[aria-checked="true"]') ?? items?.[0])?.focus({ preventScroll: true });
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
