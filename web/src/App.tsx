import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  fetchJellyfinSettings,
  fetchLibraries,
} from "./api/api-service";
import type {
  JellyfinLibrary,
  JellyfinMediaPage,
  JellyfinSettings,
} from "./api/api-media-types";
import { usePageTitle } from "./shared/display";
import { SettingsView, settingsSectionTitle } from "./settings/settings-view";
import { UsersView } from "./settings/users/users-view";
import { ProfileView } from "./settings/profile/profile-view";
import { ScansFloatingPanel } from "./scans/scans-floating-panel";
import { ArchiveView } from "./catalog/archive-view";
import { LibraryViewPage, MediaDetailPage, SeriesSeasonsPage, SeasonEpisodesPage } from "./catalog/library-pages";
import { EmptyPage } from "./ui/EmptyPage";
import { navigate, parseRoute, isRootRoute, libraryLastRoute, type Route } from "./routes";
import { applicationAssetUrl } from "./shared/runtime-urls";
import { writeUserValue } from "./shared/user-storage";
import { usePrincipal } from "./auth/auth-gate";
import { AccountMenu } from "./auth/account-menu";
import { homeRoute, mayOpen, maySeeSettings } from "./auth/route-access";
import { can } from "./auth/session";
import { AuditHistory } from "./settings/audit/audit-history";
import { CatalogItemBoundary } from "./catalog/catalog-item-boundary";

