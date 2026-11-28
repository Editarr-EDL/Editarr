import type { OperatorInspection } from "../../api/api-community-operator";
import { actionLabel, commandsFor, parseResource } from "./moderation-commands";
import { useModeration } from "./moderation-dialog";

/**
 * The actions an inspected resource's state permits, each confirmed with a reason. `heading` titles the section
 * (pass null to omit it) and `extra` renders after the buttons inside the action bar.
 */
export function ModerationActions({ inspection, enabled, onAccessFailure, onApplied, heading = "Moderation", extra }: {
  inspection: OperatorInspection; enabled: boolean; onAccessFailure: (status: number) => void; onApplied: () => void;
  heading?: string | null; extra?: React.ReactNode;
}) {
  const moderation = useModeration({ onAccessFailure, onApplied });
  const target = parseResource(inspection.resource);
  return <section aria-label="Moderation actions">
    {heading && <h3>{heading}</h3>}
    <div className="operator-action-bar">{commandsFor(inspection).map(command => <button key={command}
      className={`btn sm${command === "quarantine" || command === "suspend" ? " danger" : ""}`}
      disabled={!enabled || moderation.locked}
      onClick={() => void moderation.begin(target, command, { revision: inspection.revision })}>{actionLabel(command, target.kind)}</button>)}
      {extra}</div>
    {moderation.view}
  </section>;
}
