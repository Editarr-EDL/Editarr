import type { ScanCapabilities } from "../api/api-system-types";

/** Select the Qwen model shared by visual summaries and language analysis. */
export function QwenModelField({
  capabilities,
  value,
  onChange,
}: {
  capabilities: ScanCapabilities | null;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>Qwen model</span>
      <select
        aria-label="Qwen model"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {(capabilities?.qwen_models || []).map((model) => (
          <option key={model.id} value={model.id}>{model.display_name}</option>
        ))}
      </select>
    </label>
  );
}

/** Describe audio discovery and its fallback consistently across scan forms. */
export function AudioLanguageHelp() {
  return <p className="muted-copy">The Qwen filter checks the transcript for profanity and blasphemy across languages. Other filters detect expressions first; Qwen only assigns their categories and severity. It may download this model. Without Qwen, coverage depends on the selected filter and your Deny words; detected words remain Swearing / Medium.</p>;
}
