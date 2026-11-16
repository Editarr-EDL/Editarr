import { useEffect, useRef, type ReactNode, type RefObject } from "react";

const modalFocusableSelector = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

export function ModalDialog({
  labelledBy,
  className = "",
  onClose,
  returnFocusRef,
  children,
}: {
  labelledBy: string;
  className?: string;
  onClose?: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      const dialog = dialogRef.current;
      const modals = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      if (event.key !== "Escape" || !onClose || !dialog || dialog.contains(document.activeElement)
        || modals[modals.length - 1] !== dialog) return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  useEffect(() => {
    const previouslyFocused = returnFocusRef?.current || (
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    );
    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      // A late frame must not pull back focus that Tab already moved into the dialog.
      if (!dialog || dialog.contains(document.activeElement)) return;
      (dialog.querySelector<HTMLElement>(modalFocusableSelector) || dialog).focus();
    });
    return () => {
      window.cancelAnimationFrame(frame);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [returnFocusRef]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape" && event.key !== "Tab") return;
    // Nested dialogs share this keydown as it bubbles, so only the innermost one handles it.
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape") {
      onClose?.();
      return;
    }
    const modals = document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]');
    const topmost = modals[modals.length - 1];
    if (topmost && topmost !== dialogRef.current) {
      (topmost.querySelector<HTMLElement>(modalFocusableSelector) || topmost).focus();
      return;
    }
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(modalFocusableSelector) || [],
    );
    if (!focusable.length) {
      dialogRef.current?.focus();
      return;
    }
    const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const nextIndex = event.shiftKey
      ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
      : (currentIndex < 0 || currentIndex === focusable.length - 1 ? 0 : currentIndex + 1);
    focusable[nextIndex].focus();
  };

  return <div className="modal-backdrop" role="presentation">
    <section
      className={`scan-popover ${className}`.trim()}
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      {children}
    </section>
  </div>;
}

