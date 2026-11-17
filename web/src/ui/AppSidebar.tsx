import { useEffect, useRef, useState, type ReactNode } from "react";
import type { JellyfinLibrary, JellyfinSettings } from "../api/api-media-types";
import { AccountMenu } from "../auth/account-menu";
import { mayOpen, maySeeSettings } from "../auth/route-access";
import { can, type Principal } from "../auth/session";
import { libraryLastRoute, type Route } from "../routes";
import { settingsSectionTitle } from "../settings/settings-view";
import { applicationAssetUrl } from "../shared/runtime-urls";

const compactSidebar = "(max-width: 760px)";

export function AppSidebar({ principal, route, libraries, settings, error, onRetry, onNavigate }: {
  principal: Principal;
  route: Route;
  libraries: JellyfinLibrary[];
  settings: JellyfinSettings | null;
  error: string | null;
  onRetry: () => void;
  onNavigate: (route: string) => boolean;
}) {
  const [compact, setCompact] = useState(() => window.matchMedia?.(compactSidebar).matches ?? false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [librariesExpanded, setLibrariesExpanded] = useState(true);
  const [settingsExpanded, setSettingsExpanded] = useState(true);
  const drawer = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const query = window.matchMedia?.(compactSidebar);
    if (!query) return;
    const update = () => {
      if (!query.matches) {
        drawer.current?.close();
        setDrawerOpen(false);
      }
      setCompact(query.matches);
    };
    query.addEventListener("change", update);
    update();
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => { drawer.current?.close(); }, [route]);

  function openDrawer() {
    drawer.current?.showModal();
    setDrawerOpen(true);
  }

  function openRoute(nextRoute: string) {
    if (onNavigate(nextRoute)) drawer.current?.close();
  }

  const mayViewConnections = can(principal, "connections.view");
  const settingsSections = (["users", "connections", "filters", "models", "general"] as const)
    .filter((section) => maySeeSettings(principal, section));
  const navigation = <>
    <nav className="sidebar-scroll" aria-label="Primary navigation">
      {can(principal, "media.browse") && <SidebarDisclosure label="Jellyfin Libraries" expanded={librariesExpanded} onToggle={() => setLibrariesExpanded((value) => !value)}>
        {mayViewConnections && !settings?.api_key_configured && <p className="sidebar-hint">Connect Jellyfin in Settings.</p>}
        {!mayViewConnections && libraries.length === 0 && <p className="sidebar-hint">No libraries are available.</p>}
        {libraries.map((library) => <button
          className={`sidebar-link nested ${route.kind === "library" && route.libraryId === library.id ? "active" : ""}`}
          key={library.id}
          onClick={() => openRoute(libraryLastRoute(library.id))}
          aria-current={route.kind === "library" && route.libraryId === library.id ? "page" : undefined}
        >{library.name}</button>)}
      </SidebarDisclosure>}
      <div className="settings-link">
        <SidebarDisclosure label="Settings" expanded={settingsExpanded} onToggle={() => setSettingsExpanded((value) => !value)}>
          {settingsSections.map((section) => <button
            key={section}
            className={`sidebar-link source-subsection-link ${route.kind === "settings" && route.section === section ? "active" : ""}`}
            onClick={() => openRoute(`settings/${section}`)}
            aria-current={route.kind === "settings" && route.section === section ? "page" : undefined}
          >{settingsSectionTitle(section)}</button>)}
        </SidebarDisclosure>
      </div>
      {mayOpen(principal, { kind: "archive" }) && <button className={`sidebar-link ${route.kind === "archive" ? "active" : ""}`} onClick={() => openRoute("archive")} aria-current={route.kind === "archive" ? "page" : undefined}>Archive</button>}
      {mayOpen(principal, { kind: "audit" }) && <button className={`sidebar-link ${route.kind === "audit" ? "active" : ""}`} onClick={() => openRoute("audit")} aria-current={route.kind === "audit" ? "page" : undefined}>Audit history</button>}
    </nav>
    {error && <div className="sidebar-error" role="alert"><span>{error}</span><button onClick={onRetry}>Retry</button></div>}
  </>;

  return <aside className="app-sidebar" aria-label="Editarr navigation">
    <div className="brand">
      {compact && <button type="button" className="sidebar-toggle" aria-label="Open navigation" aria-expanded={drawerOpen} aria-controls="sidebar-drawer" aria-haspopup="dialog" onClick={openDrawer}>
        <span className="sidebar-hamburger" aria-hidden="true" />
      </button>}
      <span className="brand-mark" aria-hidden="true"><img src={applicationAssetUrl("editarr-app-icon.png?v=1")} alt="" /></span><span>Editarr</span>
      <AccountMenu principal={principal} onOpenProfile={() => openRoute("settings/profile")} />
    </div>
    {compact ? <dialog
      className="sidebar-drawer"
      id="sidebar-drawer"
      ref={drawer}
      aria-label="Navigation menu"
      onClose={(event) => setDrawerOpen(event.currentTarget.open)}
      onClick={(event) => { if (event.target === event.currentTarget) drawer.current?.close(); }}
    >
      <div className="sidebar-drawer-panel">
        <button type="button" className="sidebar-close" aria-label="Close navigation" onClick={() => drawer.current?.close()}>×</button>
        {navigation}
      </div>
    </dialog> : navigation}
  </aside>;
}

function SidebarDisclosure({ label, expanded, onToggle, children }: {
  label: string;
  expanded: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const id = `sidebar-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return <section className="sidebar-section">
    <button className="sidebar-disclosure" onClick={onToggle} aria-expanded={expanded} aria-controls={id}>
      <span>{label}</span><span aria-hidden="true">{expanded ? "⌄" : "›"}</span>
    </button>
    {expanded && <div id={id} className="sidebar-children">{children}</div>}
  </section>;
}
