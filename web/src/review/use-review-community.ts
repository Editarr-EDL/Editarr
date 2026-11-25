import { useCallback, useEffect, useState } from "react";
import { fetchMasterActiveFilters, listCommunityMasters } from "../api/api-community-masters";
import type { MasterLinkSummary } from "../api/api-community-master-types";
import type { ReviewCommunityFilter } from "../api/api-review-types";
import { useCan } from "../auth/auth-gate";
import { COMMUNITY_FILTERS_CHANGED_EVENT, COMMUNITY_MASTERS_CHANGED_EVENT } from "../scans/community-master-events";
import { communityEntries, liveFilters } from "./review-community";
import type { useSavedReviewWorkspace } from "./review-workspace-state";

type Workspace = ReturnType<typeof useSavedReviewWorkspace>;

/**
 * The review's "Community filters" entries: stored snapshots plus the account's master links for this
 * version, with each link's active filters now. Links and filters reload whenever the Community tab
 * changes master links or their active filters. Without `community.search` only stored snapshots show.
 */
export function useReviewCommunity(workspace: Workspace) {
  const maySearch = useCan("community.search");
  const { item_id: itemId, media_source_id: mediaSourceId } = workspace.reviewTarget;
  const [links, setLinks] = useState<MasterLinkSummary[]>([]);
  const [live, setLive] = useState<Record<string, ReviewCommunityFilter[]>>({});
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  useEffect(() => {
    window.addEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
    window.addEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, reload);
    return () => {
      window.removeEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
      window.removeEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, reload);
    };
  }, [reload]);
  useEffect(() => {
    if (!maySearch) { setLinks([]); setLive({}); return; }
    const controller = new AbortController();
    void (async () => {
      try {
        const listed = (await listCommunityMasters(itemId, mediaSourceId ?? null, controller.signal)).links ?? [];
        const usable = listed.filter((link) => link.linkState !== "replaced" && link.alignmentState !== "required");
        const filters = await Promise.all(usable.map((link) => fetchMasterActiveFilters(link.linkId, controller.signal)
          .then((value) => [link.linkId, liveFilters(value)] as const).catch(() => null)));
        if (controller.signal.aborted) return;
        setLinks(listed);
        setLive(Object.fromEntries(filters.filter((value) => value !== null)));
      } catch {
        if (!controller.signal.aborted) { setLinks([]); setLive({}); }
      }
    })();
    return () => controller.abort();
  }, [maySearch, itemId, mediaSourceId, attempt]);
  const sources = workspace.selected?.community_sources ?? [];
  return { maySearch, entries: workspace.draft ? communityEntries(workspace.draft, sources, links, live) : [] };
}

