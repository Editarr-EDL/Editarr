import {
  useCallback,
  useEffect,
  useRef,
  useState,
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
import { AppSidebar } from "./ui/AppSidebar";
import { EmptyPage } from "./ui/EmptyPage";
import { navigate, parseRoute, isRootRoute, type Route } from "./routes";
import { writeUserValue } from "./shared/user-storage";
import { usePrincipal } from "./auth/auth-gate";
import { homeRoute, mayOpen } from "./auth/route-access";
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
    if (reviewDirtyRef.current && !window.confirm("Discard unsaved review changes and leave this page?")) return false;
    reviewDirtyRef.current = false;
    setReviewDirty(false);
    navigate(nextRoute);
    return true;
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

  return <div className="editarr-shell">
    <AppSidebar principal={principal} route={route} libraries={libraries} settings={settings}
      error={navigationError} onRetry={() => void refreshNavigation()} onNavigate={guardedNavigate} />
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

export default App;
