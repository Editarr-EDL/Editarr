import type { Revision } from "../../../api/api-community-operator-catalog";
import { clockTime, shortDate } from "../shared/format";
import { RevisionCauseText, RevisionChanges } from "../shared/revision";
import { HistoryPager, PanelHead } from "../shared/ui";

/**
 * One page of immutable master revisions, newest first, with what changed and why. `older` links to the next page
 * (the service's `revisionsNextCursor`) and `newest` back to the first; the service's `current` flag marks the live revision.
 */
export function RevisionHistory({ revisions, label, older, newest, busy }: { revisions: Revision[]; label: string;
  older: string | null; newest: string | null; busy?: boolean }) {
  const sorted = [...revisions].sort((a, b) => b.revision - a.revision);
  return <section className="card revs" aria-labelledby="revisions-title" aria-busy={busy}>
    <PanelHead title={<span id="revisions-title">Revision history</span>} note={`${label} master set · each revision is an immutable snapshot`}>
      <HistoryPager label="Revision history pages" older={older} newest={newest} />
    </PanelHead>
    {sorted.length ? <ol className="rev-list">{sorted.map(revision => <li key={revision.revision} className={`rev-item${revision.current ? " cur" : ""}`}>
      <span className="rn">rev {revision.revision}</span>
      <span className="delta"><RevisionChanges delta={revision} /> · <RevisionCauseText cause={revision.cause} />
        {revision.current && <span className="chip ok mini">current</span>}</span>
      <span className="when">{shortDate(revision.generatedAt)} · {clockTime(revision.generatedAt)}</span></li>)}</ol> :
      <p className="empty">No revisions recorded.</p>}
  </section>;
}
