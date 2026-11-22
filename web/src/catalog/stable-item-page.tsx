import { useEffect, useState } from "react";

import type { JellyfinLibrary } from "../api/api-media-types";
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
    <p>Original scan and review history. Select a current version to verify these scans for reuse.</p>
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

export function StableItemPage({ route, libraries, onNavigate, onReplace, onDirtyChange, onUnavailable }: PageProps) {
  const [state, setState] = useState<{ identity?: EditarrItem; failed?: boolean }>({});
  const legacyLibrary = route.kind === "library" ? route.libraryId : null;
  const requestedVersion = route.kind === "title" ? route.versionId : undefined;
  const titleId = route.kind === "title" ? route.titleId : null;
  const itemId = route.itemId || (route.kind === "library" ? route.seriesId : undefined);
  const { identity, failed } = state;
  usePageTitle(identity?.name || "Library");
  useEffect(() => {
    let active = true;
    const request = titleId ? fetchEditarrItem(titleId, requestedVersion)
      : itemId && legacyLibrary ? resolveLegacyItem(itemId, legacyLibrary) : null;
    void request?.then((result) => {
      if (!active) return;
      setState({ identity: result });
      const library = result.active_item?.library_id || legacyLibrary;
      if (library) writeUserValue(`library-last-route.${library}`, editarrItemRoute(
        result.title_id, requestedVersion || result.selected_version_id || undefined,
      ));
      if (!titleId && !result.choose_version) {
        const liveItem = result.active_items.length > 1 ? result.active_item?.id : undefined;
        onReplace(editarrItemRoute(result.title_id, result.selected_version_id || undefined, liveItem));
      }
    }).catch(() => { if (active) setState({ failed: true }); });
    return () => { active = false; };
  }, [titleId, itemId, legacyLibrary, requestedVersion, onReplace]);

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

  const historicalId = route.kind === "title" ? route.versionId : identity.selected_version_id;
  const recorded = identity.versions.find((version) => version.id === historicalId);
  const historical = recorded && !recorded.available ? recorded : null;
  const activeId = recorded?.active_item_id || (route.kind === "title" ? route.itemId : undefined);
  const activeItem = identity.active_items.find((item) => item.id === activeId) || identity.active_item;
  const library = libraries.find((entry) => entry.id === activeItem?.library_id);
  const libraryCrumb = library ? { label: library.name, route: `library/${encodeURIComponent(library.id)}` } : null;
  const crumbs = [...(libraryCrumb ? [libraryCrumb] : []), { label: identity.name }];
  const choose = (version?: string, live?: string) => onNavigate(editarrItemRoute(identity.title_id, version, live));
  const versionLinks = (historical || !activeItem || identity.versions.length > 0) && <section className="stable-version-picker" aria-label="Item versions">
    <label className="field"><span>Item history</span><select aria-label="Item history"
      value={historical ? `old:${historical.id}` : activeItem ? `live:${activeItem.id}` : ""}
      onChange={(event) => {
        const selection = event.target.value;
        if (selection.startsWith("old:")) choose(selection.slice(4));
        if (selection.startsWith("live:")) {
          const item = selection.slice(5);
          const exact = identity.current_versions?.filter((version) => version.item_id === item);
          choose(exact?.length === 1 ? exact[0].version_id : undefined, identity.active_items.length > 1 ? item : undefined);
        }
      }}>
      <option value="" disabled>Choose a version</option>
      {identity.active_items.map((item) => <option key={item.id} value={`live:${item.id}`}>
        {item.versions[0]?.name || item.name} · Current
      </option>)}
      {identity.versions.filter((version) => !version.available && (version.scans.length > 0 || version.reviews.length > 0)).map((version) => (
        <option key={version.id} value={`old:${version.id}`}>{version.label} · Previous version</option>
      ))}
    </select></label>
  </section>;

  if (activeItem?.item_type === "Series" && library) {
    return <SeriesSeasonsPage library={library} seriesId={activeItem.id} />;
  }
  if (activeItem && !historical && !(route.kind === "library" && identity.choose_version)) {
    return <CatalogItemBoundary source="jellyfin" itemId={activeItem.id} onUnavailable={onUnavailable}>
      <MediaDetail key={`${activeItem.id}:${recorded?.active_media_source_id || ""}`} item={activeItem} breadcrumbs={crumbs}
        seasonNumber={activeItem.season_number} onNavigate={onNavigate} onDirtyChange={onDirtyChange}
        stableIdentity={identity} versionPicker={versionLinks} initialSourceId={recorded?.active_media_source_id} />
    </CatalogItemBoundary>;
  }
  return <div className="content-page media-detail">
    <LibraryBreadcrumbs crumbs={crumbs} onNavigate={onNavigate} />
    {versionLinks}
    {historical ? <HistoricalMedia key={historical.id} title={identity.name} version={historical} />
      : <><h1>{identity.name}</h1><p>Select a version to open its scans and reviews.</p></>}
  </div>;
}
