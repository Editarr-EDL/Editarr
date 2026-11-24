import type { FilterValues } from "../api/api-community-master-types";
import { formatRange } from "./community-master-format";

type Bar = { key: "mine" | "old" | "new"; label: string; value: FilterValues | null };

/**
 * Mini timeline comparing old and new bounds (and the user's own values when edited) on one shared
 * scale padded around the union of the ranges. The text summary is the accessible description.
 */
export function MiniTimeline({ mine, oldMaster, newMaster }: {
  mine: FilterValues | null; oldMaster: FilterValues | null; newMaster: FilterValues | null;
}) {
  const bars: Bar[] = [
    { key: "mine", label: "Mine", value: mine },
    { key: "old", label: "Old", value: oldMaster },
    { key: "new", label: "New", value: newMaster },
  ];
  const present = bars.filter((bar): bar is Bar & { value: FilterValues } => bar.value !== null);
  if (!present.length) return null;
  const start = Math.min(...present.map((bar) => bar.value.startMs));
  const end = Math.max(...present.map((bar) => bar.value.endMs));
  const padding = Math.max(1000, (end - start) * 0.25);
  const from = Math.max(0, start - padding);
  const span = end + padding - from;
  const summary = present.map((bar) => `${bar.label} ${formatRange(bar.value)}`).join("; ");
  return <div className="master-timeline" role="img" aria-label={summary}>
    {present.map((bar) => <div className="master-timeline-track" key={bar.key}>
      <span className="master-timeline-label" aria-hidden="true">{bar.label}</span>
      <span className="master-timeline-lane" aria-hidden="true"><span className={`master-timeline-bar master-timeline-${bar.key}`}
        style={{ left: `${(bar.value.startMs - from) / span * 100}%`,
          width: `${Math.max(0.5, (bar.value.endMs - bar.value.startMs) / span * 100)}%` }} /></span>
      <span className="master-timeline-range muted-copy" aria-hidden="true">{formatRange(bar.value)}</span>
    </div>)}
  </div>;
}
