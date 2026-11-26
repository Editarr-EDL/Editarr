import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { handleMenuKeyDown } from "./menu-keyboard";
import { popupHost, useAnchoredPopup } from "./use-anchored-popup";

export interface ChoiceMenuGroup {
  label: string;
  options: Array<{ key: string; label: string; checked: boolean; onSelect: () => void }>;
}

/**
 * A compact menu button whose menu holds groups of single choices (`menuitemradio`), such as a sort key and
 * its direction. Opening focuses the checked choice; arrows, Home and End move, Enter or Space chooses and
 * closes, and Escape, Tab or an outside click close, returning focus to the button.
 * @param label Accessible name of the button and menu; it should include the visible `children` text.
 */
export function ChoiceMenu({ label, groups, children, className = "" }: {
  label: string;
  groups: ChoiceMenuGroup[];
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpenState] = useState(false);
  const [host, setHost] = useState<Element | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const setOpen = (next: boolean) => { setHost(popupHost(trigger.current)); setOpenState(next); };
  const close = () => { setOpen(false); trigger.current?.focus(); };
  const choices = () => [...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])];

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
  useLayoutEffect(() => {
    if (!open) return;
    const items = choices();
    (items.find((item) => item.getAttribute("aria-checked") === "true") ?? items[0])?.focus({ preventScroll: true });
  }, [open]);

  return <>
    <button ref={trigger} type="button" className={`choice-menu-button ${className}`.trim()} aria-label={label} title={label}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
      onClick={() => setOpen(!open)}
      onKeyDown={(event) => {
        if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !open) { event.preventDefault(); setOpen(true); }
      }}>
      {children}<span className="choice-menu-caret" aria-hidden="true">▾</span>
    </button>
    {open && createPortal(<div ref={menu} id={menuId} className="more-menu choice-menu" role="menu" aria-label={label} tabIndex={-1}
      onKeyDown={(event) => handleMenuKeyDown(event, choices(), close)}>
      {groups.map((group) => <div key={group.label} role="group" aria-label={group.label} className="choice-menu-group">
        <div className="choice-menu-heading" aria-hidden="true">{group.label}</div>
        {group.options.map((option) => <button key={option.key} type="button" role="menuitemradio" tabIndex={-1}
          aria-checked={option.checked} onClick={() => { close(); option.onSelect(); }}>
          <span className="choice-menu-check" aria-hidden="true">{option.checked ? "✓" : ""}</span>{option.label}
        </button>)}
      </div>)}
    </div>, host ?? document.body)}
  </>;
}
