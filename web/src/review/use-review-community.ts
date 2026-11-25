import { useCallback, useEffect, useState } from "react";
import { listCommunityMasters } from "../api/api-community-masters";
import type { MasterLinkSummary } from "../api/api-community-master-types";
import { fetchSavedReview } from "../api/api-reviews";
import type { ReviewCommunitySource } from "../api/api-review-types";
import { useCan } from "../auth/auth-gate";
import { COMMUNITY_FILTERS_CHANGED_EVENT, COMMUNITY_MASTERS_CHANGED_EVENT } from "../scans/community-master-events";
import { communityEntries } from "./review-community";
import type { useSavedReviewWorkspace } from "./review-workspace-state";

type Workspace = ReturnType<typeof useSavedReviewWorkspace>;

/**
 * The review's "Community filters" entries: stored snapshots plus the account's master links for this
 * version. When the Community tab changes master links or downloads newer filters, the links reload
 * and the stored snapshots' states are read again, without touching the draft. Without
 * `community.search` only stored snapshots show.
 */
export function useReviewCommunity(workspace: Workspace) {
  const maySearch = useCan("community.search");
  const { item_id: itemId, media_source_id: mediaSourceId } = workspace.reviewTarget;
  const reviewId = workspace.selected?.id ?? null;
  const [links, setLinks] = useState<MasterLinkSummary[]>([]);
  const [states, setStates] = useState<{ reviewId: string; revision: number; sources: ReviewCommunitySource[] } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [stateAttempt, setStateAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  const reloadStates = useCallback(() => { setAttempt((value) => value + 1); setStateAttempt((value) => value + 1); }, []);
  useEffect(() => {
    window.addEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
    window.addEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, reloadStates);
    return () => {
      window.removeEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
      window.removeEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, reloadStates);
    };
  }, [reload, reloadStates]);
  useEffect(() => {
    if (!maySearch) { setLinks([]); return; }
    const controller = new AbortController();
    listCommunityMasters(itemId, mediaSourceId ?? null, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setLinks(value.links ?? []); })
      .catch(() => { if (!controller.signal.aborted) setLinks([]); });
    return () => controller.abort();
  }, [maySearch, itemId, mediaSourceId, attempt]);
  useEffect(() => {
    if (!reviewId || !stateAttempt) return;
    let current = true;
    fetchSavedReview(reviewId).then((review) => {
      if (current) setStates({ reviewId, revision: review.revision, sources: review.community_sources ?? [] });
    }).catch(() => undefined);
    return () => { current = false; };
  }, [reviewId, stateAttempt]);
  const stored = workspace.selected?.community_sources ?? [];
  // States read since the selected review loaded; a later save or reload supersedes them.
  const latest = states?.reviewId === reviewId && states.revision === workspace.selected?.revision ? states.sources : [];
  const sources = stored.map((source) => {
    const fresh = latest.find((value) => value.source_id === source.source_id);
    return fresh ? { ...source, state: fresh.state, link_state: fresh.link_state } : source;
  });
  return { maySearch, sources, entries: workspace.draft ? communityEntries(workspace.draft, sources, links) : [] };
}
