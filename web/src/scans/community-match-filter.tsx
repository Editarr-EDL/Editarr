import { useEffect, useId, useRef, useState } from "react";
import type { MatchBand } from "../api/api-community-lookup";
import { bandLabels, matchBands } from "./community-result-state";
import { CommunityIcon } from "./community-icon";

export function CommunityMatchFilter({ bands, counts, onChange }: {
  bands: ReadonlySet<MatchBand>; counts: ReadonlyMap<MatchBand, number>; onChange: (bands: Set<MatchBand>) => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const allControl = useRef<HTMLInputElement>(null);
  const all = bands.size === matchBands.length;
  useEffect(() => {
    if (allControl.current) allControl.current.indeterminate = !all && bands.size > 0;
  }, [all, bands.size, open]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !ref.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  return <div className="community-match-filter" ref={ref} onKeyDown={(event) => {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    event.stopPropagation();
    setOpen(false);
    trigger.current?.focus();
  }}>
    <button className="btn-secondary community-match-button" ref={trigger} type="button" aria-expanded={open}
      aria-controls={id} onClick={() => setOpen(!open)}>Match <span>{all ? "All" : bands.size}</span><CommunityIcon name="expand" /></button>
    {open && <fieldset className="community-match-options" id={id}>
      <legend className="visually-hidden">Match grades</legend>
      <label><input ref={allControl} type="checkbox" checked={all}
        onChange={(event) => onChange(new Set(event.target.checked ? matchBands : []))} />All grades</label>
      {matchBands.map((band) => <label key={band}>
        <input type="checkbox" checked={bands.has(band)} aria-labelledby={`${id}-${band}-label`}
          aria-describedby={`${id}-${band}-count`} onChange={(event) => {
            const next = new Set(bands);
            if (event.target.checked) next.add(band);
            else next.delete(band);
            onChange(next);
          }} />
        <span id={`${id}-${band}-label`}>{bandLabels[band]}</span>
        <span id={`${id}-${band}-count`} className="visually-hidden">Loaded results: {counts.get(band) ?? 0}</span>
        <span className="community-match-option-count" aria-hidden="true">{counts.get(band) ?? 0}</span>
      </label>)}
    </fieldset>}
  </div>;
}
