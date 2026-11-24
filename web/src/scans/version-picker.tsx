import { useEffect, useId, useState } from "react";
import { fetchVersionAliases } from "../api/api-community-masters";
import type { AliasChoice } from "../api/api-community-master-types";
import { OTHER, type PickerValue } from "./community-master-format";

/** Load the hosted alias list once; Theatrical is first and Other last per the contract. */
export function useVersionAliases() {
  const [choices, setChoices] = useState<AliasChoice[] | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    fetchVersionAliases(controller.signal).then((value) => setChoices(value.choices))
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attempt]);
  return { choices, error, retry: () => setAttempt((value) => value + 1) };
}

/**
 * Version picker: aliases in service order, then Other, which reveals an optional free-text field.
 * `required` adds a "Choose a version" placeholder; otherwise the first alias is preselected by the caller.
 */
export function VersionPicker({ value, onChange, disabled, required = false, label = "Version" }: {
  value: PickerValue; onChange: (value: PickerValue) => void; disabled?: boolean; required?: boolean; label?: string;
}) {
  const { choices, error, retry } = useVersionAliases();
  const otherId = useId();
  if (error) return <p role="alert" className="form-error">Version names couldn’t be loaded.{" "}
    <button type="button" className="btn-secondary compact-button" onClick={retry}>Retry</button></p>;
  if (!choices) return <p role="status" className="muted-copy">Loading version names…</p>;
  const aliases = choices.filter((choice): choice is Extract<AliasChoice, { kind: "alias" }> => choice.kind === "alias");
  const other = choices.find((choice) => choice.kind === "other");
  return <div className="master-version-picker">
    <label className="field"><span>{label}</span>
      <select value={value.selection} disabled={disabled} required={required} aria-required={required || undefined}
        onChange={(event) => onChange({ ...value, selection: event.target.value })}>
        {required && <option value="" disabled>Choose a version</option>}
        {aliases.map((choice) => <option key={choice.aliasId} value={choice.aliasId}>{choice.label}</option>)}
        {other && <option value={OTHER}>{other.label}</option>}
      </select>
    </label>
    {value.selection === OTHER && <label className="field" htmlFor={otherId}><span>Version name (optional)</span>
      <input id={otherId} value={value.otherText} disabled={disabled} placeholder="For example, IMAX"
        onChange={(event) => onChange({ ...value, otherText: event.target.value })} /></label>}
  </div>;
}
