/**
 * How many entries differ between the saved and draft lists, keyed by `key`. An added, removed,
 * or edited entry counts once, and a new order of the shared entries counts once more.
 */
export function changeCount<T>(saved: readonly T[], draft: readonly T[], key: (item: T) => string): number {
  const before = new Map(saved.map(item => [key(item), JSON.stringify(item)]));
  const after = new Set(draft.map(key));
  const edited = draft.filter(item => before.get(key(item)) !== JSON.stringify(item)).length;
  const removed = [...before.keys()].filter(id => !after.has(id)).length;
  const order = (items: readonly T[], other: { has: (id: string) => boolean }) => items.map(key).filter(id => other.has(id)).join("\n");
  return edited + removed + (order(saved, after) === order(draft, before) ? 0 : 1);
}

/** The bottom bar with the unsaved change count, Discard and Save. A status replaces the count until the next edit. */
export function SaveBar({ count, saving, blocked, invalid, status, onSave, onDiscard }: {
  count: number; saving: boolean; blocked: boolean; invalid: boolean; status?: string | null;
  onSave: () => void; onDiscard: () => void;
}) {
  const summary = count === 0 ? "No unsaved changes" : `${count} unsaved ${count === 1 ? "change" : "changes"}`;
  return <div className={count === 0 ? "filter-save-bar" : "filter-save-bar is-dirty"} role="group" aria-label="Save changes">
    {count === 0 && status ? <p className="form-status" role="status">{status}</p> : <p>{summary}</p>}
    <div className="button-row">
      <button type="button" className="btn-secondary" disabled={blocked || count === 0} onClick={onDiscard}>Discard</button>
      <button type="button" className="btn-primary" disabled={blocked || count === 0 || invalid} onClick={onSave}>{saving ? "Saving…" : "Save"}</button>
    </div>
  </div>;
}
