import type { ReactNode } from "react";
import logo from "../../../../public/apple-touch-icon.png";
import type { QueueCounts } from "../../../api/api-community-operator-community";
import type { OperatorHealth } from "../../../api/api-community-operator-system";
import { ago } from "../shared/format";
import type { OperatorRoute } from "../routes";

const icons: Record<string, ReactNode> = {
  home: <path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  items: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16" /></>,
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  contributors: <><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a5.5 5.5 0 0 1 3.5 6" /></>,
  queue: <path d="M4 6h16M4 12h16M4 18h10" />,
  backups: <><ellipse cx="12" cy="5.5" rx="8" ry="2.5" /><path d="M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5" /></>,
  health: <path d="M20.8 8.6a5 5 0 0 0-8.8-3.2 5 5 0 0 0-8.8 3.2c0 5.4 8.8 11 8.8 11s8.8-5.6 8.8-11z" />,
  audit: <path d="M9 4h6M8 4H6a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1h-2M9 11h6M9 15h4" />,
};
type Entry = { id: string; label: string; href: string; icon?: string; sub?: boolean; count?: number; tone?: string };
const queueEntries = (counts: QueueCounts | null): Entry[] => [
  { id: "queue/all", label: "Queue", href: "#/queue", icon: "queue", count: counts?.all, tone: "warn" },
  { id: "queue/reports", label: "Reports", href: "#/queue/reports", sub: true, count: counts?.reports, tone: "bad" },
  { id: "queue/aliases", label: "Alias suggestions", href: "#/queue/aliases", sub: true, count: counts?.aliases, tone: "blue" },
  { id: "queue/versions", label: "Candidate versions", href: "#/queue/versions", sub: true, count: counts?.versions, tone: "blue" },
  { id: "queue/quarantined", label: "Quarantined", href: "#/queue/quarantined", sub: true, count: counts?.quarantined },
];

/** The sidebar entry that matches the route ("queue/<kind>" for the queue; item pages highlight Items). */
export function activeEntry(route: OperatorRoute) {
  if (route.page === "queue") return `queue/${route.path[0] ?? "all"}`;
  if (route.page === "filters") return "items";
  if (route.page === "inspect") return "health";
  return route.page;
}

/** Operator navigation with queue badges, service readiness and the verified Access subject. */
export function Sidebar({ route, counts, health, subject, onReverify, onNavigate, inert, id }: { route: OperatorRoute; counts: QueueCounts | null;
  health: OperatorHealth | null; subject: string; onReverify: () => void; onNavigate: () => void; inert: boolean; id: string }) {
  const active = activeEntry(route);
  const groups: [string | null, Entry[]][] = [
    [null, [{ id: "home", label: "Home", href: "#/home", icon: "home" }]],
    ["Catalog", [{ id: "items", label: "Items", href: "#/items", icon: "items" },
      { id: "activity", label: "Activity", href: "#/activity", icon: "activity" },
      { id: "contributors", label: "Contributors", href: "#/contributors", icon: "contributors" }]],
    ["Moderate", queueEntries(counts)],
    ["Records", [{ id: "versions", label: "All versions", href: "#/versions", icon: "items" },
      { id: "aliases", label: "All alias suggestions", href: "#/aliases", icon: "audit" },
      { id: "reports", label: "All reports", href: "#/reports", icon: "queue" }]],
    ["System", [{ id: "backups", label: "Backups", href: "#/backups", icon: "backups" },
      { id: "health", label: "Health & tools", href: "#/health", icon: "health" },
      { id: "audit", label: "Audit log", href: "#/audit", icon: "audit" }]],
  ];
  const failing = health ? health.checks.filter(check => !check.ok).length : 0;
  return <aside className="sidebar" id={id} aria-label="Operator navigation" inert={inert}>
    <div className="brand"><img src={logo} alt="" width="30" height="30" /><div>Editarr Community<small>OPERATOR</small></div></div>
    <nav aria-label="Operator pages">{groups.map(([title, entries]) => <div className="navgroup" key={title ?? "top"}>
      {title && <h2 className="navtitle">{title}</h2>}
      {entries.map(entry => <a key={entry.id} href={entry.href} className={`navlink${entry.sub ? " sub" : ""}${entry.id === active ? " on" : ""}`}
        aria-current={entry.id === active ? "page" : undefined} onClick={onNavigate}>
        {entry.icon && <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{icons[entry.icon]}</svg>}{entry.label}
        {entry.count !== undefined && entry.count > 0 && <span className={`n${entry.tone ? ` ${entry.tone}` : ""}`}>
          {entry.count}<span className="sr"> open</span></span>}</a>)}
    </div>)}</nav>
    <div className="side-foot">
      {health ? <><div><span className={`live${health.ready ? "" : " bad"}`} aria-hidden="true" />
        <b>{health.ready ? "Service ready" : "Service not ready"}</b></div>
        <div>{failing ? `${failing} of ${health.checks.length} checks failing` : `${health.checks.length} checks passing`}</div>
        {health.lastRetention && <div>Retention sweep {ago(health.lastRetention.startedAt)}</div>}</> :
        <div className="faint">Service status unavailable</div>}
      <div className="op"><span className="me sm" aria-hidden="true">{subject.slice(0, 1).toUpperCase()}</span>
        <span><span className="subject">{subject}</span><span className="faint via">via Cloudflare Access ·{" "}
          <button type="button" className="linkish" onClick={onReverify}>Refresh access</button></span></span></div>
    </div>
  </aside>;
}
