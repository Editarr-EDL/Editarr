import { useId } from "react";
import type { ScanCapabilities } from "../api/api-system-types";
import { ModelOptions } from "./model-options";

/** Select the Severity and Description model (API field `qwen_model`) used for summaries and language analysis. */
export function QwenModelField({
  capabilities,
  value,
  onChange,
}: {
  capabilities: ScanCapabilities | null;
  value: string;
  onChange: (value: string) => void;
}) {
  const helpId = useId();
  return (
    <>
      <label className="field">
        <span>Severity and Description model</span>
        <select
          aria-label="Severity and Description model"
          aria-describedby={helpId}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          <ModelOptions models={capabilities?.qwen_models} />
        </select>
      </label>
      <p className="muted-copy" id={helpId}>Describes flagged scenes and rates their severity when the video model doesn't.</p>
    </>
  );
}

/** Describe audio discovery and its fallback consistently across scan forms. */
export function AudioLanguageHelp() {
  return <p className="muted-copy">The Qwen filter checks the transcript for profanity and blasphemy across languages. Other filters detect expressions first; Qwen only assigns their categories and severity. It may download this model. Without Qwen, coverage depends on the selected filter and your Deny words; detected words remain Swearing / Medium.</p>;
}
