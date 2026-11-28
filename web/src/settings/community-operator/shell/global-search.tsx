import { useEffect, useRef, useState } from "react";
import { readOperatorSearch, type SearchResult } from "../../../api/api-community-operator-catalog";
import { OperatorAccessError } from "../runtime";
import { itemTitle, titleCase } from "../shared/format";
import { useOperator } from "../shared/operator-data";
import { contributorLink, itemLink, navigate, operatorLink } from "../routes";
import type { OperatorResource } from "../../../api/api-community-operator";

const resources = new Set<string>(["filter", "attestation", "submission", "contributor", "version", "vote", "report", "alias_suggestion"]);
/** Where an exact-ID search hit opens. */
function idLink(kind: string, id: string) {
  if (kind === "work" || kind === "item") return itemLink(id);
  if (kind === "contributor") return contributorLink(id);
  if (kind === "filter") return operatorLink(`filters/${id}`);
  return resources.has(kind) ? operatorLink("inspect", { kind: kind as OperatorResource, selected: id }) : operatorLink("inspect");
}

/** Top-bar search over titles, contributors and exact IDs. "/" focuses it; Enter opens the first result. */
export function GlobalSearch() {
  const { onAccessFailure } = useOperator();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null), box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === "/" && !event.metaKey && !event.ctrlKey && !target?.closest("input, textarea, select, [contenteditable]")) {
        event.preventDefault(); input.current?.focus();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    const value = query.trim();
    if (value.length < 2) { setResult(null); setState("idle"); return; }
    const controller = new AbortController();
    setState("loading");
    const timer = window.setTimeout(() => {
      readOperatorSearch(value, controller.signal).then(found => { if (!controller.signal.aborted) { setResult(found); setState("idle"); } })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (error instanceof OperatorAccessError && [401, 403].includes(error.status)) onAccessFailure(error.status);
          else setState("error");
        });
    }, 220);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query]);
  const links: { href: string; label: string; group: string }[] = result ? [
    ...result.ids.map(hit => ({ href: idLink(hit.kind, hit.id), label: `${titleCase(hit.kind)} ${hit.id}${hit.label ? ` · ${hit.label}` : ""}`, group: "IDs" })),
    ...result.items.map(item => ({ href: itemLink(item.workId), label: itemTitle(item), group: "Items" })),
    ...result.contributors.map(person => ({ href: contributorLink(person.contributorId), label: person.displayName, group: "Contributors" })),
  ] : [];
  const close = () => { setOpen(false); setQuery(""); };
  const groups = [...new Set(links.map(link => link.group))];
  return <div className="gsearch" ref={box} onBlur={event => { if (!box.current?.contains(event.relatedTarget as Node)) setOpen(false); }}>
    <label htmlFor="operator-search" className="sr">Search titles, contributors or any ID</label>
    <input id="operator-search" ref={input} type="search" placeholder="Search titles, contributors or any ID…" value={query} maxLength={200}
      autoComplete="off" aria-controls={open && query.trim().length >= 2 ? "operator-search-results" : undefined}
      onFocus={() => setOpen(true)} onChange={event => { setQuery(event.target.value); setOpen(true); }}
      onKeyDown={event => {
        if (event.key === "Escape") { close(); input.current?.blur(); }
        if (event.key === "Enter" && links[0]) { navigate(links[0].href); close(); }
      }} />
    <kbd aria-hidden="true">/</kbd>
    {open && query.trim().length >= 2 && <div className="search-pop" id="operator-search-results" role="region" aria-label="Search results">
      {state === "loading" && <p className="muted" role="status">Searching…</p>}
      {state === "error" && <p role="alert">Search unavailable</p>}
      {state === "idle" && result && !links.length && <p className="muted" role="status">No matches</p>}
      {groups.map(group => <div key={group}><h2 className="navtitle">{group}</h2><ul>{links.filter(link => link.group === group)
        .map(link => <li key={link.href + link.label}><a href={link.href} onClick={close}>{link.label}</a></li>)}</ul></div>)}
    </div>}
  </div>;
}
