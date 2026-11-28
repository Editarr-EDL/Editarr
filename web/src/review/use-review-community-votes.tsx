import { useCallback, useEffect, useState } from "react";
import { fetchCommunityMaster } from "../api/api-community-masters";
import type { MasterFilter, MyVote } from "../api/api-community-master-types";
import type { ReviewCommunitySource, ReviewFilter } from "../api/api-review-types";
import { useCan } from "../auth/auth-gate";
import {
  COMMUNITY_FILTERS_CHANGED_EVENT, COMMUNITY_MASTERS_CHANGED_EVENT, COMMUNITY_VOTE_CHANGED_EVENT, type CommunityVoteChange,
} from "../scans/community-master-events";
import { masterErrorText } from "../scans/community-master-format";
import { MasterVote } from "../scans/master-vote";

/** What a community review filter's vote pill shows: the served filter voted on and its vote, or why it can't vote. */
export type ReviewVoteTarget = { linkId: string | null; filterId: string; myVote: MyVote; unavailable: string | null };

type Loaded = { filters: MasterFilter[]; retired: Record<string, "up" | "down"> } | { error: string; retryable: boolean };

export const voteUnavailable = {
  permission: "Your account can’t vote on community filters.",
  detached: "This filter’s community version is no longer linked, so it can’t be voted on here.",
  notServed: "The community no longer serves this filter, so it can’t be voted on.",
  loading: "Loading your vote…",
  loadFailed: "Couldn’t load your votes. Use Retry loading votes under Sources.",
};

/** The served filter a review filter votes on: its own master filter, or the one that merged it (`mergedIds`). */
export function servedFilterFor(filters: MasterFilter[], filterId: string): MasterFilter | null {
  return filters.find((item) => item.filterId === filterId)
    ?? filters.find((item) => item.mergedIds?.includes(filterId)) ?? null;
}

/**
 * The account's current votes on a review's community filters, read from each included snapshot's live
 * master link. Votes are sent immediately and aren't part of the review draft. A snapshot detached from
 * its link (deleted, replaced or another account's), a filter the link no longer serves, or an account
 * without `community.feedback.submit` gets a reason instead of a target to vote on.
 */
export function useReviewCommunityVotes(sources: ReviewCommunitySource[]) {
  const mayVote = useCan("community.feedback.submit");
  const maySearch = useCan("community.search");
  const linkIds = [...new Set(sources.filter((source) => source.link_id && source.state !== "link_unavailable")
    .map((source) => source.link_id!))].sort();
  const linkKey = linkIds.join("\n");
  const [views, setViews] = useState<Record<string, Loaded>>({});
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    const onVote = (event: Event) => {
      const { linkId, filterId, myVote } = (event as CustomEvent<CommunityVoteChange>).detail;
      setViews((current) => {
        const view = current[linkId];
        if (!view || !("filters" in view)) return current;
        return { ...current, [linkId]: { ...view, filters: view.filters.map((item) => item.filterId === filterId ? { ...item, myVote } : item) } };
      });
    };
    window.addEventListener(COMMUNITY_VOTE_CHANGED_EVENT, onVote);
    window.addEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, reload);
    window.addEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
    return () => {
      window.removeEventListener(COMMUNITY_VOTE_CHANGED_EVENT, onVote);
      window.removeEventListener(COMMUNITY_FILTERS_CHANGED_EVENT, reload);
      window.removeEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
    };
  }, [reload]);

  useEffect(() => {
    if (!maySearch || !linkKey) { setViews({}); return; }
    const controller = new AbortController();
    for (const linkId of linkKey.split("\n")) {
      fetchCommunityMaster(linkId, controller.signal)
        .then((view) => {
          if (!controller.signal.aborted) setViews((current) => ({ ...current, [linkId]: { filters: view.filters, retired: view.retiredVotes ?? {} } }));
        })
        .catch((reason) => {
          // A link the reviewer can't read stays unreadable; anything else may load on retry.
          const retryable = (reason as { code?: string })?.code !== "not_found";
          if (!controller.signal.aborted) setViews((current) => ({ ...current,
            [linkId]: { error: retryable ? voteUnavailable.loadFailed : masterErrorText(reason), retryable } }));
        });
    }
    return () => controller.abort();
  }, [maySearch, linkKey, attempt]);

  /** The vote target of a community review filter. */
  const targetFor = (filter: ReviewFilter): ReviewVoteTarget => {
    const filterId = filter.community_filter_id ?? "";
    const source = sources.find((item) => item.source_id === filter.community_source_id);
    const linkId = source?.link_id && source.state !== "link_unavailable" ? source.link_id : null;
    const loaded = linkId ? views[linkId] : undefined;
    const served = loaded && "filters" in loaded ? servedFilterFor(loaded.filters, filterId) : null;
    // A filter the link no longer serves shows the last vote stored for it.
    const myVote = served ? served.myVote : loaded && "retired" in loaded ? loaded.retired[filterId] ?? null : null;
    const target = { linkId, filterId: served?.filterId ?? filterId, myVote };
    if (!mayVote || !maySearch) return { ...target, unavailable: voteUnavailable.permission };
    if (!linkId) return { ...target, unavailable: voteUnavailable.detached };
    if (!loaded) return { ...target, unavailable: voteUnavailable.loading };
    if ("error" in loaded) return { ...target, unavailable: loaded.error };
    if (!served) return { ...target, unavailable: voteUnavailable.notServed };
    return { ...target, unavailable: null };
  };
  /** The vote pill of a community review filter; `name` describes the filter. */
  const renderVote = (filter: ReviewFilter, name: string) => {
    if (!sources.some((source) => source.source_id === filter.community_source_id)) return null;
    const target = targetFor(filter);
    return <MasterVote key={`${target.linkId}:${target.filterId}`} linkId={target.linkId} filterId={target.filterId}
      name={name} myVote={target.myVote} unavailable={target.unavailable} />;
  };
  /** Whether a snapshot's link failed to load votes in a way Retry may fix. */
  const loadFailed = (linkId: string | null) => {
    const loaded = linkId ? views[linkId] : undefined;
    return !!loaded && "error" in loaded && loaded.retryable;
  };
  return { targetFor, renderVote, loadFailed, retry: reload };
}
