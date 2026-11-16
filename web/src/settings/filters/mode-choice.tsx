import { useId, useState, type ReactNode } from "react";

import type { FilterMode } from "../../api/api-user-access";

export interface ModeOption { value: FilterMode; title: string; body: string }

/** A card with one radio card per filter mode. Choosing a card saves it right away and shows it chosen while it saves. */
export function ModeChoice({ heading, description, options, value, disabled, note, onChoose }: {
  heading: string; description: string; options: ModeOption[]; value: FilterMode | null;
  disabled: boolean; note?: ReactNode; onChoose: (mode: FilterMode) => Promise<unknown>;
}) {
  const id = useId();
  const [pending, setPending] = useState<FilterMode | null>(null);
  const shown = pending ?? value;
  const choose = (mode: FilterMode) => { setPending(mode); void onChoose(mode).finally(() => setPending(null)); };
  return <section className="settings-card filter-mode-card" aria-labelledby={`${id}-heading`}>
    <div>
      <h2 id={`${id}-heading`}>{heading}</h2>
      <p className="filter-help">{description}</p>
    </div>
    <div className="mode-options" role="radiogroup" aria-labelledby={`${id}-heading`}>
      {options.map(option => <label key={option.value} className={option.value === shown ? "mode-option is-checked" : "mode-option"}>
        <input type="radio" name={`${id}-mode`} value={option.value} checked={option.value === shown} disabled={disabled || pending !== null}
          aria-labelledby={`${id}-${option.value}`} aria-describedby={`${id}-${option.value}-body`} onChange={() => choose(option.value)} />
        <span><strong id={`${id}-${option.value}`}>{option.title}</strong><span id={`${id}-${option.value}-body`} className="filter-help">{option.body}</span></span>
      </label>)}
    </div>
    {note}
  </section>;
}
