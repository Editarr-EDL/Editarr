import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "../styles/model-picker.css";
import { popupHost, useAnchoredPopup } from "../ui/use-anchored-popup";
import { ModelBadges } from "./model-badges";
import type { ModelOption } from "./model-catalog";
import { ModelPickerList, revealActiveOption } from "./model-picker-list";
import { statusView } from "./model-status";

const bottomSheetQuery = "(max-width: 639px)";
/** Narrow windows show the list as a CSS bottom sheet rather than anchored to the card. */
const isBottomSheet = () => typeof window.matchMedia === "function" && window.matchMedia(bottomSheetQuery).matches;

/**
 * A model choice shown as a summary card whose "Change" button opens a grouped listbox of `options`
 * (see `buildModelOptions`). The button's accessible name is "<label>: <selected name>"; choosing an
 * available row calls `onChange` and returns focus to the button. On narrow screens the list is a bottom sheet.
 * @param info Optional help (for example an `InfoButton`) shown beside the visible label.
 */
export function ModelPicker({ label, options, value, onChange, disabled = false, info, className = "" }: {
  label: string;
  options: ModelOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  info?: ReactNode;
  className?: string;
}) {
  const labelId = useId();
  const summaryId = useId();
  const listboxId = useId();
  const card = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpenState] = useState(false);
  const [host, setHost] = useState<Element | null>(null);
  const selected = options.find((option) => option.id === value);
  const inactive = disabled || options.length === 0;
  const isOpen = open && !inactive;

  const setOpen = (next: boolean) => { setHost(popupHost(card.current)); setOpenState(next); };
  const close = () => { setOpenState(false); trigger.current?.focus({ preventScroll: true }); };

  // Match the popup to the card before useAnchoredPopup places it; CSS keeps a readable minimum width.
  useLayoutEffect(() => {
    if (isOpen && popup.current && card.current) popup.current.style.width = `${card.current.getBoundingClientRect().width}px`;
  }, [isOpen]);
  useAnchoredPopup(isOpen, card, popup, {
    fit: !isBottomSheet(),
    onHide: () => {
      setOpenState(false);
      if (popup.current?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
    },
    onOutside: (target) => {
      setOpenState(false);
      if (!target.closest?.("button, a, input, select, textarea, [tabindex]")) trigger.current?.focus({ preventScroll: true });
    },
  });

  // Focus only after placement: the popup is `visibility: hidden` until then and can't take focus.
  useLayoutEffect(() => {
    const listbox = popup.current?.querySelector<HTMLElement>('[role="listbox"]');
    if (!isOpen || !listbox) return;
    listbox.focus({ preventScroll: true });
    revealActiveOption(listbox);
  }, [isOpen]);

  const name = selected?.name ?? (options.length ? "Choose a model" : "No models available");
  const status = selected ? statusView(selected.status) : null;

  return <div className={`model-picker ${className}`.trim()}>
    <div className="model-picker-label"><span id={labelId}>{label}</span>{info}</div>
    <div ref={card} className="model-picker-card" data-open={isOpen || undefined} data-tone={status?.tone}>
      <div id={summaryId} className="model-picker-summary">
        <span className="model-picker-name">{name}{selected?.variant && <span className="model-picker-variant"> {selected.variant}</span>}</span>
        {selected && <span className="model-picker-description">{selected.description}</span>}
        {selected && status && <ModelBadges option={selected}>
          <span className="model-picker-badge" data-kind="status" data-tone={status.tone}>{status.chip}</span>
        </ModelBadges>}
        {selected && !selected.available && <span className="model-picker-reason">{status?.text}</span>}
      </div>
      <button ref={trigger} type="button" className="model-picker-change" disabled={inactive}
        aria-haspopup="listbox" aria-expanded={isOpen} aria-controls={isOpen ? listboxId : undefined}
        aria-label={`${label}: ${name}`} aria-describedby={summaryId}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !open) { event.preventDefault(); setOpen(true); }
        }}>
        Change<svg className="model-picker-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
      </button>
    </div>
    {isOpen && createPortal(<>
      <div className="model-picker-backdrop" aria-hidden="true" />
      <ModelPickerList label={label} labelId={labelId} listboxId={listboxId} options={options} value={value} popupRef={popup}
        onChoose={(id) => { close(); if (id !== value) onChange(id); }}
        onDismiss={() => close()} />
    </>, host ?? document.body)}
  </div>;
}