/** Own the hash route, navigation shell, and shared source caches for the signed-in account. */
function App() {
  const principal = usePrincipal();
  const mayBrowse = principal !== null && can(principal, "media.browse");
  const mayViewConnections = principal !== null && can(principal, "connections.view");
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const [settings, setSettings] = useState<JellyfinSettings | null>(null);
  const [libraries, setLibraries] = useState<JellyfinLibrary[]>([]);
  const [navigationError, setNavigationError] = useState<string | null>(null);
  const [librariesExpanded, setLibrariesExpanded] = useState(true);
  const [settingsExpanded, setSettingsExpanded] = useState(true);
  const [reviewDirty, setReviewDirty] = useState(false);
  const reviewDirtyRef = useRef(false);
  const acceptedHashRef = useRef(window.location.hash);
  const libraryPageCacheRef = useRef<Map<string, JellyfinMediaPage>>(new Map());
  const clearUnavailableMedia = useCallback(() => {
    const draftLost = reviewDirtyRef.current;
    libraryPageCacheRef.current.clear();
    reviewDirtyRef.current = false;
    setReviewDirty(false);
    return draftLost;
  }, []);

  const refreshNavigation = useCallback(async () => {
    if (!mayBrowse) return;
    if (!mayViewConnections) {
      setLibraries(await fetchLibraries().catch(() => []));
      return;
    }
    try {
      const currentSettings = await fetchJellyfinSettings();
      setSettings(currentSettings);
      if (!currentSettings.api_key_configured) {
        setLibraries([]);
        setNavigationError(null);
        return;
      }
      const loadedLibraries = await fetchLibraries();
      setLibraries(loadedLibraries);
      setNavigationError(null);
      if (isRootRoute() && loadedLibraries[0]) {
        navigate(`library/${encodeURIComponent(loadedLibraries[0].id)}`);
      }
    } catch (error) {
      setNavigationError(error instanceof Error ? error.message : "Unable to load Jellyfin libraries.");
    }
  }, [mayBrowse, mayViewConnections]);

  useEffect(() => {
    if (principal !== null && isRootRoute() && !mayOpen(principal, parseRoute())) {
      window.history.replaceState(null, "", `#/${homeRoute(principal)}`);
      acceptedHashRef.current = window.location.hash;
      setRoute(parseRoute());
    }
  }, [principal]);

  useEffect(() => {
    void refreshNavigation();
  }, [refreshNavigation]);
  useEffect(() => {
    const onHashChange = () => {
      if (
        reviewDirtyRef.current
        && window.location.hash !== acceptedHashRef.current
        && !window.confirm("Discard unsaved review changes and leave this page?")
      ) {
        window.history.replaceState(null, "", acceptedHashRef.current || "#/");
        return;
      }
      reviewDirtyRef.current = false;
      setReviewDirty(false);
      acceptedHashRef.current = window.location.hash;
      setRoute(parseRoute());
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    reviewDirtyRef.current = reviewDirty;
  }, [reviewDirty]);
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!reviewDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [reviewDirty]);
  const guardedNavigate = useCallback((nextRoute: string) => {
    if (reviewDirtyRef.current && !window.confirm("Discard unsaved review changes and leave this page?")) return;
    reviewDirtyRef.current = false;
    setReviewDirty(false);
    navigate(nextRoute);
  }, []);
  useEffect(() => {
    if (route.kind !== "library") return;
    writeUserValue(
      `library-last-route.${route.libraryId}`,
      window.location.hash.replace(/^#\/?/, "") || `library/${encodeURIComponent(route.libraryId)}`,
    );
  }, [route]);

  const activeLibrary = route.kind === "library"
    ? libraries.find((library) => library.id === route.libraryId)
    : undefined;
  const routeTitle = route.kind === "settings"
    ? `Settings — ${settingsSectionTitle(route.section)}`
    : route.kind === "audit" ? "Audit history"
    : route.kind === "archive" ? "Archive"
    : activeLibrary?.name || "Library";
  usePageTitle(routeTitle);

  if (principal === null) return null;
  const allowed = mayOpen(principal, route);
  const settingsSections = (["users", "connections", "filters", "models", "general"] as const)
    .filter((section) => maySeeSettings(principal, section));

  return <div className="editarr-shell">
    <aside className="app-sidebar" aria-label="Editarr navigation">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true"><img src={applicationAssetUrl("editarr-app-icon.png?v=1")} alt="" /></span><span>Editarr</span>
        <AccountMenu principal={principal} onOpenProfile={() => guardedNavigate("settings/profile")} />
      </div>
      <nav className="sidebar-scroll" aria-label="Primary navigation">
        {mayBrowse && <SidebarDisclosure label="Jellyfin Libraries" expanded={librariesExpanded} onToggle={() => setLibrariesExpanded((value) => !value)}>
          {mayViewConnections && !settings?.api_key_configured && <p className="sidebar-hint">Connect Jellyfin in Settings.</p>}
          {!mayViewConnections && libraries.length === 0 && <p className="sidebar-hint">No libraries are available.</p>}
          {libraries.map((library) => <button
            className={`sidebar-link nested ${route.kind === "library" && route.libraryId === library.id ? "active" : ""}`}
            key={library.id}
            onClick={() => guardedNavigate(libraryLastRoute(library.id))}
            aria-current={route.kind === "library" && route.libraryId === library.id ? "page" : undefined}
          >{library.name}</button>)}
        </SidebarDisclosure>}
        <div className="settings-link">
          <SidebarDisclosure label="Settings" expanded={settingsExpanded} onToggle={() => setSettingsExpanded((value) => !value)}>
            {settingsSections.map((section) => <button
              key={section}
              className={`sidebar-link source-subsection-link ${route.kind === "settings" && route.section === section ? "active" : ""}`}
              onClick={() => guardedNavigate(`settings/${section}`)}
              aria-current={route.kind === "settings" && route.section === section ? "page" : undefined}
            >{settingsSectionTitle(section)}</button>)}
          </SidebarDisclosure>
        </div>
        {mayOpen(principal, { kind: "archive" }) && <button className={`sidebar-link ${route.kind === "archive" ? "active" : ""}`} onClick={() => guardedNavigate("archive")} aria-current={route.kind === "archive" ? "page" : undefined}>Archive</button>}
        {mayOpen(principal, { kind: "audit" }) && <button className={`sidebar-link ${route.kind === "audit" ? "active" : ""}`} onClick={() => guardedNavigate("audit")} aria-current={route.kind === "audit" ? "page" : undefined}>Audit history</button>}
      </nav>
      {navigationError && <div className="sidebar-error" role="alert"><span>{navigationError}</span><button onClick={() => void refreshNavigation()}>Retry</button></div>}
    </aside>
    <main className="app-main">
      {!allowed && <EmptyPage title="Page unavailable" body="Your account cannot open this page. Choose another page from the navigation." />}
      {allowed && <>
      {route.kind === "audit" && <AuditHistory eventId={route.eventId} />}
      {route.kind === "settings" && route.section === "users" && <UsersView deleteAccountId={route.deleteAccountId} />}
      {route.kind === "settings" && route.section === "profile" && <ProfileView />}
      {route.kind === "settings" && route.section !== "users" && route.section !== "profile" && <SettingsView section={route.section} filterTab={route.filterTab} settings={settings} onSaved={async () => { libraryPageCacheRef.current.clear(); await refreshNavigation(); }} />}
      {route.kind === "library" && activeLibrary && route.itemId && <CatalogItemBoundary key={`jellyfin:${route.itemId}`} source="jellyfin" itemId={route.itemId} onUnavailable={clearUnavailableMedia}><MediaDetailPage library={activeLibrary} itemId={route.itemId} seriesId={route.seriesId} seasonId={route.seasonId} onNavigate={guardedNavigate} onDirtyChange={setReviewDirty} /></CatalogItemBoundary>}
      {route.kind === "library" && activeLibrary && route.seriesId && route.seasonId && !route.itemId && <SeasonEpisodesPage library={activeLibrary} seriesId={route.seriesId} seasonId={route.seasonId} />}
      {route.kind === "library" && activeLibrary && route.seriesId && !route.seasonId && !route.itemId && <SeriesSeasonsPage library={activeLibrary} seriesId={route.seriesId} />}
      {route.kind === "library" && activeLibrary && !route.itemId && !route.seriesId && <LibraryViewPage key={activeLibrary.id} library={activeLibrary} cache={libraryPageCacheRef.current} />}
      {route.kind === "library" && !activeLibrary && <EmptyPage title="Library unavailable" body="Reconnect Jellyfin or choose a library from the sidebar." />}
      {route.kind === "archive" && <ArchiveView />}
      </>}
    </main>
    {can(principal, "scan_jobs.view.own") && <ScansFloatingPanel />}
  </div>;
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

export default App;
