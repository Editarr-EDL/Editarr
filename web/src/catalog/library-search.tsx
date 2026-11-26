import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { SearchSuggestion, SearchToken } from "./library-search-suggestions";

/**
 * One search box for titles and filters (combobox pattern). Chosen filters show as removable tokens inside the
 * box. Typing (or ArrowDown) opens grouped suggestions; ArrowUp/ArrowDown move through them, Enter chooses,
 * Escape closes, and Backspace in the empty box removes the last token.
 * @param onChoose Applies a chosen suggestion; the box keeps focus and closes its list.
 */
export function LibrarySearch({ text, onTextChange, tokens, onRemoveToken, suggestions, onChoose, onClear }: {
  text: string;
  onTextChange: (text: string) => void;
  tokens: SearchToken[];
  onRemoveToken: (token: SearchToken) => void;
  suggestions: SearchSuggestion[];
  onChoose: (suggestion: SearchSuggestion) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const expanded = open && suggestions.length > 0;
  const activeSuggestion = expanded && active >= 0 ? suggestions[active] : undefined;
  // Index-based, so ids stay valid whatever a rating or title contains.
  const optionId = (suggestion: SearchSuggestion) => `${listId}-option-${suggestions.indexOf(suggestion)}`;

  useEffect(() => { setActive((index) => Math.min(index, suggestions.length - 1)); }, [suggestions.length]);

  const choose = (suggestion: SearchSuggestion) => {
    setOpen(false);
    setActive(-1);
    onChoose(suggestion);
    input.current?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const count = suggestions.length;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!count) return;
      event.preventDefault();
      if (!expanded) { setOpen(true); setActive(event.key === "ArrowDown" ? 0 : count - 1); return; }
      setActive((index) => event.key === "ArrowDown" ? (index + 1) % count : (index <= 0 ? count - 1 : index - 1));
    } else if (event.key === "Enter" && activeSuggestion) {
      event.preventDefault();
      choose(activeSuggestion);
    } else if (event.key === "Escape") {
      if (expanded) { event.preventDefault(); setOpen(false); setActive(-1); }
      else if (text) { event.preventDefault(); onTextChange(""); }
    } else if (event.key === "Backspace" && !text && tokens.length) {
      event.preventDefault();
      onRemoveToken(tokens[tokens.length - 1]);
    }
  };
  const groups = [
    { label: "Titles", options: suggestions.filter((suggestion) => suggestion.kind === "title") },
    { label: "Filters", options: suggestions.filter((suggestion) => suggestion.kind === "filter") },
  ].filter((group) => group.options.length);

  return <div className="library-search">
    <div className="library-search-box" onPointerDown={(event) => { if (event.target === event.currentTarget) { event.preventDefault(); input.current?.focus(); } }}>
      <span className="library-search-icon" aria-hidden="true">⌕</span>
      {tokens.map((token) => <span className="search-token" key={token.facet}>
        {token.label}
        <button type="button" aria-label={`Remove ${token.label} filter`} title={`Remove ${token.label} filter`}
          onClick={() => { onRemoveToken(token); input.current?.focus(); }}><span aria-hidden="true">×</span></button>
      </span>)}
      <input ref={input} type="text" role="combobox" enterKeyHint="search" autoComplete="off" spellCheck={false}
        aria-label="Search titles or filters" placeholder="Search titles or add a filter…"
        aria-expanded={expanded} aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={activeSuggestion ? optionId(activeSuggestion) : undefined}
        value={text} onKeyDown={onKeyDown} onBlur={() => { setOpen(false); setActive(-1); }}
        onChange={(event) => { onTextChange(event.target.value); setOpen(true); setActive(-1); }} />
      {(text || tokens.length > 0) && <button type="button" className="library-search-clear" aria-label="Clear search and filters"
        title="Clear search and filters" onClick={() => { onClear(); input.current?.focus(); }}><span aria-hidden="true">✕</span></button>}
    </div>
    <div id={listId} role="listbox" aria-label="Search suggestions" className="library-search-suggestions" hidden={!expanded}>
      {expanded && groups.map((group) => <div role="group" key={group.label} aria-labelledby={`${listId}-group-${group.label}`}>
        <div role="presentation" id={`${listId}-group-${group.label}`} className="library-search-group">{group.label}</div>
        {group.options.map((suggestion) => {
          const index = suggestions.indexOf(suggestion);
          return <div role="option" key={suggestion.id} id={optionId(suggestion)} aria-selected={index === active}
            className={index === active ? "active" : undefined}
            onPointerDown={(event) => event.preventDefault()} onPointerEnter={() => setActive(index)}
            onClick={() => choose(suggestion)}>{suggestion.label}</div>;
        })}
      </div>)}
    </div>
  </div>;
}
