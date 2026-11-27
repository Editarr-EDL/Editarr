import type { ScanCapabilities } from "../api/api-system-types";
import { buildModelOptions } from "./model-catalog";
import { InfoButton } from "../ui/InfoButton";
import { ModelPicker } from "./model-picker";

export const SEVERITY_MODEL_HELP = "Describes flagged scenes and rates their severity when the video model doesn't.";

/** Choose the Severity and Description model (API field `qwen_model`) used for summaries and language analysis. */
export function QwenModelField({
  capabilities,
  value,
  onChange,
}: {
  capabilities: ScanCapabilities | null;
  value: string;
  onChange: (value: string) => void;
}) {
  return <ModelPicker label="Severity and Description model" options={buildModelOptions("severity", capabilities)}
    value={value} onChange={onChange}
    info={<InfoButton label="About the Severity and Description model"><strong>Severity and description</strong><span>{SEVERITY_MODEL_HELP}</span></InfoButton>} />;
}

const AUDIO_LANGUAGE_HELP = "The Qwen filter checks the transcript for profanity and blasphemy across languages. Other filters detect expressions first; Qwen only assigns their categories and severity. It may download this model. Without Qwen, coverage depends on the selected filter and your Deny words; detected words remain Swearing / Medium.";

/** Filter help: Qwen's multilingual coverage and the fallback without it. */
export function AudioLanguageHelp() {
  return <InfoButton label="About filters"><strong>Language coverage</strong><span>{AUDIO_LANGUAGE_HELP}</span></InfoButton>;
}
