import type { ReactNode } from "react";
import type { OperatorRow } from "../../api/api-community-operator";
import type { ModerationToken } from "../../api/api-community-operator-labels";
import { actionLabel, commandsFor, parseResource, type OperatorActionKind } from "./moderation-commands";
import { useModeration } from "./moderation-dialog";
import { useOperator } from "./shared/operator-data";

/** Commands permitted by a listed state (`status`, `state`, `quarantined`, `excluded`) for a moderation token's resource. */
export function tokenCommands(token: ModerationToken, state: OperatorRow): OperatorActionKind[] {
  return commandsFor({ resource: token.resource, revision: token.revision, state, audit: [] });
}

/**
 * Buttons for the actions a resource's listed state permits, using its moderation token as the expected revision.
 * The dialog re-inspects the resource and refuses when the state or revision changed since the page loaded.
 */
export function TokenActions({ token, state, enabled, onApplied, omit = [], extra }: { token: ModerationToken; state: OperatorRow;
  enabled: boolean; onApplied: () => void; omit?: OperatorActionKind[]; extra?: ReactNode }) {
  const { onAccessFailure } = useOperator();
  const moderation = useModeration({ onAccessFailure, onApplied });
  const target = parseResource(token.resource);
  return <section aria-label="Moderation actions" className="token-actions">
    <div className="actions">{tokenCommands(token, state).filter(command => !omit.includes(command)).map(command => <button key={command} type="button"
      className={`btn sm${command === "quarantine" || command === "suspend" ? " danger" : ""}`} disabled={!enabled || moderation.locked}
      onClick={() => void moderation.begin(target, command, { revision: token.revision })}>{actionLabel(command, target.kind)}</button>)}{extra}</div>
    {moderation.view}
  </section>;
}
