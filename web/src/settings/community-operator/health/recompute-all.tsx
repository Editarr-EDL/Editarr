import { useEffect, useRef, useState } from "react";
import { OperatorMutationError } from "../../../api/api-community-operator";
import { writeOperatorRecompute, type RecomputeIntent } from "../../../api/api-community-operator-actions";
import { reasonText } from "../../../api/api-community-operator-checks";
import { OperatorAccessError } from "../runtime";

/**
 * Recompute every non-merged version under the current policy. Each action recomputes one bounded
 * batch; the operator continues from `nextAfter` with a new action until the job is complete.
 */
export function RecomputeAll({ onAccessFailure }: { onAccessFailure: (status: number) => void }) {
  const [reason, setReason] = useState("");
  const [after, setAfter] = useState<string | null>(null);
  const [saved, setSaved] = useState<RecomputeIntent | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const run = async () => {
    if (busy || (!saved && !reasonText(reason))) return;
    const intent = saved ?? { version: 1 as const, actionId: crypto.randomUUID(), versionIds: "all" as const, reason,
      ...(after ? { after } : {}) };
    setSaved(intent); setBusy(true); setMessage("");
    const controller = new AbortController(); pending.current = controller;
    try {
      const result = await writeOperatorRecompute(intent, controller.signal);
      if (controller.signal.aborted || !("jobId" in result)) return;
      setSaved(null); setAfter(result.nextAfter);
      setMessage(result.complete ? `Recompute complete. Audit ID: ${result.jobId}.` :
        `Batch recorded. Audit ID: ${result.jobId}. More versions remain after ${result.nextAfter}.`);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof OperatorAccessError && [401, 403].includes(error.status)) {
        onAccessFailure(error.status); return;
      }
      if (error instanceof OperatorMutationError || (error instanceof OperatorAccessError && error.status === 400)) {
        setSaved(null);
        setMessage(`Recompute refused${error instanceof OperatorMutationError ? ` (${error.code})` : ""}. No batch is confirmed.`);
      } else setMessage("Outcome unconfirmed. Retry only this saved batch; its ID, reason and cursor are unchanged.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <section aria-label="Recompute all versions" className="recompute-all"><h3>Recompute all versions</h3>
    <p className="operator-meta">Use after a MASTER_POLICY change. Each action recomputes up to 200 versions in version ID order
      and is audited; nothing changes for versions whose inputs did not change.</p>
    <form onSubmit={event => { event.preventDefault(); void run(); }}>
      <label htmlFor="operator-recompute-reason">Operator reason</label>
      <textarea id="operator-recompute-reason" maxLength={1000} required value={reason}
        disabled={busy || !!saved} onChange={event => setReason(event.target.value)} />
      <div className="operator-action-bar">
        <button className="btn danger" disabled={busy || (!saved && !reasonText(reason))}>{busy ? "Recomputing..." : saved ? "Retry saved batch" :
          after ? `Continue after ${after}` : "Recompute all versions"}</button>
        {(saved || after) && !busy && <button type="button" className="btn" onClick={() => { setSaved(null); setAfter(null); setMessage(""); }}>
          {saved ? "Discard saved batch" : "Start over"}</button>}
      </div>
    </form>
    {message && <p role="status" className="operator-action-message">{message}</p>}
  </section>;
}
