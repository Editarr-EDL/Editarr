export const COMMUNITY_MASTERS_CHANGED_EVENT = "editarr:community-masters-changed";

/** Tell open item pages that master links or their pending updates changed, so they reload. */
export function notifyCommunityMastersChanged() {
  window.dispatchEvent(new Event(COMMUNITY_MASTERS_CHANGED_EVENT));
}
