import { useState } from "react";
import { identifier } from "../../../api/api-community-operator-checks";
import type { MaintenanceRun } from "../../../api/api-community-operator-system";
import { useModeration } from "../moderation-dialog";
import { ago, duration, shortDate } from "../shared/format";
import { useOperator } from "../shared/operator-data";
import { PanelHead } from "../shared/ui";
import { IdLookup } from "./id-lookup";
import { RecomputeAll } from "./recompute-all";

/** ID lookup, single-version recompute and the batched recompute of every version; every tool is audited. */
export function Tools({ lastRecomputeAll, onApplied }: { lastRecomputeAll: MaintenanceRun | null; onApplied: () => void }) {
  const { onAccessFailure } = useOperator();
  const [versionId, setVersionId] = useState("");
  const moderation = useModeration({ onAccessFailure, onApplied });
  const versions = lastRecomputeAll?.detail.versions;
  return <section className="card tools-card" aria-labelledby="tools-title">
    <PanelHead title={<span id="tools-title">Tools</span>}><span className="muted small">Every tool writes to the <a href="#/audit">audit log</a></span></PanelHead>
    <div className="sect"><div><h3>Look up any ID</h3><p>Open a version, filter, submission, contributor, report or alias suggestion by ID. The global search does this too.</p></div>
      <IdLookup /></div>
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
