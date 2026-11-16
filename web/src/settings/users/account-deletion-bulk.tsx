import { useEffect, useState } from "react";
import { bulkDeleteAccounts, previewAccountDeletion, type AccountDeletionPreview, type AccountDeletionResult, type BulkDeletionOutcome, type Disposition, type FileChoices, type JellyfinReturn } from "../../api/api-account-deletion";
import type { AccountSummary } from "../../api/api-users";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import type { Principal } from "../../auth/session";
import { ModalDialog } from "../../ui/ModalDialog";
import { accountName } from "../profile/profile-editor";
import { mayManageAccount } from "./account-actions";
import { resultLines } from "./account-deletion-copy";
import type { DeletionTarget } from "./account-deletion-dialog";
import { DispositionPicker } from "./account-deletion-disposition";
import { answered, deletionRefusalMessage, fileChoices, isPasswordRefusal, refusalCodeMessage, refusedUnchanged, reviewAgain, useStepFocus } from "./account-deletion-flow";
import { DeletionImpact } from "./account-deletion-impact";
import { JellyfinReturnChoice } from "./account-deletion-jellyfin";
import { appliedOutcome, unknownOutcome, type DeletionOutcome } from "./account-deletion-outcomes";

/** Where one selected account stands. Only `reviewed` rows without blockers are sent. */
export type BulkRow = { target: DeletionTarget } & (
  | { status: "previewing"; choices: FileChoices; notice: string | null }
  | { status: "reviewed"; preview: AccountDeletionPreview; choices: FileChoices; notice: string | null }
  | { status: "unavailable"; message: string }
  | { status: "applied"; result: AccountDeletionResult }
  | { status: "refused"; message: string }
  | { status: "unknown"; message: string }
);

const sendable = (row: BulkRow): row is Extract<BulkRow, { status: "reviewed" }> => row.status === "reviewed" && row.preview.blockers.length === 0;

/**
 * Fold the server's per-target outcomes into the rows. Applied targets are final; a
 * stale or newly blocked target is previewed again with its choices kept; a code that can
 * follow a commit leaves the target unknown; any other refusal is final for this dialog.
 * Targets that were not sent are unchanged.
 */
export function settle(rows: BulkRow[], outcomes: BulkDeletionOutcome[]): BulkRow[] {
  return rows.map(row => {
    const outcome = outcomes.find(item => item.user_id === row.target.id);
    if (!outcome || row.status !== "reviewed") return row;
    if (outcome.outcome === "applied") return { target: row.target, status: "applied", result: outcome.result };
    if (!refusedUnchanged(outcome.code)) return { target: row.target, status: "unknown", message: unknownOutcome(row.target).lines[0] };
    const message = refusalCodeMessage(outcome.code);
    return reviewAgain(outcome.code)
      ? { target: row.target, status: "previewing", choices: row.choices, notice: message }
      : { target: row.target, status: "refused", message };
  });
}

/** The receipt line for a row that has reached an outcome this round; rows still under review have none. */
function rowOutcome(row: BulkRow, destination: string | null): DeletionOutcome[] {
  const { id: userId, name } = row.target;
  switch (row.status) {
    case "applied": return [appliedOutcome(name, row.result, destination)];
    case "refused": return [{ userId, name, state: "unchanged", lines: [row.message] }];
    case "unknown": return [{ userId, name, state: "unknown", lines: [row.message] }];
    case "previewing": return row.notice ? [{ userId, name, state: "unchanged", lines: [row.notice] }] : [];
    default: return [];
  }
}

/** Delete several accounts with one disposition; each target is previewed, confirmed, and reported alone. */
export function BulkAccountDeletion({ selected, principal, onDeleted }: { selected: AccountSummary[]; principal: Principal; onDeleted: (outcomes: DeletionOutcome[]) => void }) {
  const deletable = selected.filter(account => mayManageAccount(principal, account, "accounts.delete.other"));
  const excluded = selected.filter(account => !deletable.includes(account));
  const [open, setOpen] = useState<{ targets: DeletionTarget[]; excluded: string[] } | null>(null);
  return <>
    {deletable.length > 0 && <div className="button-row"><button type="button" className="danger-button" onClick={() => setOpen({ targets: deletable.map(account => ({ id: account.id, name: accountName(account) })), excluded: excluded.map(accountName) })}>Delete selected</button></div>}
    {open && <BulkDeletionDialog {...open} onClose={() => setOpen(null)} onDeleted={onDeleted} />}
  </>;
}

