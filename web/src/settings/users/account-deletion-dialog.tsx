import { useEffect, useReducer, useState } from "react";
import { deleteAccount, previewAccountDeletion, type JellyfinReturn } from "../../api/api-account-deletion";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import { ModalDialog } from "../../ui/ModalDialog";
import { resultLines } from "./account-deletion-copy";
import { DispositionPicker } from "./account-deletion-disposition";
import { answered, deletionFlow, isPasswordRefusal, useStepFocus } from "./account-deletion-flow";
import { DeletionImpact } from "./account-deletion-impact";
import { JellyfinReturnChoice } from "./account-deletion-jellyfin";
import { appliedOutcome, unknownOutcome, type DeletionOutcome } from "./account-deletion-outcomes";

export interface DeletionTarget { id: number; name: string }

/**
 * Choose a disposition for one account, review MU-38's preview, and apply exactly that
 * preview with fresh authentication. A stale or newly blocked preview is fetched again
 * and must be reviewed before another confirmation. `onDeleted` receives a deletion and an
 * unknown outcome (the answer never arrived), so the caller can reread account state.
 */
export function AccountDeletionDialog({ target, destination, onClose, onDeleted }: {
  target: DeletionTarget;
  destination?: DeletionTarget;
  onClose: () => void;
  onDeleted: (outcome: DeletionOutcome) => void;
}) {
  const [flow, dispatch] = useReducer(deletionFlow, { step: "choose", notice: null, mode: null });
  const [confirming, setConfirming] = useState(false);
  const [jellyfinUser, setJellyfinUser] = useState<JellyfinReturn>("allow");
  const stepFocus = useStepFocus(flow.step);
  const loading = flow.step === "loading" ? flow : null;
  useEffect(() => {
    if (!loading) return;
    const controller = new AbortController();
    void previewAccountDeletion(target.id, loading.disposition, controller.signal)
      .then(preview => { if (!controller.signal.aborted) dispatch({ type: "previewed", preview }); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) dispatch({ type: "refused", reason }); });
    return () => controller.abort();
  }, [loading, target.id]);
  const headingId = `account-deletion-heading-${target.id}`;
  return <>
    <ModalDialog labelledBy={headingId} className="account-dialog deletion-dialog" onClose={confirming ? undefined : onClose}>
      <div className="section-heading"><h2 id={headingId}>Delete {target.name}</h2><button type="button" className="btn-secondary" onClick={onClose}>Close</button></div>
      <div ref={stepFocus} tabIndex={-1} className="deletion-step">
      {flow.step === "choose" && <DispositionPicker excluded={[target.id]} initial={{ mode: flow.mode ?? undefined, destination }} notice={flow.notice} onReview={disposition => dispatch({ type: "preview", disposition })} onCancel={onClose} />}
      {flow.step === "loading" && <>{flow.notice && <p role="alert" className="form-status">{flow.notice}</p>}<p role="status">Checking what this changes...</p></>}
      {flow.step === "review" && <section className="deletion-review" aria-label="Deletion impact">
        {flow.notice && <p role="alert" className="form-status">{flow.notice}</p>}
        <DeletionImpact preview={flow.preview} disposition={flow.disposition} name={target.name} choices={flow.choices} onChoice={(exportId, choice) => dispatch({ type: "choice", exportId, choice })} />
        {flow.preview.credentials.linked && <JellyfinReturnChoice value={jellyfinUser} onChange={setJellyfinUser} />}
        <div className="button-row">
          <button type="button" className="btn-secondary" onClick={() => dispatch({ type: "choose" })}>Change choice</button>
          {flow.preview.blockers.length === 0 && <button type="button" className="danger-button" onClick={() => setConfirming(true)}>Delete account</button>}
        </div>
      </section>}
      {flow.step === "done" && <section className="deletion-result" aria-label="Deletion result">
        <p role="status">{target.name} was deleted.</p>
        <ul>{resultLines(flow.result, flow.disposition.mode === "transfer" ? flow.disposition.destination.name : null).map(line => <li key={line}>{line}</li>)}</ul>
        <div className="button-row"><button type="button" className="btn-primary" onClick={onClose}>Done</button></div>
      </section>}
      {flow.step === "stopped" && <section className="deletion-stopped" aria-label="Deletion stopped">
        <p role="alert" className="form-status">{flow.message}</p>
        <div className="button-row"><button type="button" className="btn-secondary" onClick={onClose}>Close</button></div>
      </section>}
      </div>
    </ModalDialog>
    {confirming && flow.step === "review" && <FreshAuthenticationDialog title={`Confirm deleting ${target.name}`}
      body={`Editarr applies exactly the impact you reviewed. If anything changed since, nothing happens and you review it again.${flow.preview.credentials.linked && jellyfinUser === "block" ? " Their Jellyfin user stays blocked until the owner unblocks them." : ""}`}
      actionLabel="Delete account" danger
      onClose={() => setConfirming(false)}
      onConfirm={async password => {
        try {
          const result = await deleteAccount({ userId: target.id, disposition: flow.disposition, preview: flow.preview, choices: flow.choices, jellyfinUser: flow.preview.credentials.linked ? jellyfinUser : "allow" }, password);
          dispatch({ type: "deleted", result });
          onDeleted(appliedOutcome(target.name, result, flow.disposition.mode === "transfer" ? flow.disposition.destination.name : null));
        } catch (reason) {
          if (isPasswordRefusal(reason)) throw reason;
          if (answered(reason)) dispatch({ type: "refused", reason });
          else {
            const outcome = unknownOutcome(target);
            dispatch({ type: "unconfirmed", message: outcome.lines[0] });
            onDeleted(outcome);
          }
        }
        setConfirming(false);
      }} />}
  </>;
}
