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

export interface PopupBounds { top: number; bottom: number }

/** The vertical space a popup may use: the window, narrowed to the trigger's scroll container when it has one. */
export function popupBounds(trigger: Element): PopupBounds {
  const bounds = { top: viewportInset, bottom: window.innerHeight - viewportInset };
  const container = scrollingAncestor(trigger)?.getBoundingClientRect();
  if (!container || container.height <= 0) return bounds;
  return { top: Math.max(bounds.top, container.top), bottom: Math.min(bounds.bottom, container.bottom) };
}

/**
 * Place a popup of natural `height` below `anchor` when it fits, otherwise on the side with more room.
 * `maxHeight` is the smaller of `height` and that side's space, and an above placement is positioned by that
 * same value, so the popup scrolls internally instead of overflowing or overlapping its anchor.
 */
export function fitPopupVertically(anchor: { top: number; bottom: number }, height: number, bounds: PopupBounds) {
  const below = Math.max(0, bounds.bottom - anchor.bottom - popupGap);
  const above = Math.max(0, anchor.top - popupGap - bounds.top);
  // Never exceed the measured (CSS-capped) height, so the popup can't grow past where it was placed.
  if (height <= below || below >= above) return { top: anchor.bottom + popupGap, maxHeight: Math.floor(Math.min(height, below)), room: below };
  const maxHeight = Math.floor(Math.min(height, above));
  return { top: anchor.top - popupGap - maxHeight, maxHeight, room: above };
}

/** Below this much room on either side, a `fit` popup first scrolls its trigger up in its scroll container. */
const minimumFitRoom = 240;

/**
 * Keep an open, `position: fixed` popup (hidden until placed) under its trigger, right-aligned and flipped above
 * near the bottom of the window. It's hidden (`onHide`) once the trigger scrolls out of view, and `onOutside`
 * receives pointer presses outside both elements. With `fit`, the popup stays within the trigger's scroll
 * container as well, flips to the side with more room, and gets a `max-height` for that space; when neither
 * side has usable room, the container is scrolled once so the trigger sits near its top.
 */
export function useAnchoredPopup(open: boolean, trigger: RefObject<HTMLElement | null>, popup: RefObject<HTMLElement | null>,
  { onHide, onOutside, fit = false }: { onHide: () => void; onOutside: (target: Element) => void; fit?: boolean }) {
  const handlers = useRef({ onHide, onOutside });
  handlers.current = { onHide, onOutside };
  useLayoutEffect(() => {
    if (!open) return;
    let scrolledForRoom = false;
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
      // Re-measuring the natural height must not clamp the scroll position of the popup's own lists.
      const scrolled = fit ? [element, ...element.querySelectorAll("*")].filter((node) => node.scrollTop > 0)
        .map((node) => [node, node.scrollTop] as const) : [];
      if (fit) element.style.maxHeight = "";
      const { width, height } = element.getBoundingClientRect();
      if (fit) {
        const bounds = popupBounds(button);
        const placement = fitPopupVertically(anchor, height, bounds);
        const container = scrollingAncestor(button);
        if (!scrolledForRoom && container && placement.room < Math.min(height, minimumFitRoom)) {
          scrolledForRoom = true;
          const before = container.scrollTop;
          container.scrollTop += anchor.top - bounds.top - popupGap;
          if (container.scrollTop !== before) { place(); return; }
        }
        element.style.top = `${placement.top}px`;
        element.style.maxHeight = `${placement.maxHeight}px`;
        for (const [node, top] of scrolled) node.scrollTop = top;
      } else {
        const below = anchor.bottom + popupGap;
        const above = anchor.top - popupGap - height;
        element.style.top = `${below + height > window.innerHeight - viewportInset && above >= viewportInset ? above : below}px`;
      }
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
    // Scrolling inside the popup doesn't move its trigger.
    const onScroll = (event: Event) => { if (!(event.target instanceof Node && popup.current?.contains(event.target))) place(); };
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    return () => {
      observer?.disconnect();
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [open, trigger, popup, fit]);
}
