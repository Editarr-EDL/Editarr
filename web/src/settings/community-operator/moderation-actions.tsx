import { useEffect, useRef, useState } from "react";
import { readOperatorInspection, readOperatorPage, writeOperatorAction, OperatorMutationError,
  type OperatorCommand, type OperatorInspection, type OperatorIntent, type OperatorPage } from "../../api/api-community-operator";
import { OperatorAccessError } from "./runtime";

const labels: Record<OperatorCommand, string> = { quarantine: "Quarantine revision", restore: "Restore revision",
  suspend: "Suspend contributor", unsuspend: "Restore contributor access", exclude_vote: "Exclude vote",
  restore_vote: "Restore vote", resolve_report: "Resolve report" };
const impacts: Record<OperatorCommand, string> = {
  quarantine: "Hide this revision from discovery and downloads. Signed bytes, authorship and existing local downloads are retained.",
  restore: "Restore discovery and downloads only if the author and remaining restrictions permit it. No local evidence changes.",
  suspend: "Block all community access for this contributor and all their installations, and hide every authored revision. Keys remain unchanged; votes and totals are preserved. Existing local downloads cannot be recalled.",
  unsuspend: "Remove this contributor's suspension. Membership and key checks still apply; quarantined revisions remain hidden. No key is reactivated or rotated.",
  exclude_vote: "Exclude this contributor's vote on this exact revision from totals and quality ranking. The original vote, import fact and audit are retained.",
  restore_vote: "Include this exact vote in totals only if the import, contributor, author and revision remain eligible. Original evidence is retained.",
  resolve_report: "Close this private report and retain its text and audit. This does not quarantine a revision, suspend a contributor or change votes.",
};
function commandsFor(data: OperatorInspection): OperatorCommand[] {
  if (data.resource.startsWith("revision:")) return data.state.state === "published" ? ["quarantine"] :
    data.state.state === "quarantined" ? ["restore"] : [];
  if (data.resource.startsWith("contributor:")) return data.state.state === "active" ? ["suspend"] : ["unsuspend"];
  if (data.resource.startsWith("report:")) return data.state.state === "open" ? ["resolve_report"] : [];
  return data.state.excluded === 1 ? ["restore_vote"] : ["exclude_vote"];
}
type Review = { command: OperatorCommand; snapshot: OperatorInspection; intent: OperatorIntent | null; keys: OperatorPage | null };

