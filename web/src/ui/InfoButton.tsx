import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

const viewportInset = 12;
const anchorGap = 5;

export interface InfoButtonHost {
  panelRef: RefObject<HTMLDivElement | null>;
  activeDismiss: RefObject<(() => void) | null>;
}

/**
 * Show details outside scrollable content on hover, focus, or tap. `content` replaces the default "i"
 * glyph and `className` the default button style, so labels and icons can carry the same tooltip.
 */
export function InfoButton({ label, host, children, content = "i", className = "scan-options-button" }: {
  label: string;
  host?: InfoButtonHost;
  children: ReactNode;
  content?: ReactNode;
  className?: string;
}) {
  const localDismiss = useRef<(() => void) | null>(null);
  const activeDismiss = host?.activeDismiss ?? localDismiss;
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const tooltip = useRef<HTMLSpanElement>(null);
  const pointerInTooltip = useRef(false);
  const hideTimer = useRef<number | undefined>(undefined);
  const [mode, setMode] = useState<"preview" | "pinned" | null>(null);
  const open = mode !== null;

  const cancelHide = useCallback(() => window.clearTimeout(hideTimer.current), []);
  const close = useCallback(function dismiss() {
    cancelHide();
    pointerInTooltip.current = false;
    if (activeDismiss.current === dismiss) activeDismiss.current = null;
    setMode(null);
  }, [activeDismiss, cancelHide]);

  useEffect(() => () => {
    cancelHide();
    if (activeDismiss.current === close) activeDismiss.current = null;
  }, [activeDismiss, cancelHide, close]);

  useLayoutEffect(() => {
    const button = trigger.current;
    const popup = tooltip.current;
    if (!open || !button || !popup) return;

    // In the Review Sources popover only the popover clips the button; it may extend past the filter list.
    const sources = button.closest(".review-sources-popover");
    const clips = sources ? [sources] : [button.closest(".scans-floating-table-wrap"), button.closest(".scans-floating-popover"), button.closest(".review-filter-list")];
    const position = () => {
      const anchor = button.getBoundingClientRect();
      const width = document.documentElement.clientWidth;
      const height = window.innerHeight;
      if (anchor.right <= 0 || anchor.left >= width || anchor.bottom <= 0 || anchor.top >= height
        || clips.some((clip) => {
          if (!clip) return false;
          const bounds = clip.getBoundingClientRect();
          return anchor.bottom <= bounds.top || anchor.top >= bounds.bottom
            || anchor.right <= bounds.left || anchor.left >= bounds.right;
        })) {
        close();
        return;
      }

      const desiredHeight = popup.scrollHeight + popup.offsetHeight - popup.clientHeight;
      const below = height - viewportInset - anchor.bottom - anchorGap;
      const above = anchor.top - viewportInset - anchorGap;
      const placeBelow = desiredHeight <= below || below >= above;
      const availableHeight = Math.max(0, placeBelow ? below : above);
      const left = Math.max(viewportInset, Math.min(anchor.left, width - popup.offsetWidth - viewportInset));
      const top = placeBelow ? anchor.bottom + anchorGap
        : anchor.top - anchorGap - Math.min(desiredHeight, availableHeight);
      popup.style.left = `${left}px`;
      popup.style.top = `${top}px`;
      popup.style.maxHeight = `${availableHeight}px`;
      popup.style.visibility = "visible";
    };

    position();
    window.addEventListener("scroll", position, true);
    window.addEventListener("resize", position);
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(position);
    observer?.observe(popup);
    const table = button.closest("table");
    if (table) observer?.observe(table);
    return () => {
      window.removeEventListener("scroll", position, true);
      window.removeEventListener("resize", position);
      observer?.disconnect();
    };
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    const dismissOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!trigger.current?.contains(target) && !tooltip.current?.contains(target)) close();
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || activeDismiss.current !== close) return;
      event.preventDefault();
      close();
    };
    document.addEventListener("pointerdown", dismissOutside);
    document.addEventListener("keydown", dismissOnEscape, true);
    return () => {
      document.removeEventListener("pointerdown", dismissOutside);
      document.removeEventListener("keydown", dismissOnEscape, true);
    };
  }, [open, activeDismiss, close]);

  const reveal = () => {
    cancelHide();
    if (activeDismiss.current !== close) {
      activeDismiss.current?.();
      activeDismiss.current = close;
    }
  };
  const preview = () => {
    reveal();
    setMode((current) => current ?? "preview");
  };
  const hidePreview = () => {
    cancelHide();
    if (mode !== "preview") return;
    hideTimer.current = window.setTimeout(() => {
      if (!trigger.current?.matches(":focus-visible")) close();
    }, 150);
  };

  return <>
    <button
      ref={trigger}
      className={className}
      type="button"
      aria-label={label}
      aria-expanded={open}
      aria-describedby={open ? id : undefined}
      onMouseEnter={preview}
      onMouseLeave={hidePreview}
      onClick={() => {
        if (mode === "pinned") close();
        else { reveal(); setMode("pinned"); }
      }}
      onFocus={preview}
      onBlur={() => { if (!pointerInTooltip.current) close(); }}
    >{content}</button>
    {open && createPortal(
      <span
        ref={tooltip}
        className="scan-options-tooltip"
        id={id}
        role="tooltip"
        onMouseEnter={() => { pointerInTooltip.current = true; cancelHide(); }}
        onMouseLeave={() => { pointerInTooltip.current = false; hidePreview(); }}
      >{children}</span>,
      host?.panelRef.current ?? document.body,
    )}
  </>;
}
