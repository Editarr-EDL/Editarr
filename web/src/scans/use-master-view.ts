import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCommunityMaster } from "../api/api-community-masters";
import type { MasterFilter, MasterView } from "../api/api-community-master-types";
import { COMMUNITY_VOTE_CHANGED_EVENT, type CommunityVoteChange } from "./community-master-events";
import { masterErrorText } from "./community-master-format";
import { masterPreferences } from "./master-preferences-store";

/** Load one master set view; its reported preferences seed the shared preference store. */
export function useMasterView(linkId: string, store = masterPreferences) {
  const [view, setView] = useState<MasterView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setError(null);
    try {
      const next = await fetchCommunityMaster(linkId, request.signal);
      if (request.signal.aborted) return;
      store.seed(next.preferences);
      setView(next);
    } catch (reason) { if (!request.signal.aborted) setError(masterErrorText(reason)); }
  }, [linkId, store]);
  useEffect(() => {
    void reload();
    return () => controller.current?.abort();
  }, [reload]);

  const replaceFilter = (filter: MasterFilter) => setView((current) => current && {
    ...current, filters: current.filters.map((item) => item.filterId === filter.filterId ? filter : item),
  });
  useEffect(() => {
    // A vote sent from a review shows here too.
    const onVote = (event: Event) => {
      const { linkId: voted, filterId, myVote } = (event as CustomEvent<CommunityVoteChange>).detail;
      if (voted !== linkId) return;
      setView((current) => current && { ...current,
        filters: current.filters.map((item) => item.filterId === filterId ? { ...item, myVote } : item) });
    };
    window.addEventListener(COMMUNITY_VOTE_CHANGED_EVENT, onVote);
    return () => window.removeEventListener(COMMUNITY_VOTE_CHANGED_EVENT, onVote);
  }, [linkId]);
  return { view, error, reload, replaceFilter };
}
