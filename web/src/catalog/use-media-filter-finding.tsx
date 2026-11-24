import { useRef } from "react";
import type { JellyfinMediaItem } from "../api/api-media-types";
import { useCan } from "../auth/auth-gate";
import { CommunityMasterPanel } from "../scans/community-master-panel";
import { FindFiltersDialog } from "../scans/find-filters-dialog";
import { useFindFilters } from "../scans/use-find-filters";

type Version = JellyfinMediaItem["versions"][number];

/**
 * Local and community filter finding for one item version on the media detail page: the Find Filters
 * button, its dialog (local matches and community master versions) and the community master filter
 * section. `verifyImports` asks for timing checks after associating local scans on stable items.
 */
export function useMediaFilterFinding({ item, sourceId, version, verifyImports, loadScans, onMessage }: {
  item: JellyfinMediaItem; sourceId: string | null; version: Version | undefined; verifyImports: boolean;
  loadScans: () => Promise<unknown>; onMessage: (message: string | null) => void;
}) {
  const maySearchCommunity = useCan("community.search");
  const mayReadFindings = useCan("scans.findings.read");
  const trigger = useRef<HTMLButtonElement>(null);
  const target = { source: "jellyfin" as const, item_id: item.id, media_source_id: sourceId };
  const filters = useFindFilters({
    target: {
      ...target, library_id: item.library_id, item_name: item.name, item_type: item.item_type,
      duration_seconds: sourceId ? version?.duration_seconds || item.duration_seconds : item.duration_seconds,
      scene_name: version?.scene_name || null, release_group: version?.release_group || null,
      media_format: version?.media_format || item.media_format, media_provider_id: item.media_provider_id,
    },
    onMessage,
    requireVerification: verifyImports,
    onAssociated: async () => { await loadScans(); },
  });

  const button = mayReadFindings && <button type="button" className="btn-secondary" ref={trigger}
    onClick={() => void filters.find()}
    disabled={filters.finding}>{filters.finding ? "Finding Filters…" : "Find Filters"}</button>;
  const dialog = filters.open && <FindFiltersDialog candidates={filters.candidates} results={filters.results}
    selectedIds={filters.selectedIds} associating={filters.associating} onSelect={filters.select}
    onAssociate={() => void filters.associate()} onClose={filters.close} returnFocusRef={trigger}
    masterTarget={maySearchCommunity ? target : undefined} />;
  const masterPanel = <CommunityMasterPanel target={target} />;
  return { button, dialog, masterPanel };
}
