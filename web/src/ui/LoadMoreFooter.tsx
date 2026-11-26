import { useEffect, useRef } from "react";

/**
 * Footer of a continuously loading list: it asks for more as it nears the viewport (an IntersectionObserver
 * sentinel), offers a "Load more" button for keyboard and assistive-technology users, and announces
 * "Showing N of M" politely.
 * @param status Replaces the count announcement, e.g. while a jump loads further items.
 */
export function LoadMoreFooter({ loaded, total, loading, complete, onLoadMore, status }: {
  loaded: number;
  total: number;
  loading: boolean;
  complete: boolean;
  onLoadMore: () => void;
  status?: string | null;
}) {
  const sentinel = useRef<HTMLDivElement>(null);
  const request = useRef(onLoadMore);
  request.current = onLoadMore;
  useEffect(() => {
    const element = sentinel.current;
    if (complete || !element || typeof IntersectionObserver === "undefined") return;
    // Observing again after each load re-reports a sentinel that is still in view.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) request.current();
    }, { rootMargin: "0px 0px 800px 0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, [complete, loaded]);
  return <div className="load-more-footer">
    <div ref={sentinel} className="load-more-sentinel" aria-hidden="true" />
    <p role="status">{status || `Showing ${loaded} of ${total}`}</p>
    {!complete && <button type="button" className="btn-secondary compact-button" aria-disabled={loading}
      onClick={() => { if (!loading) onLoadMore(); }}>
      {loading ? "Loading…" : "Load more"}
    </button>}
  </div>;
}
