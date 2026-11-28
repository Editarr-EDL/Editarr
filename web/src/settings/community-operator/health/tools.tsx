import { useState } from "react";
import { identifier } from "../../../api/api-community-operator-checks";
import type { MaintenanceRun } from "../../../api/api-community-operator-system";
import { useModeration } from "../moderation-dialog";
import { ago, duration, shortDate } from "../shared/format";
import { useOperator } from "../shared/operator-data";
import { PanelHead } from "../shared/ui";
import { navigate, operatorLink } from "../routes";
import { RecomputeAll } from "./recompute-all";

/** ID lookup, single-version recompute and the batched recompute of every version; every tool is audited. */
export function Tools({ lastRecomputeAll, onApplied }: { lastRecomputeAll: MaintenanceRun | null; onApplied: () => void }) {
  const { onAccessFailure } = useOperator();
  const [id, setId] = useState(""), [versionId, setVersionId] = useState("");
  const moderation = useModeration({ onAccessFailure, onApplied });
  const versions = lastRecomputeAll?.detail.versions;
  return <section className="card tools-card" aria-labelledby="tools-title">
    <PanelHead title={<span id="tools-title">Tools</span>}><span className="muted small">Every tool writes to the <a href="#/audit">audit log</a></span></PanelHead>
    <div className="sect"><div><h3>Look up any ID</h3><p>Open a version, filter, submission, contributor, report or alias suggestion by ID. The global search does this too.</p></div>
      <form className="inline" onSubmit={event => { event.preventDefault(); if (id.trim()) navigate(operatorLink("inspect", { selected: id.trim(), kind: guessKind(id.trim()) })); }}>
        <label htmlFor="tools-lookup" className="sr">Resource ID</label>
        <input id="tools-lookup" className="mono" value={id} maxLength={161} placeholder="f_…, v_…, r_…" onChange={event => setId(event.target.value)} />
        <button className="btn primary" disabled={!id.trim()}>Look up</button></form></div>
    <div className="sect"><div><h3>Recompute</h3><p>Rebuild master sets under the current policy. A revision advances only when served content changes.</p></div>
      <div className="stack-v">
        <form className="inline" onSubmit={event => { event.preventDefault();
          if (identifier(versionId.trim())) void moderation.begin({ kind: "version", targetId: versionId.trim() }, "recompute"); }}>
          <label htmlFor="tools-version" className="sr">Version ID to recompute</label>
          <input id="tools-version" className="mono" value={versionId} maxLength={128} placeholder="Version ID (v_…)" onChange={event => setVersionId(event.target.value)} />
          <button className="btn" disabled={!identifier(versionId.trim()) || moderation.locked}>Recompute version</button></form>
        {moderation.view}
        <p className="faint small">{lastRecomputeAll ? `Last full recompute ${shortDate(lastRecomputeAll.startedAt)} (${ago(lastRecomputeAll.startedAt)}) ·
          ${typeof versions === "number" ? `${versions} versions · ` : ""}${duration(lastRecomputeAll.durationMs)} · ${lastRecomputeAll.outcome.replace("_", " ")}` :
          "No full recompute recorded yet."}</p>
        <RecomputeAll onAccessFailure={onAccessFailure} />
      </div></div>
  </section>;
}
/** Resource kind from a conventional ID prefix (f_, v_, r_, as_, sub_), defaulting to filter. */
function guessKind(id: string) {
  if (id.includes(":")) return "attestation";
  const prefixes: [string, string][] = [["as_", "alias_suggestion"], ["sub_", "submission"], ["v_", "version"], ["r_", "report"], ["f_", "filter"]];
  return prefixes.find(([prefix]) => id.startsWith(prefix))?.[1] ?? (/^[0-9a-f-]{36}$/i.test(id) ? "contributor" : "filter");
}
