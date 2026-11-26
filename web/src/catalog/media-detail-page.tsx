import type { ItemRouteStep } from "../routes";
import { useEffect, useState } from "react";
import { fetchLibraryItem } from "../api/api-service";
import type { JellyfinLibrary, JellyfinMediaItem } from "../api/api-media-types";
import { usePageTitle } from "../shared/display";
import { MediaDetail, type Breadcrumb } from "./media-detail";

/** Load a Jellyfin item and render its detail workspace. */
export function MediaDetailPage({
  library,
  itemId,
  seriesId,
  seasonId,
  onNavigate,
  onDirtyChange,
  initialStep,
}: {
  library: JellyfinLibrary;
  itemId: string;
  seriesId?: string;
  seasonId?: string;
  onNavigate: (route: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  initialStep?: ItemRouteStep;
}) {
  const [item, setItem] = useState<JellyfinMediaItem | null>(null);
  const [series, setSeries] = useState<JellyfinMediaItem | null>(null);
  const [season, setSeason] = useState<JellyfinMediaItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(item?.name || library.name);
  useEffect(() => {
    let active = true;
    const requests = [fetchLibraryItem(itemId)];
    if (seriesId) requests.push(fetchLibraryItem(seriesId));
    if (seasonId) requests.push(fetchLibraryItem(seasonId));
    void Promise.all(requests).then((loaded) => {
      if (!active) return;
      let parentIndex = 1;
      setItem(loaded[0]);
      setSeries(seriesId ? loaded[parentIndex++] : null);
      setSeason(seasonId ? loaded[parentIndex] : null);
      setError(null);
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "Unable to load library item.");
    });
    return () => { active = false; };
  }, [itemId, seasonId, seriesId]);
  const libraryRoute = `library/${encodeURIComponent(library.id)}`;
  if (error) return <div className="content-page"><div className="load-error" role="alert"><span>{error}</span><button className="btn-secondary" onClick={() => onNavigate(libraryRoute)}>Back to library</button></div></div>;
  if (!item || item.id !== itemId) return <div className="content-page"><p className="loading-state">Loading item…</p></div>;
  const breadcrumbs: Breadcrumb[] = [
    { label: library.name, route: libraryRoute },
    ...(series && seriesId ? [{ label: series.name, route: `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}` }] : []),
    ...(season && seriesId && seasonId ? [{ label: season.name, route: `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}/season/${encodeURIComponent(seasonId)}` }] : []),
    { label: item.name },
  ];
  return <MediaDetail key={item.id} item={item} breadcrumbs={breadcrumbs} onNavigate={onNavigate} seasonNumber={season?.index_number ?? item.season_number} onDirtyChange={onDirtyChange} initialStep={initialStep} />;
}
