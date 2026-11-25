import { useLayoutEffect, useRef, type RefObject } from "react";

const popupGap = 6;
const viewportInset = 8;

/**
 * The trigger's nearest user-scrollable ancestor, whose visible bounds it must stay in for its popup to stay open.
 * Ancestors that only clip (`hidden`, `clip`) are skipped, so a rounded wrapper can't hide the list that scrolls.
 */
export function scrollingAncestor(element: Element): Element | null {
  for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
    const { overflowX, overflowY } = getComputedStyle(node);
    if (/(auto|scroll)/.test(`${overflowX} ${overflowY}`)) return node;
  }
  return null;
}

/**
 * Where a portaled popup renders: inside its trigger's dialog or main landmark, so it stays in the page's
 * regions and any focus trap.
 */
export function popupHost(trigger: Element | null): Element {
  return trigger?.closest("dialog, [role='dialog'], main, [role='main']") ?? document.body;
}

/**
 * Keep an open, `position: fixed` popup (hidden until placed) under its trigger, right-aligned and flipped above
 * near the bottom of the window. It's hidden (`onHide`) once the trigger scrolls out of view, and `onOutside`
 * receives pointer presses outside both elements.
 */
export function useAnchoredPopup(open: boolean, trigger: RefObject<HTMLElement | null>, popup: RefObject<HTMLElement | null>,
  { onHide, onOutside }: { onHide: () => void; onOutside: (target: Element) => void }) {
  const handlers = useRef({ onHide, onOutside });
  handlers.current = { onHide, onOutside };
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const button = trigger.current;
      const element = popup.current;
      if (!button || !element) return;
      const anchor = button.getBoundingClientRect();
      const viewWidth = document.documentElement.clientWidth;
      const measured = anchor.width > 0 || anchor.height > 0;
      if (measured && (anchor.bottom <= 0 || anchor.top >= window.innerHeight || anchor.right <= 0 || anchor.left >= viewWidth)) {
        handlers.current.onHide();
        return;
      }
      const { width, height } = element.getBoundingClientRect();
      const below = anchor.bottom + popupGap;
      const above = anchor.top - popupGap - height;
      element.style.top = `${below + height > window.innerHeight - viewportInset && above >= viewportInset ? above : below}px`;
      element.style.left = `${Math.max(viewportInset, Math.min(anchor.right - width, viewWidth - width - viewportInset))}px`;
      element.style.visibility = "visible";
    };
    place();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element;
      if (popup.current?.contains(target) || trigger.current?.contains(target)) return;
      handlers.current.onOutside(target);
    };
    // A trigger scrolled out of its table or list is hidden even while still inside the window.
    const observer = typeof IntersectionObserver === "undefined" || !trigger.current ? null
      : new IntersectionObserver((records) => { if (records.some((record) => !record.isIntersecting)) handlers.current.onHide(); },
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
  }, [open, trigger, popup]);
}
