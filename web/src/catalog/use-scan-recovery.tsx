import { useId, useRef } from "react";
import type { JellyfinMediaItem } from "../api/api-media-types";
import { useCan } from "../auth/auth-gate";
import { FindFiltersDialog } from "../scans/find-filters-dialog";
import { useFindFilters } from "../scans/use-find-filters";

type Version = JellyfinMediaItem["versions"][number];

export const NOTHING_TO_RECOVER = "There are no local scans of this item, its previous versions or other files to recover.";

/**
 * Local scan recovery for one item version on the media detail page: the Scans tab's Recover Scans
 * button and its dialog, which match this item's local scans (other versions and copies) and associate
 * the chosen ones. `recoverable` is false when no local scan exists for the item, its previous versions
 * or any other file; the button is then disabled with an explanation. `verifyImports` asks for timing checks after
 * associating scans on stable items.
 */
export function useScanRecovery({ item, sourceId, version, verifyImports, recoverable, loadScans, onMessage }: {
  item: JellyfinMediaItem; sourceId: string | null; version: Version | undefined; verifyImports: boolean;
  recoverable: boolean; loadScans: () => Promise<unknown>; onMessage: (message: string | null) => void;
}) {
  const mayReadFindings = useCan("scans.findings.read");
  const trigger = useRef<HTMLButtonElement>(null);
  const reasonId = useId();
  const recovery = useFindFilters({
    target: {
      source: "jellyfin", item_id: item.id, media_source_id: sourceId,
      library_id: item.library_id, item_name: item.name, item_type: item.item_type,
      duration_seconds: sourceId ? version?.duration_seconds || item.duration_seconds : item.duration_seconds,
      scene_name: version?.scene_name || null, release_group: version?.release_group || null,
      media_format: version?.media_format || item.media_format, media_provider_id: item.media_provider_id,
    },
    onMessage,
    requireVerification: verifyImports,
    onAssociated: async () => { await loadScans(); },
  });

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
