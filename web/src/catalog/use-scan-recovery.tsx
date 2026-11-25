import { useEffect, useId, useRef, useState } from "react";
import { filterCandidatesAvailable } from "../api/api-scans";
import type { JellyfinMediaItem } from "../api/api-media-types";
import { useCan } from "../auth/auth-gate";
import { FindFiltersDialog } from "../scans/find-filters-dialog";
import { useFindFilters } from "../scans/use-find-filters";

type Version = JellyfinMediaItem["versions"][number];

export const NOTHING_TO_RECOVER = "There are no local scans of this item's other versions or files, its previous versions, or the same work under another item to recover.";

/**
 * Local scan recovery for one item version on the media detail page: the Scans tab's Recover Scans
 * button and its dialog, which match local scans of other files and associate the chosen ones.
 * The button is enabled when `localRecoverable` (this item's other versions, previous versions,
 * pending imports or linked scans have scans) or the server reports a reachable scan of another file
 * of the same work; otherwise it is disabled with an explanation. `verifyImports` asks for timing
 * checks after associating scans on stable items.
 */
export function useScanRecovery({ item, sourceId, version, verifyImports, localRecoverable, loadScans, onMessage }: {
  item: JellyfinMediaItem; sourceId: string | null; version: Version | undefined; verifyImports: boolean;
  localRecoverable: boolean; loadScans: () => Promise<unknown>; onMessage: (message: string | null) => void;
}) {
  const mayReadFindings = useCan("scans.findings.read");
  const trigger = useRef<HTMLButtonElement>(null);
  const reasonId = useId();
  const [checked, setChecked] = useState(0);
  const [elsewhere, setElsewhere] = useState<{ key: string; available: boolean } | null>(null);
  const target = {
    source: "jellyfin" as const, item_id: item.id, media_source_id: sourceId,
    library_id: item.library_id, item_name: item.name, item_type: item.item_type,
    duration_seconds: sourceId ? version?.duration_seconds || item.duration_seconds : item.duration_seconds,
    scene_name: version?.scene_name || null, release_group: version?.release_group || null,
    media_format: version?.media_format || item.media_format, media_provider_id: item.media_provider_id,
  };
  const key = JSON.stringify(target);
  const recovery = useFindFilters({
    target, onMessage, requireVerification: verifyImports,
    onAssociated: async () => { await loadScans(); setChecked((value) => value + 1); },
  });
  useEffect(() => {
    if (!mayReadFindings) return;
    const controller = new AbortController();
    // A failed check leaves the button enabled, so the search itself can report the problem.
    filterCandidatesAvailable(JSON.parse(key), controller.signal)
      .then((available) => { if (!controller.signal.aborted) setElsewhere({ key, available }); })
      .catch(() => { if (!controller.signal.aborted) setElsewhere({ key, available: true }); });
    return () => controller.abort();
  }, [key, mayReadFindings, checked]);
  const recoverable = localRecoverable || (elsewhere?.key === key && elsewhere.available);

  const button = mayReadFindings && <span className="recover-scans-action">
    <button type="button" className="btn-secondary" ref={trigger} aria-describedby={recoverable ? undefined : reasonId}
      title={recoverable ? undefined : NOTHING_TO_RECOVER}
      onClick={() => void recovery.find()} disabled={!recoverable || recovery.finding}>
      {recovery.finding ? "Recovering Scans…" : "Recover Scans"}</button>
    {!recoverable && <small id={reasonId} className="muted-copy">{NOTHING_TO_RECOVER}</small>}
  </span>;
  const dialog = recovery.open && <FindFiltersDialog candidates={recovery.candidates} results={recovery.results}
    selectedIds={recovery.selectedIds} associating={recovery.associating} onSelect={recovery.select}
    onAssociate={() => void recovery.associate()} onClose={recovery.close} returnFocusRef={trigger} />;
  return { button, dialog };
}
