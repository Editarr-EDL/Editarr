import type { OperatorActionExtras } from "../../api/api-community-operator-actions";
import { aliasIdentifier, identifier } from "../../api/api-community-operator-checks";
import type { OperatorActionKind } from "./moderation-commands";

/** Unvalidated form values for command-specific fields. */
export interface ExtrasDraft { intoVersionId?: string; aliasId?: string; label?: string; create?: boolean }

/** The request fields for `command`, or null while the draft is incomplete or invalid. */
export function extrasFor(command: OperatorActionKind, draft: ExtrasDraft): OperatorActionExtras | null {
  if (command === "merge_version") return identifier(draft.intoVersionId) ? { intoVersionId: draft.intoVersionId } : null;
  if (command !== "approve_alias") return {};
  if (!aliasIdentifier(draft.aliasId)) return null;
  if (!draft.create) return { aliasId: draft.aliasId };
  const label = draft.label ?? "";
  return label.trim() === label && [...label].length >= 1 && [...label].length <= 64 && !/[\p{Cc}\p{Cf}]/u.test(label)
    ? { newAliasId: draft.aliasId, label } : null;
}

/** Inputs for the merge target or alias approval; renders nothing for other commands. */
export function ActionExtras({ command, value, onChange, disabled }: { command: OperatorActionKind; value: ExtrasDraft;
  onChange: (value: ExtrasDraft) => void; disabled: boolean }) {
  if (command === "merge_version") return <>
    <label htmlFor="operator-merge-target">Target version ID (same work)</label>
    <input id="operator-merge-target" value={value.intoVersionId ?? ""} maxLength={128} disabled={disabled}
      onChange={event => onChange({ ...value, intoVersionId: event.target.value })} />
  </>;
  if (command !== "approve_alias") return null;
  return <>
    <label><input type="checkbox" checked={!!value.create} disabled={disabled} className="operator-checkbox"
      onChange={event => onChange({ ...value, create: event.target.checked })} /> Create a new alias</label>
    <label htmlFor="operator-alias-id">{value.create ? "New alias ID (a-z, 0-9, _)" : "Existing alias ID"}</label>
    <input id="operator-alias-id" value={value.aliasId ?? ""} maxLength={64} disabled={disabled}
      onChange={event => onChange({ ...value, aliasId: event.target.value })} />
    {value.create && <><label htmlFor="operator-alias-label">New alias label</label>
      <input id="operator-alias-label" value={value.label ?? ""} maxLength={64} disabled={disabled}
        onChange={event => onChange({ ...value, label: event.target.value })} /></>}
  </>;
}
