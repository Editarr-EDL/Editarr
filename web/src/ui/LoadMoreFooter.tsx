import { useEffect, useRef, type RefObject } from "react";

/**
 * Call `onNear` whenever `sentinel` comes within `margin` of the viewport while `enabled`. Changing `revision`
 * observes again, so a sentinel still in view after a load asks again.
 */
function useNearViewport(sentinel: RefObject<HTMLElement | null>, enabled: boolean, margin: string, revision: unknown, onNear: () => void) {
  const request = useRef(onNear);
  request.current = onNear;
  useEffect(() => {
    const element = sentinel.current;
    if (!enabled || !element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) request.current();
    }, { rootMargin: margin });
    observer.observe(element);
    return () => observer.disconnect();
  }, [enabled, margin, revision, sentinel]);
}

/**
 * Footer of a continuously loading list: it asks for more as it nears the viewport (an IntersectionObserver
 * sentinel), offers a "Load more" button for keyboard and assistive-technology users, and announces the
 * shown range politely.
 * @param start Index of the first loaded item, when the list starts part-way.
 * @param status Replaces the count announcement, e.g. while a jump loads further items.
 */
export function LoadMoreFooter({ start = 0, loaded, total, loading, complete, onLoadMore, status }: {
  start?: number;
  loaded: number;
  total: number;
  loading: boolean;
  complete: boolean;
  onLoadMore: () => void;
  status?: string | null;
}) {
  const sentinel = useRef<HTMLDivElement>(null);
  useNearViewport(sentinel, !complete, "0px 0px 800px 0px", start + loaded, onLoadMore);
  const range = start ? `${start + 1}–${start + loaded}` : String(loaded);
  return <div className="load-more-footer">
    <div ref={sentinel} className="load-more-sentinel" aria-hidden="true" />
    <p role="status">{status || `Showing ${range} of ${total}`}</p>
    {!complete && <button type="button" className="btn-secondary compact-button" aria-disabled={loading}
      onClick={() => { if (!loading) onLoadMore(); }}>
      {loading ? "Loading…" : "Load more"}
    </button>}
  </div>;
}

/** Header of a list loaded from part-way: loads the items before it as it nears the viewport or on request. */
export function LoadEarlierHeader({ start, loading, onLoadEarlier }: { start: number; loading: boolean; onLoadEarlier: () => void }) {
  const sentinel = useRef<HTMLDivElement>(null);
  useNearViewport(sentinel, start > 0, "800px 0px 0px 0px", start, onLoadEarlier);
  if (!start) return null;
  return <div className="load-earlier-header">
    <div ref={sentinel} className="load-more-sentinel" aria-hidden="true" />
    <button type="button" className="btn-secondary compact-button" aria-disabled={loading}
      onClick={() => { if (!loading) onLoadEarlier(); }}>
      {loading ? "Loading…" : "Load earlier"}
    </button>
  </div>;
}
