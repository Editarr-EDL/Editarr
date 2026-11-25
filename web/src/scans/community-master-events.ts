export const COMMUNITY_MASTERS_CHANGED_EVENT = "editarr:community-masters-changed";

/** Tell open item pages that master links changed, so they reload. */
export function notifyCommunityMastersChanged() {
  window.dispatchEvent(new Event(COMMUNITY_MASTERS_CHANGED_EVENT));
}

export const COMMUNITY_FILTERS_CHANGED_EVENT = "editarr:community-filters-changed";

/** Tell open reviews that a link's served filters may have changed (a downloaded revision or an
 * approved alignment), so they read their stored snapshots' states again. */
export function notifyCommunityFiltersChanged() {
  window.dispatchEvent(new Event(COMMUNITY_FILTERS_CHANGED_EVENT));
}
