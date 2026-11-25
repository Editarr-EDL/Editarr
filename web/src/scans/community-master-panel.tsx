import { useCallback, useEffect, useState } from "react";
import { listCommunityMasters } from "../api/api-community-masters";
import type { MasterLinkSummary, MasterTarget } from "../api/api-community-master-types";
import { useCan } from "../auth/auth-gate";
import { masterErrorText } from "./community-master-format";
import { COMMUNITY_MASTERS_CHANGED_EVENT } from "./community-master-events";
import { MasterLinkCard } from "./master-link-card";

/**
 * The current account's community master sets on one local version, as link cards. Renders nothing
 * without `community.search`; without links it shows `emptyText`, or nothing when that is omitted.
 */
export function CommunityMasterPanel({ target, emptyText }: { target: MasterTarget; emptyText?: string }) {
  const maySearch = useCan("community.search");
  const [links, setLinks] = useState<MasterLinkSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const { item_id: itemId, media_source_id: mediaSourceId } = target;
  const reload = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    window.addEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
    return () => window.removeEventListener(COMMUNITY_MASTERS_CHANGED_EVENT, reload);
  }, [reload]);
  useEffect(() => {
    if (!maySearch) return;
    const controller = new AbortController();
    setError(null);
    listCommunityMasters(itemId, mediaSourceId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setLinks(Array.isArray(value.links) ? value.links : []);
    }).catch((reason) => {
      if (controller.signal.aborted) return;
      setLinks([]);
      // Only retryable failures are worth surfacing; missing access or links simply hide the section.
      if (reason?.retryable) setError(masterErrorText(reason));
    });
    return () => controller.abort();
  }, [maySearch, itemId, mediaSourceId, attempt]);

  if (!maySearch) return null;
  if (!links.length && !error) return emptyText ? <p className="muted-copy" role="status">{emptyText}</p> : null;
  const itemTarget = { source: "jellyfin" as const, item_id: itemId, media_source_id: mediaSourceId };
  return <section className="community-masters" aria-label="Community filter sets">
    {error && <p className="form-error" role="alert">{error}{" "}
      <button type="button" className="btn-secondary compact-button" onClick={reload}>Retry</button></p>}
    {links.map((link) => <MasterLinkCard key={link.linkId} summary={link} target={itemTarget} onChanged={reload} />)}
  </section>;
}
