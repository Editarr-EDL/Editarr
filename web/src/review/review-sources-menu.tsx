import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { Scan } from "../api/api-scan-types";
import type { SavedReview } from "../api/api-review-types";
import { scanLabel, scanOriginLabel } from "../shared/display";
import type { ReviewDraft } from "./review-draft";
import type { CommunityEntry } from "./review-community";
import { communityEntryChanged, ReviewCommunityEntries } from "./review-community-sources";
import { ReviewSourceRow } from "./review-source-row";
import { ReviewScanInfo, reviewScanName } from "./review-scan-source";

type Role = SavedReview["memberships"][number]["role"];

function shortDate(value: string): string {
  const date = new Date(value);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

/** A scan's muted details line: its type, models (or origin), filter count and date. */
export function reviewScanDetails(scan: Scan): string {
  const type = scan.scan_audio && scan.scan_video ? "Audio and video" : scan.scan_audio ? "Audio" : "Video";
  const models = (scan.origin ?? "generated") === "generated"
    ? [scan.scan_audio && (scan.audio_model || "whisper"), scan.scan_video && scan.video_model].filter(Boolean).join(" · ")
    : scanOriginLabel(scan, { includeTime: false });
  const count = typeof scan.finding_count === "number" && `${scan.finding_count} ${scan.finding_count === 1 ? "filter" : "filters"}`;
  const timestamp = (scan.origin === "imported" ? scan.imported_at : scan.scan_started_at) || scan.created_at;
  return [type, models, count, timestamp && shortDate(timestamp)].filter(Boolean).join(" · ");
}

/**
 * The Filters toolbar's "Sources · N of M" button and its Included scans popover: every completed scan and
 * community filter set with its include checkbox, details, states and actions. A dot marks included community
 * filters that changed since they were included. Changes edit the draft and apply when the review is saved.
 */
export function ReviewSourcesMenu({ scans, draft, entries, maySearch, disabled, onToggleScan, onChangeRole, onChange, votesFailed, onRetryVotes }: {
  scans: Scan[]; draft: ReviewDraft; entries: CommunityEntry[]; maySearch: boolean; disabled: boolean;
  onToggleScan: (scan: Scan, included: boolean) => void;
  onChangeRole: (scan: Scan, role: Role) => void;
  onChange: (draft: ReviewDraft) => void;
  votesFailed?: (linkId: string | null) => boolean; onRetryVotes?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const popoverId = useId();
  const membership = (scan: Scan) => draft.memberships.find((value) => value.scan_id === scan.id);
  const included = scans.filter(membership).length + entries.filter((entry) => entry.included).length;
  const total = scans.length + entries.length;
  const changed = entries.some(communityEntryChanged);
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return;
    const element = popover.current;
    const first = element?.querySelector<HTMLElement>("input:not(:disabled), select:not(:disabled), button:not(:disabled)");
    (first ?? element)?.focus({ preventScroll: true });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element;
      if (popover.current?.contains(target) || trigger.current?.contains(target)) return;
      // Clicking another control moves focus there; clicking elsewhere returns it to the button.
      close(!target.closest?.("button, a, input, select, textarea, label, [tabindex]"));
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  return <span className="review-sources">
    <button ref={trigger} type="button" className="btn-secondary compact-button review-sources-button"
      aria-label={`Sources · ${included} of ${total}${changed ? ", community filters changed" : ""}`}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? popoverId : undefined}
      onClick={() => (open ? close() : setOpen(true))}>
      Sources · {included} of {total}
      {changed && <span className="review-sources-dot" title="Community filters changed" />}
      <span className="review-sources-caret" aria-hidden="true">▾</span>
    </button>
    {open && <div ref={popover} id={popoverId} className="review-sources-popover" role="dialog" aria-label="Included scans" tabIndex={-1}
      onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); event.stopPropagation(); close(); } }}
      onBlur={(event) => {
        const next = event.relatedTarget as Node | null;
        if (next && !popover.current?.contains(next) && !trigger.current?.contains(next)) close(false);
      }}>
      <div className="review-sources-list">
        {scans.map((scan) => {
          const current = membership(scan);
          return <ReviewSourceRow key={scan.id} kind={scan.scan_audio && !scan.scan_video ? "audio" : "video"}
            name={reviewScanName(scan)} details={reviewScanDetails(scan)} included={!!current} disabled={disabled}
            info={<ReviewScanInfo scans={[scan]} />}
            onChange={(value) => onToggleScan(scan, value)}>
            {current && scan.scan_audio && scan.scan_video && <label className="review-source-role">
              <span>Role</span>
              <select aria-label={`Review role for ${scanLabel(scan)}`} value={current.role} disabled={disabled}
                onChange={(event) => onChangeRole(scan, event.target.value as Role)}>
                <option value="audio">Audio only</option>
                <option value="video">Video only</option>
                <option value="both">Audio and video</option>
              </select>
            </label>}
          </ReviewSourceRow>;
        })}
        <ReviewCommunityEntries entries={entries} draft={draft} maySearch={maySearch} disabled={disabled} onChange={onChange}
          votesFailed={votesFailed} onRetryVotes={onRetryVotes} />
        {!total && <p className="muted-copy review-sources-empty">No completed scans or community filters for this version yet.</p>}
      </div>
      <div className="review-sources-footer">
        <span>Changes apply when you save the review.</span>
        <button type="button" className="btn-secondary compact-button" onClick={() => close()}>Done</button>
      </div>
    </div>}
  </span>;
}
