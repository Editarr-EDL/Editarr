import { useEffect, useId, useState } from "react";
import type { ReviewFilter } from "../api/api-review-types";
import { formatReviewTimestamp, parseReviewTimestamp } from "./review-timestamps";

type FilterTiming = Pick<ReviewFilter, "start_seconds" | "end_seconds">;
type Endpoint = "start_seconds" | "end_seconds";
type TimingText = Partial<Record<Endpoint, string>>;

const timestampFormat = "HH:MM:SS.SS";

/** Edit a cue's endpoints together so incomplete text never reaches the review draft. */
export function ReviewFilterTimeFields({ controlId, value, onChange, onInvalidChange, requireEnd = false }: {
  controlId: string;
  value: FilterTiming;
  onChange: (timing: FilterTiming) => void;
  onInvalidChange: (invalid: boolean) => void;
  requireEnd?: boolean;
}) {
  const [edits, setEdits] = useState<TimingText>({});
  const errorId = useId();
  useEffect(() => () => onInvalidChange(false), [onInvalidChange]);

  const display = (endpoint: Endpoint) => formatReviewTimestamp(value[endpoint], 2);
  const parsed = (text: TimingText, endpoint: Endpoint) => {
    const edited = text[endpoint];
    if (edited === undefined || edited === display(endpoint)) return value[endpoint];
    if (edited === "" && endpoint === "end_seconds" && !requireEnd) return null;
    return parseReviewTimestamp(edited, 2);
  };
  const errors = (text: TimingText) => {
    const start = parsed(text, "start_seconds");
    const end = parsed(text, "end_seconds");
    return {
      start: start === null || !Number.isFinite(start),
      end: end === null ? requireEnd : !Number.isFinite(end),
      range: start !== null && end !== null && end <= start,
    };
  };
  const update = (endpoint: Endpoint, text: string) => {
    const pending = { ...edits, [endpoint]: text };
    const { start, end, range } = errors(pending);
    const invalid = start || end || range;
    onInvalidChange(invalid);
    setEdits(invalid ? pending : {});
    if (invalid) return;
    const startSeconds = parsed(pending, "start_seconds");
    const endSeconds = parsed(pending, "end_seconds");
    if (startSeconds !== null && (startSeconds !== value.start_seconds || endSeconds !== value.end_seconds)) {
      onChange({ start_seconds: startSeconds, end_seconds: endSeconds });
    }
  };
  const { start, end, range } = errors(edits);
  const field = (endpoint: Endpoint, label: "Start time" | "End time", invalid: boolean) => <label className="field">
    <span>{label}</span>
    <input type="text" aria-label={`${controlId} ${label.toLowerCase()}`} placeholder={timestampFormat}
      aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : undefined}
      value={edits[endpoint] ?? display(endpoint)}
      onChange={(event) => update(endpoint, event.target.value)} />
  </label>;
  return <>
    <div className="finding-timing-fields">
      {field("start_seconds", "Start time", start || range)}
      {field("end_seconds", "End time", end || range)}
    </div>
    {(start || end || range) && <p id={errorId} className="form-error">
      {start || end ? `Use ${timestampFormat} for the time.` : "End time must be after start time."}
    </p>}
  </>;
}