function BulkDeletionDialog({ targets, excluded, onClose, onDeleted }: { targets: DeletionTarget[]; excluded: string[]; onClose: () => void; onDeleted: (outcomes: DeletionOutcome[]) => void }) {
  const [disposition, setDisposition] = useState<Disposition | null>(null);
  const [rows, setRows] = useState<BulkRow[]>([]);
  /** The rows reviewed when confirmation opened; it stays open only while they are unchanged. */
  const [confirming, setConfirming] = useState<BulkRow[] | null>(null);
  const [round, setRound] = useState(0);
  const [jellyfinUser, setJellyfinUser] = useState<JellyfinReturn>("allow");
  const stepFocus = useStepFocus(disposition === null);
  useEffect(() => {
    const pending = rows.filter(row => row.status === "previewing").map(row => row.target.id);
    if (!disposition || !pending.length) return;
    const controller = new AbortController();
    const update = (id: number, next: (row: BulkRow) => BulkRow) => setRows(current => current.map(row => row.target.id === id ? next(row) : row));
    void (async () => {
      for (const id of pending) {
        try {
          const preview = await previewAccountDeletion(id, disposition, controller.signal);
          if (controller.signal.aborted) return;
          update(id, row => row.status === "previewing" ? { target: row.target, status: "reviewed", preview, choices: fileChoices(preview, row.choices), notice: row.notice } : row);
        } catch (reason) {
          if (controller.signal.aborted) return;
          update(id, row => ({ target: row.target, status: "unavailable", message: deletionRefusalMessage(reason) }));
        }
      }
    })();
    return () => controller.abort();
  }, [disposition, round]);
  const settled = rows.every(row => row.status !== "previewing");
  const ready = settled ? rows.filter(sendable) : [];
  const reviewed = confirming === rows ? confirming.filter(sendable) : null;
  const applied = rows.some(row => row.status === "applied");
  const linked = ready.some(row => row.preview.credentials.linked);
  const destination = disposition?.mode === "transfer" ? disposition.destination.name : null;
  function review(next: Disposition) {
    setDisposition(next);
    setRows(targets.map(target => ({ target, status: "previewing", choices: {}, notice: null })));
    setRound(value => value + 1);
  }
  async function apply(snapshot: BulkRow[], password: string) {
    try {
      const outcomes = await bulkDeleteAccounts(snapshot.filter(sendable).map(row => ({ userId: row.target.id, disposition: disposition!, preview: row.preview, choices: row.choices, jellyfinUser: row.preview.credentials.linked ? jellyfinUser : "allow" })), password);
      const next = settle(snapshot, outcomes);
      setRows(next);
      setRound(value => value + 1);
      onDeleted(next.flatMap(row => rowOutcome(row, destination)));
    } catch (reason) {
      if (isPasswordRefusal(reason)) throw reason;
      const refused = answered(reason);
      const next = snapshot.map((row): BulkRow => !sendable(row) ? row : refused
        ? { target: row.target, status: "refused", message: deletionRefusalMessage(reason) }
        : { target: row.target, status: "unknown", message: unknownOutcome(row.target).lines[0] });
      setRows(next);
      if (!refused) onDeleted(next.flatMap(row => rowOutcome(row, destination)));
    }
    setConfirming(null);
  }
  return <>
    <ModalDialog labelledBy="bulk-deletion-heading" className="account-dialog deletion-dialog" onClose={reviewed ? undefined : onClose}>
      <div className="section-heading"><h2 id="bulk-deletion-heading">Delete selected accounts</h2><button type="button" className="btn-secondary" onClick={onClose}>Close</button></div>
      <p>{targets.map(target => target.name).join(", ")}. Each account is checked and changed on its own.</p>
      {excluded.length > 0 && <p className="deletion-excluded">Not included: {excluded.join(", ")}. The owner, your own account, and accounts you can't manage are protected.</p>}
      <div ref={stepFocus} tabIndex={-1} className="deletion-step">
      {!disposition && <DispositionPicker excluded={targets.map(target => target.id)} notice={null} onReview={review} onCancel={onClose} />}
      {disposition && <>
        {rows.map(row => <section key={row.target.id} className="deletion-target" aria-label={`Deletion of ${row.target.name}`}>
          <h3>{row.target.name}</h3>
          {row.status === "previewing" && <>{row.notice && <p role="alert" className="form-status">{row.notice}</p>}<p role="status">Checking what this changes...</p></>}
          {row.status === "reviewed" && <>
            {row.notice && <p role="alert" className="form-status">{row.notice}</p>}
            <DeletionImpact preview={row.preview} disposition={disposition} name={row.target.name} choices={row.choices}
              onChoice={(exportId, choice) => setRows(current => current.map(item => item.target.id === row.target.id && item.status === "reviewed" ? { ...item, choices: { ...item.choices, [exportId]: choice } } : item))} />
          </>}
          {row.status === "unavailable" && <p role="alert" className="form-status">{row.message}</p>}
          {row.status === "applied" && <><p role="status">Deleted.</p><ul>{resultLines(row.result, destination).map(line => <li key={line}>{line}</li>)}</ul></>}
          {row.status === "refused" && <p role="alert" className="form-status">Not changed. {row.message}</p>}
          {row.status === "unknown" && <p role="alert" className="form-status">{row.message}</p>}
        </section>)}
        {linked && <JellyfinReturnChoice value={jellyfinUser} onChange={setJellyfinUser} many />}
        <div className="button-row">
          {!applied && <button type="button" className="btn-secondary" onClick={() => { setDisposition(null); setRows([]); }}>Change choice</button>}
          {applied && <button type="button" className="btn-primary" onClick={onClose}>Done</button>}
          {ready.length > 0 && <button type="button" className="danger-button" onClick={() => setConfirming(rows)}>Delete {ready.length} {ready.length === 1 ? "account" : "accounts"}</button>}
        </div>
      </>}
      </div>
    </ModalDialog>
    {reviewed && <FreshAuthenticationDialog title="Confirm deleting selected accounts"
      body={`${reviewed.map(row => row.target.name).join(", ")}. Editarr applies exactly the impact you reviewed for each account. An account that changed since is left unchanged for you to review again.`}
      actionLabel={`Delete ${reviewed.length} ${reviewed.length === 1 ? "account" : "accounts"}`} danger
      onClose={() => setConfirming(null)} onConfirm={password => apply(confirming!, password)} />}
  </>;
}
