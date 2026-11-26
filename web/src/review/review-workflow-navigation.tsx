import { useRef, type KeyboardEvent } from "react";
import type { GuidedReviewWorkspace, WorkflowStep } from "./review-workflow-types";

const steps: ReadonlyArray<{ value: WorkflowStep; label: string }> = [
  { value: "community", label: "Community" },
  { value: "scans", label: "Scans" },
  { value: "review", label: "Review" },
  { value: "subtitles", label: "Subtitles" },
  { value: "publish", label: "Publish" },
];

export function ReviewWorkflowNavigation({ step, onStepChange }: {
  step: WorkflowStep;
  onStepChange: (step: WorkflowStep) => void;
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const navigate = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    switch (event.key) {
      case "ArrowRight": next = (index + 1) % steps.length; break;
      case "ArrowLeft": next = (index + steps.length - 1) % steps.length; break;
      case "Home": next = 0; break;
      case "End": next = steps.length - 1; break;
      default: return;
    }
    event.preventDefault();
    onStepChange(steps[next].value);
    buttons.current[next]?.focus();
  };

  return <div className="review-workflow-tabs" role="tablist" aria-label="Library item workflow">
    {steps.map(({ value, label }, index) => <button key={value} type="button"
      id={`workflow-${value}-tab`} role="tab" aria-selected={step === value}
      aria-controls={`workflow-${value}-panel`} tabIndex={step === value ? 0 : -1}
      ref={(node) => { buttons.current[index] = node; }}
      onKeyDown={(event) => navigate(event, index)} onClick={() => onStepChange(value)}>
      {label}
    </button>)}
  </div>;
}

/** Choose a saved review without leaving the current workflow step. */
export function SavedReviewPicker({ workspace }: { workspace: GuidedReviewWorkspace }) {
  const { reviews, selected, selectReview, busy } = workspace;
  const hasSelectableReviews = reviews.some((review) => !review.repair);
  return <label className="field workflow-review-picker">
    <span>Saved review</span>
    <select value={selected?.id ?? ""} disabled={busy || !hasSelectableReviews}
      onChange={(event) => { if (event.target.value) void selectReview(event.target.value); }}>
      <option value="" disabled>{hasSelectableReviews ? "Choose a review" : "No saved review available"}</option>
      {reviews.map((review) => <option key={review.id} value={review.id} disabled={Boolean(review.repair)}>
        {review.name}{review.repair ? " (needs repair)" : ""}
      </option>)}
    </select>
  </label>;
}
