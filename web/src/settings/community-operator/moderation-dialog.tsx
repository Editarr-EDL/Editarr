import { useEffect, useRef, useState, type ReactNode } from "react";
import { readOperatorInspection, readOperatorPage, OperatorMutationError,
  type OperatorInspection, type OperatorPage, type OperatorResource } from "../../api/api-community-operator";
import { writeOperatorAction, writeOperatorRecompute, type OperatorIntent,
  type RecomputeIntent } from "../../api/api-community-operator-actions";
import { reasonText } from "../../api/api-community-operator-checks";
import { OperatorAccessError } from "./runtime";
import { actionImpacts, actionLabel, commandsFor, parseResource, type OperatorActionKind } from "./moderation-commands";
import { ActionExtras, extrasFor, type ExtrasDraft } from "./action-extras";

export interface ModerationTarget { kind: OperatorResource; targetId: string; contributorId?: string }
export interface ModerationStart { revision?: number; extras?: ExtrasDraft }
type Review = { command: OperatorActionKind; snapshot: OperatorInspection; intent: OperatorIntent | RecomputeIntent | null; keys: OperatorPage | null };

/**
 * Reasoned moderation for any resource. `begin` re-inspects the target, refuses when its state changed (or its
 * revision differs from `start.revision`), then opens a confirmation dialog; render `view` once where messages belong.
 */
export function useModeration({ onAccessFailure, onApplied }: { onAccessFailure: (status: number) => void; onApplied: () => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [reason, setReason] = useState("");
  const [extras, setExtras] = useState<ExtrasDraft>({});
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
  const begin = async ({ kind, targetId: id, contributorId }: ModerationTarget, command: OperatorActionKind, start: ModerationStart = {}) => {
    if (lock.current || review) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    lock.current = true; setBusy(true); setMessage("");
    const controller = new AbortController(); requests.current.add(controller);
    try {
      const snapshot = await readOperatorInspection(kind, id, controller.signal, contributorId);
      const keys = kind === "contributor" ? await readOperatorPage("keys", null, controller.signal, id) : null;
      if (!active.current) return;
      if ((start.revision !== undefined && snapshot.revision !== start.revision) || !commandsFor(snapshot).includes(command)) {
        setMessage("State changed. Refresh and review the resource before starting a new action."); onApplied(); return;
      }
      setReason(""); setExtras(start.extras ?? {}); setUncertain(false); setCollapsed(false); setReview({ command, snapshot, intent: null, keys });
    } catch (error) {
      if (active.current && !accessFailure(error)) setMessage("Fresh inspection unavailable. No action sent.");
    } finally { requests.current.delete(controller); lock.current = false; if (active.current) setBusy(false); }
  };
  const submit = async () => {
    if (!review || lock.current || (!review.intent && (!reasonText(reason) || !extrasFor(review.command, extras)))) return;
    lock.current = true; setBusy(true); setMessage("");
    const { kind, targetId, contributorId } = parseResource(review.snapshot.resource);
    const actionId = crypto.randomUUID();
    const intent = review.intent ?? (review.command === "recompute"
      ? { version: 1 as const, actionId, versionIds: [targetId], reason }
      : { version: 1 as const, actionId, command: review.command, resource: kind, targetId,
        ...(contributorId ? { contributorId } : {}), expectedRevision: review.snapshot.revision, reason, ...extrasFor(review.command, extras) });
    setReview({ ...review, intent });
    const controller = new AbortController(); requests.current.add(controller);
    try {
      let applied: string;
      if ("versionIds" in intent) {
        const result = await writeOperatorRecompute(intent, controller.signal);
        const outcome = "versions" in result ? result.versions[0] : null;
        applied = `Recompute recorded. Audit ID: ${intent.actionId}.${outcome ? ` Master revision ${outcome.revision} (${outcome.changed ? "changed" : "unchanged"}).` : ""}`;
      } else applied = `Action applied. Audit ID: ${(await writeOperatorAction(intent, controller.signal)).actionId}.`;
      if (!active.current) return;
      setReview(null); setUncertain(false);
      setMessage(`${applied} Refreshing current state...`); onApplied();
    } catch (error) {
      if (!active.current || accessFailure(error)) return;
      if (error instanceof OperatorAccessError && (error.status === 409 || error.status === 400 || error.status === 404 ||
        (error instanceof OperatorMutationError && error.code === "moderation_ineligible"))) {
        setReview(null); setUncertain(false);
        const code = error instanceof OperatorMutationError ? error.code : "";
        setMessage(code === "moderation_conflict" || (error.status === 409 && !code) ? "State conflict. Refresh and review before starting a new action." :
          `Action refused${code ? ` (${code})` : ""}. Refresh and review resource eligibility; no successful action is confirmed.`); onApplied();
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
  const kind = review ? parseResource(review.snapshot.resource).kind : null;
  const view: ReactNode = <>
    {message && (!review || collapsed) && <p role="status" className="operator-action-message">{message}</p>}
    {collapsed && <button className="btn sm" onClick={() => setCollapsed(false)}>Review saved action</button>}
    {review && kind && !collapsed && <dialog ref={dialog} aria-labelledby="operator-confirm-title" aria-describedby="operator-confirm-impact"
      onCancel={event => { event.preventDefault(); cancel(); }} className="operator-confirm">
      <h2 id="operator-confirm-title">{actionLabel(review.command, kind)}</h2>
      <div className="operator-confirm-body">
      <p id="operator-confirm-impact">{actionImpacts[review.command]}</p>
      <dl><dt>Resource</dt><dd>{review.snapshot.resource}</dd><dt>Expected revision</dt><dd>{review.snapshot.revision}</dd>
        {Object.entries(review.snapshot.state).map(([key, value]) => <div className="operator-fact" key={key}><dt>{key}</dt><dd>{String(value ?? "Unknown")}</dd></div>)}
        {review.intent && <><dt>Audit action ID</dt><dd>{review.intent.actionId}</dd></>}</dl>
      <ActionExtras command={review.command} value={extras} onChange={setExtras} disabled={busy || !!review.intent} />
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
        <button className="btn" disabled={busy} onClick={cancel}>{uncertain ? "Close without confirming outcome" : "Cancel"}</button>
        <button className="btn primary" disabled={busy || (!review.intent && (!reason.trim() || !extrasFor(review.command, extras)))} onClick={() => void submit()}>
          {busy ? "Submitting..." : uncertain ? "Retry saved action" : "Confirm action"}</button>
      </div>
    </dialog>}
  </>;
  return { begin, busy, locked: busy || !!review, view };
}
