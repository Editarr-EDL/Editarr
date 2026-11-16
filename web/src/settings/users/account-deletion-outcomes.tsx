import type { AccountDeletionResult } from "../../api/api-account-deletion";
import { useOperationReceipt } from "../../auth/operation-receipts";
import { resultLines } from "./account-deletion-copy";

/**
 * One target's final outcome, kept as an operation receipt so it survives a permission refresh.
 * `unknown` means the request was sent but Editarr's answer never arrived.
 */
export interface DeletionOutcome { userId: number; name: string; state: "deleted" | "unchanged" | "unknown"; lines: string[] }

const STATE_LABELS: Record<DeletionOutcome["state"], string> = { deleted: "deleted", unchanged: "not changed", unknown: "outcome unknown" };

export const appliedOutcome = (name: string, result: AccountDeletionResult, destination: string | null): DeletionOutcome =>
  ({ userId: result.user_id, name, state: "deleted", lines: resultLines(result, destination) });

export const unknownOutcome = ({ id, name }: { id: number; name: string }): DeletionOutcome =>
  ({ userId: id, name, state: "unknown", lines: [`Editarr did not confirm whether ${name} was deleted. Check the Users list before trying again.`] });

export function useDeletionOutcomes() {
  return useOperationReceipt<DeletionOutcome[]>("users.account-deletion");
}

/** The latest deletion outcomes, naming exactly what changed for each target. */
export function DeletionOutcomes({ outcomes }: { outcomes: DeletionOutcome[] }) {
  if (!outcomes.length) return null;
  return <section className="deletion-outcomes" aria-label="Account deletion results">
    <h3>Account deletion results</h3>
    <ul>{outcomes.map(outcome => <li key={outcome.userId}>
      <strong>{outcome.name}: {STATE_LABELS[outcome.state]}</strong>
      <ul>{outcome.lines.map(line => <li key={line}>{line}</li>)}</ul>
    </li>)}</ul>
  </section>;
}
