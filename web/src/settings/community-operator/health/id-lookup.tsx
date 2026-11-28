import { useEffect, useRef, useState } from "react";
import { accessFailureStatus } from "../../../api/api-community-operator";
import { readOperatorSearch, type SearchId } from "../../../api/api-community-operator-catalog";
import { titleCase } from "../shared/format";
import { useOperator } from "../shared/operator-data";
import { navigate, operatorLink, searchIdLink } from "../routes";

/**
 * Resolve a pasted ID through the service's exact-ID search and open it. Several matches are listed; none (or IDs the
 * search does not cover, such as attestations) send the operator to the inspector to choose the resource type.
 */
export function IdLookup() {
  const { onAccessFailure } = useOperator();
  const [id, setId] = useState("");
  const [state, setState] = useState<{ kind: "idle" | "busy" | "none" | "error" } | { kind: "many"; hits: SearchId[] }>({ kind: "idle" });
  const pending = useRef<AbortController | null>(null), current = useRef("");
  useEffect(() => () => pending.current?.abort(), []);
  const value = id.trim();
  current.current = value;
  const lookup = () => {
    if (!value || state.kind === "busy") return;
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    setState({ kind: "busy" });
    Promise.resolve().then(() => readOperatorSearch(value, controller.signal)).then(result => {
      // A response for an earlier input must never navigate.
      if (controller.signal.aborted || result.q !== current.current || value !== current.current) return;
      const hits = result.ids.filter(hit => hit.id === value);
      if (hits.length === 1) { setState({ kind: "idle" }); navigate(searchIdLink(hits[0].kind, hits[0].id)); }
      else setState(hits.length ? { kind: "many", hits } : { kind: "none" });
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      const denied = accessFailureStatus(error);
      if (denied) onAccessFailure(denied); else setState({ kind: "error" });
    });
  };
  const chooser = <a href={operatorLink("inspect", { selected: value })}>choose the resource type in the inspector</a>;
  return <div className="stack-v">
    <form className="inline" onSubmit={event => { event.preventDefault(); lookup(); }}>
      <label htmlFor="tools-lookup" className="sr">Resource ID</label>
      <input id="tools-lookup" className="mono" value={id} maxLength={100} placeholder="Any version, filter, report… ID"
        onChange={event => { pending.current?.abort(); pending.current = null; setId(event.target.value); setState({ kind: "idle" }); }} />
      <button className="btn primary" disabled={!value || state.kind === "busy"}>{state.kind === "busy" ? "Looking up…" : "Look up"}</button></form>
    {state.kind === "many" && <div className="result" role="status">This ID matches several resources:<ul>{state.hits.map(hit =>
      <li key={hit.kind}><a href={searchIdLink(hit.kind, hit.id)}>{titleCase(hit.kind)} · {hit.label}</a></li>)}</ul></div>}
    {state.kind === "none" && <p className="result" role="status">No resource has this exact ID. For attestations or votes, {chooser}.</p>}
    {state.kind === "error" && <p className="result" role="alert">Lookup unavailable. You can still {chooser}.</p>}
  </div>;
}
