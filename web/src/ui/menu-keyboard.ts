import type { KeyboardEvent } from "react";

/**
 * Keyboard handling shared by menu popups: ArrowUp/ArrowDown wrap through `items`, Home and End jump to the
 * ends, Escape closes (returning focus is up to `close`), and Tab closes while letting focus move on.
 */
export function handleMenuKeyDown(event: KeyboardEvent<HTMLElement>, items: HTMLElement[], close: () => void): void {
  const index = items.indexOf(document.activeElement as HTMLElement);
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (event.key === "Escape") { event.preventDefault(); close(); }
  else if (event.key === "Tab") close();
  else if (step) { event.preventDefault(); items[(index + step + items.length) % items.length]?.focus(); }
  else if (event.key === "Home" || event.key === "End") { event.preventDefault(); items[event.key === "Home" ? 0 : items.length - 1]?.focus(); }
}
