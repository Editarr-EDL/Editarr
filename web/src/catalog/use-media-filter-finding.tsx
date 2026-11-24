import { useRef, useState } from "react";
import type { LookupInput } from "../api/api-community-lookup";
import type { JellyfinMediaItem } from "../api/api-media-types";
import type { Scan } from "../api/api-scan-types";
import { useCan } from "../auth/auth-gate";
import { CommunityMasterPanel } from "../scans/community-master-panel";
import { FindFiltersDialog } from "../scans/find-filters-dialog";
import { useAudioTracks } from "../scans/use-audio-tracks";
import { useCommunityDownloads } from "../scans/use-community-downloads";
import { useFindFilters } from "../scans/use-find-filters";

type Version = JellyfinMediaItem["versions"][number];

/**
 * Local and community filter finding for one item version on the media detail page: the Find Filters
 * button, its dialog (local matches, community lookup and master versions) and the community master
 * filter section. `onVerify` opens verification for a downloaded scan on stable items.
 */
export function useMediaFilterFinding({ item, sourceId, version, verifyImports, loadScans, onMessage, onVerify }: {
  item: JellyfinMediaItem; sourceId: string | null; version: Version | undefined; verifyImports: boolean;
  loadScans: () => Promise<unknown>; onMessage: (message: string | null) => void; onVerify: (scan: Scan) => void;
}) {
  const maySearchCommunity = useCan("community.search");
  const mayReadFindings = useCan("scans.findings.read");
  const [active, setActive] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const target = { source: "jellyfin" as const, item_id: item.id, media_source_id: sourceId };
  const lookupTracks = useAudioTracks({ item_id: item.id, media_source_id: sourceId }, active);
  const communityInput: LookupInput | null = maySearchCommunity && active
    ? { target, audio_selection: lookupTracks.ready ? lookupTracks.selection : null } : null;
  const downloads = useCommunityDownloads(communityInput, () => void loadScans(), verifyImports ? (scan) => {
    filters.close();
    setActive(false);
    onVerify(scan);
  } : undefined);
  const filters = useFindFilters({
    communityInput,
    target: {
      ...target, library_id: item.library_id, item_name: item.name, item_type: item.item_type,
      duration_seconds: sourceId ? version?.duration_seconds || item.duration_seconds : item.duration_seconds,
      scene_name: version?.scene_name || null, release_group: version?.release_group || null,
      media_format: version?.media_format || item.media_format, media_provider_id: item.media_provider_id,
    },
    onMessage,
    requireVerification: verifyImports,
    onAssociated: async () => { setActive(false); await loadScans(); },
  });

  const button = mayReadFindings && <button type="button" className="btn-secondary" ref={trigger}
    onClick={() => { setActive(true); void filters.find(false); }}
    disabled={filters.finding}>{filters.finding ? "Finding Filters…" : "Find Filters"}</button>;
  const dialog = filters.open && <FindFiltersDialog candidates={filters.candidates} results={filters.results}
    selectedIds={filters.selectedIds} associating={filters.associating} onSelect={filters.select}
    onAssociate={() => void filters.associate()} onClose={() => { filters.close(); setActive(false); }} returnFocusRef={trigger}
    masterTarget={maySearchCommunity ? target : undefined}
    community={{ lookup: filters.community, downloads, tracks: { inventory: lookupTracks.inventory, selection: lookupTracks.selection,
      loading: lookupTracks.loading, error: lookupTracks.error, disabled: filters.associating,
      onChange: lookupTracks.onChange, onRetry: lookupTracks.onRetry } }} />;
  const masterPanel = <CommunityMasterPanel target={target} />;
  return { button, dialog, masterPanel };
}
