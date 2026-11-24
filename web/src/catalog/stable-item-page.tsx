import { useEffect, useState } from "react";

import type { JellyfinLibrary, JellyfinMediaItem } from "../api/api-media-types";
import { fetchSavedReview } from "../api/api-reviews";
import type { SavedReview } from "../api/api-review-types";
import { fetchEditarrItem, resolveLegacyItem, type EditarrItem, type HistoricalVersion } from "../api/api-stable-media";
import { CatalogItemBoundary } from "./catalog-item-boundary";
import { editarrItemRoute, type Route } from "../routes";
import { scanLabel, usePageTitle } from "../shared/display";
import { writeUserValue } from "../shared/user-storage";
import { MediaDetailPage, SeriesSeasonsPage } from "./library-pages";
import { LibraryBreadcrumbs, MediaDetail } from "./media-detail";

type ItemRoute = Extract<Route, { kind: "library" | "title" }>;
type ExplicitVersion = { historical: HistoricalVersion }
  | { item: JellyfinMediaItem; sourceId: string | null };

interface PageProps {
  route: ItemRoute;
  libraries: JellyfinLibrary[];
  onNavigate: (path: string) => boolean;
  onReplace: (path: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  onUnavailable: () => void;
}

function HistoricalReview({ id }: { id: string }) {
  const [result, setResult] = useState<{ review?: SavedReview; error?: string }>({});
  useEffect(() => {
    let active = true;
    void fetchSavedReview(id)
      .then((review) => { if (active) setResult({ review }); })
      .catch((error: Error) => { if (active) setResult({ error: error.message }); });
    return () => { active = false; };
  }, [id]);
  if (result.error) return <p role="alert">{result.error}</p>;
  if (!result.review) return <p role="status">Loading review…</p>;
  return <p>{result.review.filters.length} filters · Revision {result.review.revision}</p>;
}

function HistoricalMedia({ title, version }: { title: string; version: HistoricalVersion }) {
  const [reviewId, setReviewId] = useState<string | null>(null);
  return <>
    <header className="page-header"><div><h1>{title}</h1><p>{version.label} · Previous version</p></div></header>
    <p>Original scan and review history.</p>
    <h2>Scans</h2>
    {version.scans.length
      ? <ul>{version.scans.map((scan) => <li key={scan.id}>{scanLabel(scan)} · {scan.status}</li>)}</ul>
      : <p>No scans available.</p>}
    <h2>Saved reviews</h2>
    {version.reviews.length
      ? <ul>{version.reviews.map((review) => <li key={review.id}>
        <button className="item-link" type="button" aria-expanded={review.id === reviewId}
          onClick={() => setReviewId(review.id === reviewId ? null : review.id)}>{review.name}</button>
        {review.id === reviewId && <HistoricalReview key={review.id} id={review.id} />}
      </li>)}</ul>
      : <p>No saved reviews available.</p>}
  </>;
}

function explicitVersion(identity: EditarrItem, versionId: string): ExplicitVersion | null {
  const recorded = identity.versions.find((version) => version.id === versionId);
  if (recorded && !recorded.available) return { historical: recorded };

  const matches = identity.current_versions?.filter((version) => version.version_id === versionId) ?? [];
  const recordedTarget = recorded?.active_item_id
    ? { item_id: recorded.active_item_id, media_source_id: recorded.active_media_source_id ?? null }
    : null;
  const target = matches.length === 1 ? matches[0] : recordedTarget;
  if (!target) return null;
  const item = identity.active_items.find((candidate) => candidate.id === target.item_id);
  if (!item) return null;
  const sourceId = target.media_source_id;
  const sourceAvailable = sourceId === null
    ? !item.versions.some((source) => source.id)
    : item.versions.some((source) => source.id === sourceId);
  return sourceAvailable ? { item, sourceId } : null;
}

export function StableItemPage({ route, libraries, onNavigate, onReplace, onDirtyChange, onUnavailable }: PageProps) {
  const legacyLibrary = route.kind === "library" ? route.libraryId : null;
  const requestedVersion = route.kind === "title" ? route.versionId : undefined;
  const titleId = route.kind === "title" ? route.titleId : null;
  const itemId = route.itemId || (route.kind === "library" ? route.seriesId : undefined);
  const sourceId = route.kind === "library" ? route.sourceId : undefined;
  const routeKey = JSON.stringify([titleId, itemId, legacyLibrary, requestedVersion, sourceId]);
  const [state, setState] = useState<{
    routeKey: string; identity?: EditarrItem; exactVersion?: ExplicitVersion | null; failed?: boolean;
  }>();
  const { identity, exactVersion, failed } = state?.routeKey === routeKey ? state : {};
  usePageTitle(identity?.name || "Library");
  useEffect(() => {
    let active = true;
    const request = titleId ? fetchEditarrItem(titleId, requestedVersion)
      : itemId && legacyLibrary ? resolveLegacyItem(itemId, legacyLibrary) : null;
    void request?.then((result) => {
      if (!active) return;
      const exactVersion = requestedVersion ? explicitVersion(result, requestedVersion) : null;
      if (requestedVersion && !exactVersion) {
        setState({ routeKey, failed: true });
        return;
      }
      setState({ routeKey, identity: result, exactVersion });
      const library = result.active_item?.library_id || legacyLibrary;
      if (library) writeUserValue(`library-last-route.${library}`, editarrItemRoute(
        result.title_id, requestedVersion || result.selected_version_id || undefined,
      ));
      // A library route naming a media source (from a notification) opens that exact version.
      const sourceVersion = sourceId ? result.current_versions?.find((version) =>
        version.item_id === itemId && version.media_source_id === sourceId)?.version_id : undefined;
      if (!titleId && (sourceVersion || !result.choose_version)) {
        const liveItem = result.active_items.length > 1 ? result.active_item?.id : undefined;
        onReplace(editarrItemRoute(result.title_id, sourceVersion || result.selected_version_id || undefined, liveItem));
      }
    }).catch(() => { if (active) setState({ routeKey, failed: true }); });
    return () => { active = false; };
  }, [titleId, itemId, legacyLibrary, requestedVersion, sourceId, routeKey, onReplace]);

  if (failed && route.kind === "library" && itemId) {
    const library = libraries.find((entry) => entry.id === legacyLibrary);
    if (library && !route.itemId && route.seriesId) return <SeriesSeasonsPage library={library} seriesId={route.seriesId} />;
    if (library) return <CatalogItemBoundary source="jellyfin" itemId={itemId} onUnavailable={onUnavailable}>
      <MediaDetailPage library={library} itemId={itemId} seasonId={route.seasonId}
        seriesId={route.seriesId} onNavigate={onNavigate} onDirtyChange={onDirtyChange} />
    </CatalogItemBoundary>;
  }
  if (failed) return <div className="content-page"><p role="alert">This library item is unavailable.</p></div>;
  if (!identity) return <div className="content-page"><p role="status">Loading item…</p></div>;

  const recorded = route.kind === "library"
    ? identity.versions.find((version) => version.id === identity.selected_version_id) : undefined;
  const historical = exactVersion && "historical" in exactVersion ? exactVersion.historical
    : recorded && !recorded.available ? recorded : null;
  const exactLive = exactVersion && "item" in exactVersion ? exactVersion : null;
  const activeId = recorded?.active_item_id || (route.kind === "title" ? route.itemId : undefined);
  const activeItem = exactLive?.item
    ?? (identity.active_items.find((item) => item.id === activeId) || identity.active_item);
  const initialSourceId = exactLive ? exactLive.sourceId : recorded?.active_media_source_id;
  const library = libraries.find((entry) => entry.id === activeItem?.library_id);
  const libraryCrumb = library ? { label: library.name, route: `library/${encodeURIComponent(library.id)}` } : null;
  const crumbs = [...(libraryCrumb ? [libraryCrumb] : []), { label: identity.name }];
  if (activeItem?.item_type === "Series" && library) {
    return <SeriesSeasonsPage library={library} seriesId={activeItem.id} />;
  }
  if (activeItem && !historical && !(route.kind === "library" && identity.choose_version)) {
    return <CatalogItemBoundary source="jellyfin" itemId={activeItem.id} onUnavailable={onUnavailable}>
      <MediaDetail key={`${activeItem.id}:${initialSourceId || ""}`} item={activeItem} breadcrumbs={crumbs}
        seasonNumber={activeItem.season_number} onNavigate={onNavigate} onDirtyChange={onDirtyChange}
        stableIdentity={identity} initialSourceId={initialSourceId} />
    </CatalogItemBoundary>;
  }
  return <div className="content-page media-detail">
    <LibraryBreadcrumbs crumbs={crumbs} onNavigate={onNavigate} />
    {historical ? <HistoricalMedia key={historical.id} title={identity.name} version={historical} />
      : <><h1>{identity.name}</h1><p>Select a version to open its scans and reviews.</p></>}
    {identity.active_items.map((item) => {
      const exact = identity.current_versions?.filter((version) => version.item_id === item.id);
      const path = editarrItemRoute(identity.title_id, exact?.length === 1 ? exact[0].version_id : undefined,
        identity.active_items.length > 1 ? item.id : undefined);
      return <p key={item.id}><button type="button" className="item-link" onClick={() => onNavigate(path)}>
        {identity.active_items.length === 1 ? "Open current version" : `${item.versions[0]?.name || item.name} · Current`}
      </button></p>;
    })}
    {!historical && identity.versions.filter((version) => !version.available && (version.scans.length || version.reviews.length)).map((version) =>
      <p key={version.id}><button type="button" className="item-link" onClick={() => onNavigate(editarrItemRoute(identity.title_id, version.id))}>
        {version.label} · Previous version
      </button></p>)}
  </div>;
}
