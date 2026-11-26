import { useRef, useState, type RefObject } from "react";
import type { SavedReviewSummary } from "../api/api-review-types";
import { recoverSavedReview } from "../api/api-reviews";
import { usePrincipal } from "../auth/auth-gate";
import { can } from "../auth/session";
import { ModalDialog } from "../ui/ModalDialog";

type RepairOption = "delete" | "recover";
type RepairState =
  | { kind: "choosing"; option: RepairOption | null }
  | { kind: "submitting"; option: RepairOption }
  | { kind: "failed"; option: RepairOption; reason: string };

export function ReviewRepairDialog({ review, onClose, onResolved, onDelete, returnFocusRef }: {
  review: SavedReviewSummary;
  onClose: () => void;
  onResolved: () => void;
  /** Continue in the review deletion dialog, which asks what happens to each published file. */
  onDelete: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const principal = usePrincipal();
  const repair = review.repair!;
  const [state, setState] = useState<RepairState>({ kind: "choosing", option: null });
  const submitting = useRef(false);
  const allowed = (own: "reviews.delete.own" | "reviews.save.own", other: "reviews.delete.other" | "reviews.edit.other") => (
    principal !== null && (can(principal, own) || can(principal, other))
  );
  const availability: Record<RepairOption, { enabled: boolean; reason: string }> = {
    delete: { enabled: allowed("reviews.delete.own", "reviews.delete.other"), reason: "You do not have permission to delete this review." },
    recover: { enabled: allowed("reviews.save.own", "reviews.edit.other"), reason: "You do not have permission to save or edit this review." },
  };
  const options: { id: RepairOption; label: string; description: string }[] = [
    { id: "delete", label: "Delete the review", description: "Next, choose whether to keep or remove each file published from it." },
    { id: "recover", label: "Recover the review", description: `Keep ${repair.recover.kept_cues} copied cues as manual cues. Remove ${repair.recover.removed_memberships} scan memberships and ${repair.recover.removed_accepted_credits} accepted credits references.` },
  ];
  const busy = state.kind === "submitting";
  const submit = async () => {
    const option = state.option;
    if (!option || !availability[option].enabled || submitting.current) return;
    if (option === "delete") { onDelete(); return; }
    submitting.current = true;
    setState({ kind: "submitting", option });
    try {
      await recoverSavedReview(review.id);
      onResolved();
    } catch (error) {
      setState({ kind: "failed", option, reason: error instanceof Error ? error.message : "Unable to resolve this review." });
    } finally { submitting.current = false; }
  };

  return <ModalDialog labelledBy="review-repair-heading" className="review-repair-dialog"
    onClose={busy ? undefined : onClose} returnFocusRef={returnFocusRef}>
    <h2 id="review-repair-heading">Resolve review</h2>
    <p><strong>{review.name}</strong></p>
    <p>{repair.message}</p>
    <fieldset className="review-repair-options" disabled={busy}>
      <legend>Choose how to resolve this review</legend>
      {options.map(({ id, label, description }) => <div className="review-repair-option" key={id}>
        <label>
          <input type="radio" name="review-repair-option" value={id} checked={state.option === id}
            disabled={!availability[id].enabled} aria-describedby={`repair-${id}-description${!availability[id].enabled ? ` repair-${id}-reason` : ""}`}
            onChange={() => setState({ kind: "choosing", option: id })} />
          <strong>{label}</strong>
        </label>
        <p id={`repair-${id}-description`}>{description}</p>
        {!availability[id].enabled && <p id={`repair-${id}-reason`} className="form-error">{availability[id].reason}</p>}
      </div>)}
    </fieldset>
    {state.kind === "failed" && <p className="form-error" role="alert">{state.reason}</p>}
    <div className="button-row">
      <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className={state.option === "delete" ? "danger-button" : "btn-primary"}
        disabled={busy || !state.option || !availability[state.option].enabled} onClick={() => void submit()}>
        {busy ? "Resolving..." : state.option === "delete" ? "Continue" : "Recover review"}
      </button>
    </div>
  </ModalDialog>;
}