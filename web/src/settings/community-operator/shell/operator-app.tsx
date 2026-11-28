import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readOperatorQueue, type QueueCounts } from "../../../api/api-community-operator-community";
import { readOperatorHealth, type OperatorHealth } from "../../../api/api-community-operator-system";
import { accessFailureStatus } from "../../../api/api-community-operator";
import type { OperatorSession } from "../runtime";
import { age } from "../shared/format";
import { OperatorContext, type OperatorServices } from "../shared/operator-data";
import { operatorRoute, type OperatorRoute } from "../routes";
import { Sidebar } from "./sidebar";
import { GlobalSearch } from "./global-search";
import { OperatorPageView } from "./page-view";

const narrowQuery = "(max-width: 900px)";
function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia?.(narrowQuery).matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.(narrowQuery);
    if (!media) return;
    const change = () => setNarrow(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  return narrow;
}

/** The verified operator app: sidebar (a drawer below 900px), top-bar search and the hash-routed page. */
export function OperatorApp({ session, onAccessFailure, onReverify }: { session: OperatorSession;
  onAccessFailure: (status: number) => void; onReverify: () => void }) {
  const [route, setRoute] = useState<OperatorRoute>(() => operatorRoute());
  const page = useRef(route);
  const [drawer, setDrawer] = useState(false);
  const [counts, setCounts] = useState<QueueCounts | null>(null);
  const [health, setHealth] = useState<OperatorHealth | null>(null);
  const [observedAt, setObservedAt] = useState<number | null>(null);
  const [statusAttempt, setStatusAttempt] = useState(0);
  const [, setTick] = useState(0);
  const narrow = useNarrow();
  useEffect(() => {
    const changed = () => {
      const next = operatorRoute();
      // Scroll to the top for a new page; parameter changes on the same page (filters, history pages) keep the position.
      if (next.page !== page.current.page || next.path.join("/") !== page.current.path.join("/")) window.scrollTo?.(0, 0);
      page.current = next; setRoute(next); setDrawer(false);
    };
    window.addEventListener("hashchange", changed);
    const timer = window.setInterval(() => setTick(value => value + 1), 10000);
    return () => { window.removeEventListener("hashchange", changed); window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!drawer) return;
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") setDrawer(false); };
    window.addEventListener("keydown", key);
    document.querySelector<HTMLElement>(".sidebar .navlink.on, .sidebar .navlink")?.focus();
    return () => window.removeEventListener("keydown", key);
  }, [drawer]);
  useEffect(() => {
    const controller = new AbortController();
    const denied = (error: unknown) => {
      if (!controller.signal.aborted && accessFailureStatus(error)) onAccessFailure(accessFailureStatus(error)!);
    };
    readOperatorQueue("all", null, controller.signal).then(page => { if (!controller.signal.aborted) setCounts(page.counts ?? null); }).catch(denied);
    readOperatorHealth(controller.signal).then(value => { if (!controller.signal.aborted) setHealth(value); }).catch(denied);
    return () => controller.abort();
  }, [statusAttempt]);
  const refreshCounts = useCallback(() => setStatusAttempt(value => value + 1), []);
  const services = useMemo<OperatorServices>(() => ({ subject: session.subject, counts, onAccessFailure, refreshCounts, setObservedAt }),
    [session.subject, counts, onAccessFailure, refreshCounts]);
  return <OperatorContext.Provider value={services}>
    <div className={`shell${drawer ? " nav-open" : ""}`}>
      <Sidebar id="operator-sidebar" route={route} counts={counts} health={health} subject={session.subject}
        onReverify={onReverify} onNavigate={() => setDrawer(false)} inert={narrow && !drawer} />
      {drawer && <div className="scrim" aria-hidden="true" onClick={() => setDrawer(false)} />}
      <div className="main-col" inert={narrow && drawer}>
        <header className="topbar">
          <button type="button" className="menu-btn" aria-label="Open navigation" aria-controls="operator-sidebar" aria-expanded={drawer}
            onClick={() => setDrawer(true)}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 6h16M4 12h16M4 18h16" /></svg></button>
          <GlobalSearch />
          <div className="top-right"><span className="hide-sm">{observedAt ? `Updated ${age(observedAt)} ago` : "Not yet updated"}</span>
            <span className="me" title={session.subject} aria-hidden="true">{session.subject.slice(0, 1).toUpperCase()}</span></div>
        </header>
        <main id="operator-main"><OperatorPageView route={route} /></main>
      </div>
    </div>
  </OperatorContext.Provider>;
}
