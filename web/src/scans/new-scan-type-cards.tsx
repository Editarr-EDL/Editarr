import type { ReactNode } from "react";
import type { NewScanType } from "./use-new-scan";

const iconPaths: Record<NewScanType, ReactNode> = {
  audio: <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
  video: <><rect x="3" y="6" width="13" height="12" rx="2" /><path d="M16 10l5-3v10l-5-3" /></>,
  combined: <><rect x="2.5" y="7" width="11" height="10" rx="2" /><path d="M13.5 10.5l4-2.5v8l-4-2.5" /><path d="M20 9.5c1 .8 1.5 1.6 1.5 2.5s-.5 1.7-1.5 2.5" /></>,
  credits: <path d="M4 5h16M7 9.5h10M5 14h14M8 18.5h8" />,
};

/** Scan type choices; `name` is the radio's accessible name, `label` its visible title. */
export const scanTypeChoices: Array<{ value: NewScanType; name: string; label: string; description: string }> = [
  { value: "audio", name: "Audio", label: "Audio", description: "Transcribe dialogue and flag language" },
  { value: "video", name: "Video", label: "Video", description: "Detect visual content scene by scene" },
  { value: "combined", name: "Audio and video", label: "Audio + video", description: "Both passes in one queued scan" },
  { value: "credits", name: "Credits", label: "Credits", description: "Find where closing credits start" },
];

/** Line icon shared by the scan type cards and section headings. */
export function ScanTypeIcon({ type }: { type: NewScanType }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{iconPaths[type]}</svg>;
}

/** Radio cards choosing the scan type; the group is named "Scan type". */
export function NewScanTypeCards({ value, disabled, onChange }: {
  value: NewScanType;
  disabled: boolean;
  onChange: (value: NewScanType) => void;
}) {
  return <fieldset className="new-scan-types" disabled={disabled}>
    <legend>Scan type</legend>
    <div className="new-scan-type-grid">
      {scanTypeChoices.map((choice) => <label key={choice.value} className="new-scan-type-card">
        <input type="radio" name="new-scan-type" value={choice.value} aria-label={choice.name}
          aria-describedby={`new-scan-type-${choice.value}`} checked={value === choice.value}
          onChange={() => onChange(choice.value)} />
        <span className="new-scan-type-check" aria-hidden="true" />
        <span className="new-scan-icon" aria-hidden="true"><ScanTypeIcon type={choice.value} /></span>
        <strong aria-hidden="true">{choice.label}</strong>
        <small id={`new-scan-type-${choice.value}`}>{choice.description}</small>
      </label>)}
    </div>
  </fieldset>;
}
