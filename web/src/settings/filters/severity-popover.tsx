import { useEffect, useId, useLayoutEffect, useRef, type KeyboardEvent } from "react";

import type { McfCategory, Severity } from "../../api/api-mcf-types";
import { severityLabels } from "../filter-policy-display";
import type { HeatCell } from "./heat-cells";

const levels: Severity[] = ["low", "medium", "high"];
const viewportMargin = 8;
const arrowSteps: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

export function displayCategoryLabel(label: string): string {
  return label.replace(/ \(General\)$/, " (all)");
}

/** The level picker that opens from a heat map chip. Escape asks the owner to close it. */
export function SeverityPopover({ cell, showPersonalState, disabled, onChange, onReset, onClose }: {
  cell: HeatCell; showPersonalState: boolean; disabled: boolean;
  onChange: (token: McfCategory, severity: Severity) => void;
  onReset?: (token: McfCategory) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);
  const radios = useRef<(HTMLButtonElement | null)[]>([]);
  useLayoutEffect(() => {
    const node = dialog.current;
    if (!node) return;
    const { left, right } = node.getBoundingClientRect();
    const overflow = Math.min(right - (document.documentElement.clientWidth - viewportMargin), left - viewportMargin);
    if (overflow > 0) node.style.left = `${-overflow}px`;
  }, []);
  useEffect(() => {
    const checked = radios.current[levels.indexOf(cell.value)];
    (checked && !checked.disabled ? checked : dialog.current)?.focus();
  }, []);

  const origin = !showPersonalState ? "Default for everyone" : cell.server ? `Server default ${severityLabels[cell.server]}` : null;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    onClose();
  };
  const onRadioKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = arrowSteps[event.key];
    if (!step) return;
    event.preventDefault();
    const next = (index + step + levels.length) % levels.length;
    onChange(cell.token, levels[next]);
    radios.current[next]?.focus();
  };

  return <div ref={dialog} className="severity-popover" role="dialog" aria-labelledby={titleId} tabIndex={-1} onKeyDown={onKeyDown}>
    <strong id={titleId}>{displayCategoryLabel(cell.label)}</strong>
    <span className="severity-popover-origin">{[cell.family, origin].filter(Boolean).join(" · ")}</span>
    {cell.locked ? <p className="severity-popover-locked">Required by an admin at {severityLabels[cell.value]}. You can't change it here.</p>
      : <div className="severity-segments" role="radiogroup" aria-label="Severity">{levels.map((level, index) => {
        const checked = level === cell.value;
        return <button key={level} ref={node => { radios.current[index] = node; }} type="button" role="radio" aria-checked={checked}
          tabIndex={checked ? 0 : -1} disabled={disabled} onClick={() => { if (!checked) onChange(cell.token, level); }} onKeyDown={event => onRadioKeyDown(event, index)}>
          {severityLabels[level]}
        </button>;
      })}</div>}
    {!cell.locked && cell.changed && onReset && <button type="button" className="filter-link-button" disabled={disabled}
      onClick={() => { onReset(cell.token); onClose(); }}>Reset to server default</button>}
  </div>;
}
