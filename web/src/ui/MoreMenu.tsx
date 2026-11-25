import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { popupHost, useAnchoredPopup } from "./use-anchored-popup";

export interface MoreMenuItem {
  key: string;
  label: string;
  /** A fuller accessible name, when the visible label alone isn't unique on the page. */
  accessibleName?: string;
  danger?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

/**
 * A "⋯" button opening a menu of secondary actions (menu button pattern). The menu renders in a portal beside
 * the button, moves focus to its first enabled item, supports arrow keys, Home and End, and closes on choosing,
 * Escape, Tab or an outside click, returning focus to the button.
 * @param label The button's and menu's accessible name, e.g. "More actions for Fight at 1:00".
 */
export function MoreMenu({ label, items, disabled = false }: { label: string; items: MoreMenuItem[]; disabled?: boolean }) {
  const [open, setOpenState] = useState(false);
  const [host, setHost] = useState<Element | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const setOpen = (next: boolean) => {
    setHost(popupHost(trigger.current));
    setOpenState(next);
  };
  const close = () => { setOpen(false); trigger.current?.focus(); };
  const enabledItems = () => [...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])];

  useAnchoredPopup(open, trigger, menu, {
    onHide: () => {
      setOpenState(false);
      if (menu.current?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
    },
    onOutside: (target) => {
      setOpen(false);
      if (!target.closest?.("button, a, input, select, textarea, [tabindex]")) trigger.current?.focus();
    },
  });
  // With every item unavailable the menu itself takes focus, so Escape and Tab still close it.
  useLayoutEffect(() => {
    if (open) (enabledItems()[0] ?? menu.current)?.focus({ preventScroll: true });
  }, [open]);

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const list = enabledItems();
    const index = list.indexOf(document.activeElement as HTMLElement);
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "Tab") close();
    else if (step) { event.preventDefault(); list[(index + step + list.length) % list.length]?.focus(); }
    else if (event.key === "Home" || event.key === "End") { event.preventDefault(); list[event.key === "Home" ? 0 : list.length - 1]?.focus(); }
  };

  return <>
    <button ref={trigger} type="button" className="icon-button more-menu-button" aria-label={label} title={label}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} disabled={disabled}
      onClick={() => setOpen(!open)}
      onKeyDown={(event) => {
        if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !open) { event.preventDefault(); setOpen(true); }
        else if (event.key === "Escape" && open) { event.preventDefault(); close(); }
      }}>
      <span aria-hidden="true">⋯</span>
    </button>
    {open && createPortal(<div ref={menu} id={menuId} className="more-menu" role="menu" aria-label={label} tabIndex={-1} onKeyDown={onMenuKeyDown}>
      {items.map((item) => <button key={item.key} type="button" role="menuitem" tabIndex={-1}
        className={item.danger ? "more-menu-danger" : undefined} disabled={item.disabled}
        aria-label={item.accessibleName} onClick={() => { close(); item.onSelect(); }}>{item.label}</button>)}
    </div>, host ?? document.body)}
  </>;
}
