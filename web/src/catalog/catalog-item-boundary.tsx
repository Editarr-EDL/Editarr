import { useLayoutEffect, useState, type ReactNode } from "react";
import { watchMediaAccess } from "../api/media-access";
import { usePageTitle } from "../shared/display";

/** Own target denial separately from principal revisions and discard the whole workspace. */
export function CatalogItemBoundary({ source, itemId, onUnavailable, children }: {
  source: string; itemId: string; onUnavailable: () => boolean | void; children: ReactNode;
}) {
  const [state, setState] = useState<"available" | "unavailable" | "draft_lost">("available");
  const unavailable = state !== "available";
  usePageTitle(unavailable ? "Media unavailable" : "Library");
  useLayoutEffect(() => {
    if (unavailable) return;
    let refused = false;
    return watchMediaAccess({ source, itemId }, () => {
      if (refused) return;
      refused = true;
      setState(onUnavailable() ? "draft_lost" : "unavailable");
    });
  }, [source, itemId, onUnavailable, unavailable]);
  if (unavailable) return <div className="content-page"><div className="load-error" role="alert">
    <span>Media unavailable</span>
    {state === "draft_lost" && <p>Your unsaved review draft could not be kept because this media is no longer available.</p>}
    <button className="btn-secondary" onClick={() => setState("available")}>Retry</button>
  </div></div>;
  return children;
}