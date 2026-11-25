export const COMMUNITY_MASTERS_CHANGED_EVENT = "editarr:community-masters-changed";

/** Tell open item pages that master links or their pending updates changed, so they reload. */
export function notifyCommunityMastersChanged() {
  window.dispatchEvent(new Event(COMMUNITY_MASTERS_CHANGED_EVENT));
}

export const COMMUNITY_FILTERS_CHANGED_EVENT = "editarr:community-filters-changed";

/** Tell open reviews that a link's active filters may have changed (decisions, preferences,
 * alignment or accepted updates), so they compare their stored snapshots again. */
export function notifyCommunityFiltersChanged() {
  window.dispatchEvent(new Event(COMMUNITY_FILTERS_CHANGED_EVENT));
}