export function ModerationActions({ inspection, enabled, onAccessFailure, onApplied }: {
  inspection: OperatorInspection; enabled: boolean; onAccessFailure: (status: number) => void; onApplied: () => void;
}) {
  const [review, setReview] = useState<Review | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const lock = useRef(false), active = useRef(true), dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const requests = useRef(new Set<AbortController>());
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; for (const controller of requests.current) controller.abort(); };
  }, []);
  useEffect(() => {
    if (review && !collapsed && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [review, collapsed]);
  const showing = !!review && !collapsed;
  useEffect(() => {
    if (showing) return () => { if (opener.current?.isConnected) opener.current.focus(); };
  }, [showing]);
  const accessFailure = (error: unknown) => {
    if (error instanceof OperatorMutationError && error.code === "moderation_ineligible") return false;
    if (error instanceof OperatorAccessError && [401, 403].includes(error.status)) {
      onAccessFailure(error.status); return true;
    }
    return false;
  };
  const begin = async (command: OperatorCommand) => {
    if (lock.current || !enabled) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    lock.current = true; setBusy(true); setMessage("");
    const controller = new AbortController(); requests.current.add(controller);
    const [kind, id, revisionId] = inspection.resource.split(":");
    try {
      const snapshot = await readOperatorInspection(kind, id, controller.signal, revisionId);
      const keys = kind === "contributor" ? await readOperatorPage("keys", null, controller.signal, id) : null;
      if (!active.current) return;
      if (snapshot.revision !== inspection.revision || !commandsFor(snapshot).includes(command)) {
        setMessage("State changed. Refresh and review the resource before starting a new action."); onApplied(); return;
      }
      setReason(""); setUncertain(false); setCollapsed(false); setReview({ command, snapshot, intent: null, keys });
    } catch (error) {
      if (active.current && !accessFailure(error)) setMessage("Fresh inspection unavailable. No action sent.");
    } finally { requests.current.delete(controller); lock.current = false; if (active.current) setBusy(false); }
  };
  const submit = async () => {
    if (!review || lock.current || (!review.intent && (!reason.trim() || reason.length > 1000 ||
      /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(reason)))) return;
    lock.current = true; setBusy(true); setMessage("");
    const [, targetId, revisionId] = review.snapshot.resource.split(":");
    const intent = review.intent ?? { version: 1 as const, actionId: crypto.randomUUID(), command: review.command, targetId,
      ...(revisionId ? { revisionId } : {}), expectedRevision: review.snapshot.revision, reason };
    setReview({ ...review, intent });
    const controller = new AbortController(); requests.current.add(controller);
    try {
      const result = await writeOperatorAction(intent, controller.signal);
      if (!active.current) return;
      setReview(null); setUncertain(false);
      setMessage(`Action applied. Audit ID: ${result.actionId}. Refreshing current state...`); onApplied();
    } catch (error) {
      if (!active.current || accessFailure(error)) return;
      if (error instanceof OperatorAccessError && (error.status === 409 || error.status === 400 || error.status === 404 ||
        (error instanceof OperatorMutationError && error.code === "moderation_ineligible"))) {
        setReview(null); setUncertain(false);
        setMessage(error.status === 409 ? "State conflict. Refresh and review before starting a new action." :
          "Action refused. Refresh and review resource eligibility; no successful action is confirmed."); onApplied();
      } else {
        setUncertain(true); setMessage("Outcome unconfirmed. Retry only this saved action; its ID, reason and expected revision are unchanged.");
      }
    } finally { requests.current.delete(controller); lock.current = false; if (active.current) setBusy(false); }
  };
  const cancel = () => {
    if (lock.current) return;
    if (uncertain) setCollapsed(true);
    else { setReview(null); setMessage(""); }
  };
  return <section aria-label="Moderation actions">
    <h3>Moderation</h3>
    <div className="operator-action-bar">{commandsFor(inspection).map(command => <button key={command}
      disabled={!enabled || busy || !!review} onClick={() => void begin(command)}>{labels[command]}</button>)}</div>
    {message && (!review || collapsed) && <p role="status" className="operator-action-message">{message}</p>}
    {collapsed && <button onClick={() => setCollapsed(false)}>Review saved action</button>}
    {review && !collapsed && <dialog ref={dialog} aria-labelledby="operator-confirm-title" aria-describedby="operator-confirm-impact"
      onCancel={event => { event.preventDefault(); cancel(); }} className="operator-confirm">
      <h2 id="operator-confirm-title">{labels[review.command]}</h2>
      <div className="operator-confirm-body">
      <p id="operator-confirm-impact">{impacts[review.command]}</p>
      <dl><dt>Resource</dt><dd>{review.snapshot.resource}</dd><dt>Expected revision</dt><dd>{review.snapshot.revision}</dd>
        {Object.entries(review.snapshot.state).map(([key, value]) => <div className="operator-fact" key={key}><dt>{key}</dt><dd>{String(value ?? "Unknown")}</dd></div>)}
        {review.intent && <><dt>Audit action ID</dt><dd>{review.intent.actionId}</dd></>}</dl>
      {review.keys && <section aria-label="Affected credentials"><h3>Credential scope</h3>
        <p>All installations are affected; key lifecycle states remain unchanged.</p>
        {review.keys.items.length ? <ul>{review.keys.items.map(key => <li key={String(key.keyId)}>
          {key.keyId} | {key.installationId} | {key.state}</li>)}</ul> : <p>No keys in this snapshot</p>}
        {review.keys.nextCursor && <p>Additional keys exist beyond this bounded page; they are also in scope.</p>}
      </section>}
      <label htmlFor="operator-action-reason">Operator reason</label>
      <textarea id="operator-action-reason" maxLength={1000} required value={reason} disabled={busy || !!review.intent}
        onChange={event => setReason(event.target.value)} />
      {message && <p role="alert">{message}</p>}
      </div>
      <div className="operator-action-bar">
        <button disabled={busy} onClick={cancel}>{uncertain ? "Close without confirming outcome" : "Cancel"}</button>
        <button disabled={busy || (!review.intent && !reason.trim())} onClick={() => void submit()}>
          {busy ? "Submitting..." : uncertain ? "Retry saved action" : "Confirm action"}</button>
      </div>
    </dialog>}
  </section>;
}

export function VoteInspection({ contributorId, onAccessFailure }: { contributorId: string; onAccessFailure: (status: number) => void }) {
  const [revisionId, setRevisionId] = useState("");
  const [inspection, setInspection] = useState<OperatorInspection | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const inspect = async () => {
    pending.current?.abort(); const controller = new AbortController(); pending.current = controller;
    setBusy(true); setInspection(null); setMessage("");
    try {
      const value = await readOperatorInspection("vote", contributorId, controller.signal, revisionId);
      if (!controller.signal.aborted) setInspection(value);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof OperatorAccessError && [401, 403].includes(error.status)) onAccessFailure(error.status);
      else setMessage(error instanceof OperatorAccessError && error.status === 404 ? "Vote not found" : "Vote inspection unavailable");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <section aria-label="Vote inspection"><h3>Individual vote</h3>
    <form onSubmit={event => { event.preventDefault(); void inspect(); }}>
      <label htmlFor="operator-vote-revision">Revision ID</label>
      <input id="operator-vote-revision" value={revisionId} maxLength={128} onChange={event => {
        pending.current?.abort(); setBusy(false); setRevisionId(event.target.value); setInspection(null); setMessage("");
      }} />
      <button disabled={busy || !/^[A-Za-z0-9_.-]{1,128}$/.test(revisionId)}>Inspect vote</button>
    </form>
    {message && <p role="status">{message}</p>}
    {inspection && <><p>{inspection.state.value} | {inspection.state.excluded === 1 ? "Excluded" : "Included"}</p>
      <ModerationActions key={`${inspection.resource}:${inspection.revision}`} inspection={inspection} enabled={!busy}
        onAccessFailure={onAccessFailure} onApplied={() => void inspect()} />
      <h3>Vote audit</h3>{inspection.audit.length ? <ol>{inspection.audit.map(row => <li key={String(row.actionId)}>
        {row.command}: {row.outcome} | {row.reason} | {row.actionId}</li>)}</ol> : <p>No audit entries</p>}</>}
  </section>;
}