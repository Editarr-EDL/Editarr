import { useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import type { ModelOption } from "./model-catalog";
import { ModelBadges } from "./model-badges";
import { statusView } from "./model-status";

interface Group { key: string; label: string; options: ModelOption[] }

/** Split options into Recommended, other available models, and models this host can't run. */
export function groupModelOptions(options: ModelOption[]): Group[] {
  const recommended = options.filter((option) => option.available && option.recommended);
  const others = options.filter((option) => option.available && !option.recommended);
  const unavailable = options.filter((option) => !option.available);
  return [
    { key: "recommended", label: "Recommended", options: recommended },
    { key: "other", label: recommended.length ? "Other models" : "Models", options: others },
    { key: "unavailable", label: "Unavailable on this host", options: unavailable },
  ].filter((group) => group.options.length);
}

const typeaheadResetMs = 500;

/** Scroll only `listbox` so its active option (and that group's heading, for the first row) is fully visible. */
export function revealActiveOption(listbox: HTMLElement) {
  const row = listbox.querySelector<HTMLElement>("[data-active]");
  if (!row) return;
  const group = row.parentElement;
  const target = group?.querySelector('[role="option"]') === row ? group : row;
  const view = listbox.getBoundingClientRect();
  const box = (target ?? row).getBoundingClientRect();
  const rowBox = row.getBoundingClientRect();
  if (box.top < view.top) listbox.scrollTop -= view.top - box.top;
  else if (rowBox.bottom > view.bottom) listbox.scrollTop += Math.min(rowBox.bottom - view.bottom, rowBox.top - view.top);
}

/**
 * The picker's popup listbox; the picker focuses it once placed. It tracks the active row with
 * `aria-activedescendant`; arrows, Home/End and typeahead move, Enter/Space choose an available row,
 * and Escape or Tab dismiss through `onDismiss`.
 */
export function ModelPickerList({ label, labelId, listboxId, options, value, popupRef, onChoose, onDismiss }: {
  label: string;
  labelId: string;
  listboxId: string;
  options: ModelOption[];
  value: string;
  popupRef: RefObject<HTMLDivElement | null>;
  onChoose: (id: string) => void;
  onDismiss: (reason: "escape" | "tab") => void;
}) {
  const groups = groupModelOptions(options);
  const ordered = groups.flatMap((group) => group.options);
  const typeahead = useRef({ text: "", at: 0 });
  const [activeId, setActiveId] = useState(() =>
    (ordered.find((option) => option.id === value) ?? ordered.find((option) => option.available) ?? ordered[0])?.id ?? "");
  const domId = (id: string) => `${listboxId}-option-${id.replace(/[^\w-]/g, "_")}`;

  const listbox = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { if (listbox.current) revealActiveOption(listbox.current); }, [activeId]);

  const choose = (option: ModelOption | undefined) => { if (option?.available) onChoose(option.id); };
  const move = (index: number) => { const next = ordered[Math.max(0, Math.min(index, ordered.length - 1))]; if (next) setActiveId(next.id); };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = ordered.findIndex((option) => option.id === activeId);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); move(index + (event.key === "ArrowDown" ? 1 : -1)); }
    else if (event.key === "Home" || event.key === "End") { event.preventDefault(); move(event.key === "Home" ? 0 : ordered.length - 1); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(ordered[index]); }
    else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onDismiss("escape"); }
    else if (event.key === "Tab") onDismiss("tab");
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const text = (now - typeahead.current.at > typeaheadResetMs ? "" : typeahead.current.text) + event.key.toLowerCase();
      typeahead.current = { text, at: now };
      const rotated = [...ordered.slice(index + 1), ...ordered.slice(0, index + 1)];
      const search = text.length > 1 ? [ordered[index], ...rotated] : rotated;
      const match = search.find((option) => option?.name.toLowerCase().startsWith(text));
      if (match) setActiveId(match.id);
    }
  };

  return <div ref={popupRef} className="model-picker-popup">
    <div className="model-picker-sheet-header" aria-hidden="true"><strong>{label}</strong><span>Tap outside to close</span></div>
    <div ref={listbox} id={listboxId} className="model-picker-listbox" role="listbox" tabIndex={0} aria-labelledby={labelId}
      aria-activedescendant={activeId ? domId(activeId) : undefined} onKeyDown={onKeyDown}>
      {groups.map((group) => <div key={group.key} role="group" aria-labelledby={`${listboxId}-${group.key}`} className="model-picker-group">
        <div id={`${listboxId}-${group.key}`} role="presentation" className="model-picker-group-heading">{group.label}</div>
        {group.options.map((option) => <ModelPickerRow key={option.id} option={option} id={domId(option.id)}
          selected={option.id === value} active={option.id === activeId}
          onPoint={() => setActiveId(option.id)} onChoose={() => choose(option)} />)}
      </div>)}
    </div>
    <div className="model-picker-footer">
      <span className="model-picker-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> move <kbd>↵</kbd> select <kbd>esc</kbd> close</span>
      <span>Downloads are managed in Settings › Models</span>
    </div>
  </div>;
}

function ModelPickerRow({ option, id, selected, active, onPoint, onChoose }: {
  option: ModelOption; id: string; selected: boolean; active: boolean; onPoint: () => void; onChoose: () => void;
}) {
  const status = statusView(option.status);
  const details = [
    option.detects.length ? `Detects: ${option.detects.join(", ")}` : "",
    option.reports.length ? `Reports: ${option.reports.join(", ")}` : "",
    option.bestFor ? `Best for: ${option.bestFor}` : "",
  ].filter(Boolean);
  return <div id={id} role="option" aria-selected={selected} aria-disabled={option.available ? undefined : true}
    aria-labelledby={option.variant ? `${id}-name ${id}-variant` : `${id}-name`} aria-describedby={`${id}-badges ${id}-description ${id}-status${details.length ? ` ${id}-details` : ""}`}
    className="model-picker-option" data-active={active || undefined} data-tone={status.tone}
    onMouseMove={active ? undefined : onPoint} onClick={onChoose}>
    <span className="model-picker-radio" aria-hidden="true" />
    <span className="model-picker-option-main">
      <span className="model-picker-option-title">
        <span id={`${id}-name`} className="model-picker-name">{option.name}</span>
        {option.variant && <span id={`${id}-variant`} className="model-picker-variant">{option.variant}</span>}
        <ModelBadges id={`${id}-badges`} option={option} extra={option.badges} />
      </span>
      <span id={`${id}-description`} className="model-picker-description">{option.description}</span>
      {details.length > 0 && <span id={`${id}-details`} className="model-picker-details">{details.join(" · ")}</span>}
    </span>
    <span id={`${id}-status`} className="model-picker-option-status">
      {status.size && <span className="model-picker-size">{status.size}</span>}
      {status.percent !== null && <span className="model-picker-progress" aria-hidden="true"><span style={{ width: `${status.percent}%` }} /></span>}
      <span className="model-picker-status-text"><span className="model-picker-dot" aria-hidden="true" />{status.text}</span>
    </span>
  </div>;
}
