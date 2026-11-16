import { useRef, useState, type RefObject } from "react";
import type { SavedReviewSummary } from "../api/api-review-types";
import { recoverSavedReview, redownloadReviewCommunityScan } from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { usePrincipal } from "../auth/auth-gate";
import { can } from "../auth/session";
import { ModalDialog } from "../ui/ModalDialog";

type RepairOption = "delete" | "recover" | "community";
type Availability = Record<RepairOption, { enabled: boolean; reason: string | null }>;
type RepairState = (
  | { kind: "choosing"; option: RepairOption | null }
  | { kind: "submitting"; option: RepairOption }
  | { kind: "failed"; option: RepairOption; reason: string }
) & { unavailableReason: string | null };

const unavailableMessage = "The missing scan could not be retrieved from the community. Choose one of the remaining options.";
const reasonMessages: Record<string, string> = {
  not_connected: "Your account is not connected to the community service.",
  identity_unavailable: "Your community credentials are unavailable.",
  identity_changed: "Your community credentials changed. Try again.",
  rotation_pending: "Finish updating your community credentials before trying again.",
  wrong_service: "This scan belongs to a different community service.",
  offline: "The community service is currently unreachable.",
  candidate_unavailable: "The scan is no longer offered for this media file.",
  artifact_unavailable: "The scan is no longer available.",
  contribution_not_found: "The community contribution could not be found.",
  contribution_unavailable: "The community contribution is no longer available.",
  revision_not_available: "This scan revision is no longer available.",
  historical_key_untrusted: "The scan's signing key is no longer trusted.",
  invalid_artifact: "The downloaded scan could not be validated.",
  invalid_artifact_signature: "The scan's signature could not be verified.",
  digest_mismatch: "The downloaded scan does not match the saved scan.",
  mapping_mismatch: "The scan's saved timing could not be restored.",
  incompatible_artifact: "The scan is not compatible with this media file.",
  invalid_channels: "The scan does not contain the required audio or video channels.",
  community_identity_unavailable: "The missing scan has no recoverable community identity.",
  target_changed: "The media file changed. Reopen the review before trying again.",
};
const reasonMessage = (code: string | null, fallback: string) => (code && reasonMessages[code]) || fallback;

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
  const [state, setState] = useState<RepairState>({
    kind: "choosing", option: null,
    unavailableReason: repair.community?.unavailable_reason
      ? reasonMessage(repair.community.unavailable_reason, repair.community.unavailable_reason) : null,
  });
  const [confirmationRequired, setConfirmationRequired] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const submitting = useRef(false);
  const allowed = (own: "reviews.delete.own" | "reviews.save.own", other: "reviews.delete.other" | "reviews.edit.other") => (
    principal !== null && (can(principal, own) || can(principal, other))
  );
  const editable = allowed("reviews.save.own", "reviews.edit.other");
  const downloadable = principal !== null && can(principal, "community.download");
  const communityUnavailable = state.unavailableReason !== null || repair.community?.available === false;
  const availability: Availability = {
    delete: { enabled: allowed("reviews.delete.own", "reviews.delete.other"), reason: "You do not have permission to delete this review." },
    recover: { enabled: editable, reason: "You do not have permission to save or edit this review." },
    community: {
      enabled: editable && downloadable && !communityUnavailable,
      reason: communityUnavailable
        ? `${unavailableMessage}${state.unavailableReason ? ` ${state.unavailableReason}` : ""}`
        : !editable ? "You do not have permission to save or edit this review."
          : !downloadable ? "You do not have permission to download community scans." : null,
    },
  };
  const options: { id: RepairOption; label: string; description: string }[] = [
    { id: "delete", label: "Delete the review", description: "Next, choose whether to keep or remove each file published from it." },
    { id: "recover", label: "Recover the review", description: `Keep ${repair.recover.kept_cues} copied cues as manual cues. Remove ${repair.recover.removed_memberships} scan memberships and ${repair.recover.removed_accepted_credits} accepted credits references.` },
    ...(repair.community ? [{ id: "community" as const, label: "Re-download from the community", description: "Retrieve the missing scan and restore its review references." }] : []),
  ];
  const busy = state.kind === "submitting";
  const submit = async () => {
    const option = state.option;
    if (!option || !availability[option].enabled || submitting.current
      || (option === "community" && confirmationRequired && !confirmed)) return;
    if (option === "delete") { onDelete(); return; }
    submitting.current = true;
    setState({ ...state, kind: "submitting", option });
    try {
      if (option === "recover") await recoverSavedReview(review.id);
      else if (confirmed) await redownloadReviewCommunityScan(review.id, true);
      else await redownloadReviewCommunityScan(review.id);
      onResolved();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to resolve this review.";
      const reason = error instanceof ApiError ? reasonMessage(error.reason, message) : message;
      if (option === "community") {
        if (error instanceof ApiError && error.reason === "explicit_review_required") {
          setConfirmationRequired(true);
          setConfirmed(false);
          setState({ kind: "choosing", option: "community", unavailableReason: null });
          return;
        }
        const storedReason = error instanceof ApiError ? error.repair?.community?.unavailable_reason : null;
        setState({ kind: "choosing", option: null, unavailableReason: storedReason ? reasonMessage(storedReason, message) : reason });
      } else setState({ ...state, kind: "failed", option, reason });
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
            onChange={() => setState({ kind: "choosing", option: id, unavailableReason: state.unavailableReason })} />
          <strong>{label}</strong>
        </label>
        <p id={`repair-${id}-description`}>{description}</p>
        {!availability[id].enabled && <p id={`repair-${id}-reason`} className="form-error" role={id === "community" && state.unavailableReason ? "status" : undefined}>{availability[id].reason}</p>}
      </div>)}
      {confirmationRequired && <label>
        <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        I will review this scan's uncertain compatibility before using it.
      </label>}
    </fieldset>
    {state.kind === "failed" && <p className="form-error" role="alert">{state.reason}</p>}
    <div className="button-row">
      <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className={state.option === "delete" ? "danger-button" : "btn-primary"}
        disabled={busy || !state.option || !availability[state.option].enabled
          || (state.option === "community" && confirmationRequired && !confirmed)} onClick={() => void submit()}>
        {busy ? "Resolving..." : state.option === "delete" ? "Continue" : state.option === "community" ? "Re-download" : "Recover review"}
      </button>
    </div>
  </ModalDialog>;
}