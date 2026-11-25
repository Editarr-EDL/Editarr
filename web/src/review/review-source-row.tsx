import { useId, type ReactNode } from "react";

export type ReviewSourceKind = "audio" | "video" | "community";

const icons: Record<ReviewSourceKind, string> = { audio: "🎙", video: "🎞", community: "👥" };

/**
 * One source in the Included scans popover: its include checkbox, type icon, name, a muted details line, and
 * any states or actions for it underneath.
 * @param describedBy Ids of status text that also describes the checkbox.
 */
export function ReviewSourceRow({ kind, name, details, included, disabled, describedBy, onChange, children, checkboxRef, info }: {
  kind: ReviewSourceKind; name: string; details: string; included: boolean; disabled: boolean;
  /** A details button shown after the name. */
  info?: ReactNode;
  describedBy?: string; onChange: (included: boolean) => void; children?: ReactNode;
  checkboxRef?: (element: HTMLInputElement | null) => void;
}) {
  const id = useId();
  return <div className={`review-source-row${included ? "" : " is-excluded"}`}>
    <input ref={checkboxRef} id={id} type="checkbox" checked={included} disabled={disabled}
      aria-describedby={[`${id}-details`, describedBy].filter(Boolean).join(" ")}
      onChange={(event) => onChange(event.target.checked)} />
    <span className={`review-source-icon ${kind}`} aria-hidden="true">{icons[kind]}</span>
    <span className="review-source-text">
      <span className="review-source-title"><label htmlFor={id} className="review-source-name">{name}</label>{info}</span>
      <span id={`${id}-details`} className="review-source-details">{details}</span>
    </span>
    {children && <div className="review-source-extra">{children}</div>}
  </div>;
}
