import { useCallback, useEffect, useId, useRef, useState } from "react";

import type { McfCategory, Severity } from "../../api/api-mcf-types";
import { severityLabels } from "../filter-policy-display";
import type { HeatCell } from "./heat-cells";
import { displayCategoryLabel, SeverityPopover } from "./severity-popover";

const levelLetters: Record<Severity, string> = { low: "L", medium: "M", high: "H" };

interface SeverityHeatMapProps {
  cells: HeatCell[];
  onChange: (token: McfCategory, severity: Severity) => void;
  /** Offered in the popover of a changed chip. */
  onReset?: (token: McfCategory) => void;
  /** Show what the person changed and what an admin requires, with the Changed filter. */
  showPersonalState?: boolean;
  disabled?: boolean;
  hint?: string;
}

/** Every category's severity as colored chips grouped by family. Each chip opens a level picker. */
export function SeverityHeatMap({ cells, onChange, onReset, showPersonalState = false, disabled = false, hint }: SeverityHeatMapProps) {
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [openToken, setOpenToken] = useState<McfCategory | null>(null);
  const closePopover = useCallback(() => setOpenToken(null), []);
  const idPrefix = useId();

  const changedCount = cells.filter(cell => cell.changed).length;
  const filterChanged = showPersonalState && onlyChanged;
  const needle = query.trim().toLowerCase();
  const shown = (cell: HeatCell) => (!filterChanged || cell.changed) && displayCategoryLabel(cell.label).toLowerCase().includes(needle);
  const families = new Map<string, HeatCell[]>();
  for (const cell of cells) families.set(cell.family, [...families.get(cell.family) ?? [], cell]);
  const rows = [...families].map(([family, members]) => ({ family, members, visible: members.filter(shown) })).filter(row => row.visible.length);

  return <div className="severity-heat-map">
    <div className="heat-map-toolbar">
      <ul className="severity-legend" aria-label="Legend">
        {(["low", "medium", "high"] as const).map(level => <li key={level}><span className={`severity-swatch severity-${level}`}>{levelLetters[level]} · {severityLabels[level]}</span></li>)}
        {showPersonalState && <>
          <li><span className="legend-changed">Outlined</span> changed by you</li>
          <li><span aria-hidden="true">🔒</span> required by an admin</li>
        </>}
      </ul>
      <input type="search" className="heat-map-search" aria-label="Search categories" placeholder={`Search ${cells.length} categories`} value={query} onChange={event => setQuery(event.target.value)} />
      {showPersonalState && <div className="heat-map-filter" role="group" aria-label="Show categories">
        <button type="button" aria-pressed={!onlyChanged} onClick={() => setOnlyChanged(false)}>All</button>
        <button type="button" aria-pressed={onlyChanged} onClick={() => setOnlyChanged(true)}>Changed ({changedCount})</button>
      </div>}
    </div>
    {hint && <p className="filter-help">{hint}</p>}
    {rows.length ? rows.map(({ family, members, visible }, index) => {
      const changed = members.filter(cell => cell.changed).length;
      const headingId = `${idPrefix}-family-${index}`;
      return <section className="heat-family" key={family} aria-labelledby={headingId}>
        <div className="heat-family-heading">
          <h3 id={headingId}>{family}</h3>
          <span>{members.length} {members.length === 1 ? "category" : "categories"}{showPersonalState && changed > 0 && ` · ${changed} changed by you`}</span>
        </div>
        <div className="heat-family-chips">{visible.map(cell => <HeatChip key={cell.token} cell={cell} open={openToken === cell.token}
          showPersonalState={showPersonalState} disabled={disabled} onChange={onChange} onReset={onReset}
          onToggle={() => setOpenToken(openToken === cell.token ? null : cell.token)} onClose={closePopover} />)}</div>
      </section>;
    }) : <p className="filter-help">No categories match.</p>}
  </div>;
}

function HeatChip({ cell, open, showPersonalState, disabled, onChange, onReset, onToggle, onClose }: {
  cell: HeatCell; open: boolean; showPersonalState: boolean; disabled: boolean;
  onChange: (token: McfCategory, severity: Severity) => void;
  onReset?: (token: McfCategory) => void;
  onToggle: () => void; onClose: () => void;
}) {
  const slot = useRef<HTMLSpanElement>(null);
  const chip = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => { onClose(); chip.current?.focus(); }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => { if (!slot.current?.contains(event.target as Node)) close(); };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, close]);

  const label = displayCategoryLabel(cell.label);
  const changed = showPersonalState && cell.changed;
  const locked = showPersonalState && cell.locked;
  const name = [`${label}: ${severityLabels[cell.value]}`, changed && "changed by you", locked && "required by an admin"].filter(Boolean).join(", ");
  const classes = ["heat-chip", `severity-${cell.value}`, changed && "is-changed", open && "is-open"].filter(Boolean).join(" ");
  return <span ref={slot} className="heat-chip-slot">
    <button ref={chip} type="button" className={classes} aria-label={name} aria-haspopup="dialog" aria-expanded={open} onClick={onToggle}>
      <span className="heat-chip-level" aria-hidden="true">{levelLetters[cell.value]}</span>
      {locked && <span aria-hidden="true">🔒</span>}
      {label}
    </button>
    {open && <SeverityPopover cell={cell} showPersonalState={showPersonalState} disabled={disabled} onChange={onChange} onReset={onReset} onClose={close} />}
  </span>;
}
