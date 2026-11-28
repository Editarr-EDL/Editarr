import type { ItemDetailVersion } from "../../../api/api-community-operator-catalog";
import { useModeration } from "../moderation-dialog";
import { actionLabel } from "../moderation-commands";
import { tokenCommands } from "../token-actions";
import { useOperator } from "../shared/operator-data";

/** Hero actions for the selected version: recompute plus the state-permitted version commands in a menu. */
export function VersionActions({ version, enabled, onApplied }: { version: ItemDetailVersion; enabled: boolean; onApplied: () => void }) {
  const { onAccessFailure } = useOperator();
  const moderation = useModeration({ onAccessFailure, onApplied });
  const target = { kind: "version" as const, targetId: version.versionId };
  // Commands are offered from the listed state; the dialog re-inspects and refuses if the state changed.
  const revision = version.moderation.revision;
  const commands = tokenCommands(version.moderation, { status: version.status, quarantined: version.quarantined ? 1 : 0 })
    .filter(command => command !== "recompute");
  const disabled = !enabled || moderation.locked;
  return <div className="hero-actions">
    {version.status !== "merged_into" && <button type="button" className="btn sm" disabled={disabled}
      onClick={() => void moderation.begin(target, "recompute")}>Recompute</button>}
    {commands.length > 0 && <details className="menu"><summary className="btn sm">Version actions <span aria-hidden="true">▾</span></summary>
      <div className="menu-pop">{commands.map(command => <button type="button" key={command} className="btn sm ghost" disabled={disabled}
        onClick={event => { event.currentTarget.closest("details")?.removeAttribute("open"); void moderation.begin(target, command, { revision }); }}>
        {actionLabel(command, "version")}</button>)}</div></details>}
    {moderation.view}
  </div>;
}
