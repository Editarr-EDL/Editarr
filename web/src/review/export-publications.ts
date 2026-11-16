import { useCallback, useEffect, useRef, useState } from "react";
import { fetchVersionPublications } from "../api/api-exports";
import { fetchExportStatus } from "../api/api-reviews";
import type { ExportPublication, ExportStatus } from "../api/api-mcf-types";
import { useReviewLifetime } from "./review-access";

/**
 * Read one media version's canonical publications through discovery once a
 * persisted media item ID is known, falling back to the parent's decorated
 * export status before that. `refresh` rereads discovery and the parent's
 * status after a change, so badges and this workspace agree. A parent status
 * the caller may not read is left as is.
 */
export function useVersionPublications({ itemId, mediaItemId, mediaSourceId, exportStatus, onExportStatusChange }: {
  itemId: string;
  mediaItemId: string | null;
  mediaSourceId: string | null;
  exportStatus?: ExportStatus;
  onExportStatusChange?: (status: ExportStatus) => void;
}) {
  const [discovered, setDiscovered] = useState<{ mediaItemId: string; publications: ExportPublication[] } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const lifetime = useReviewLifetime();
  const isCurrent = useRef(lifetime);
  isCurrent.current = lifetime;
  const onChange = useRef(onExportStatusChange);
  onChange.current = onExportStatusChange;
  const known = useRef<string | null>(null);
  if (mediaItemId) known.current = mediaItemId;
  const request = useRef(0);
  const publications = discovered?.publications
    ?? exportStatus?.publications?.filter((row) => !mediaItemId || row.media_item_id === mediaItemId)
    ?? [];

  const refresh = useCallback(async (): Promise<ExportPublication[]> => {
    const ticket = ++request.current;
    void fetchExportStatus(itemId, mediaSourceId)
      .then((status) => { if (ticket === request.current && isCurrent.current()) onChange.current?.(status); })
      .catch(() => {});
    const id = known.current;
    if (!id) return [];
    const rows = await fetchVersionPublications(id, mediaSourceId);
    if (ticket === request.current && isCurrent.current()) setDiscovered({ mediaItemId: id, publications: rows });
    return rows;
  }, [itemId, mediaSourceId]);

  const loadedFor = discovered?.mediaItemId ?? null;
  useEffect(() => {
    if (!mediaItemId || loadedFor === mediaItemId) return;
    const ticket = ++request.current;
    void fetchVersionPublications(mediaItemId, mediaSourceId).then((rows) => {
      if (ticket === request.current && isCurrent.current()) setDiscovered({ mediaItemId, publications: rows });
    }).catch(() => {});
  }, [mediaItemId, mediaSourceId, loadedFor]);

  return { publications, refresh, notice, setNotice };
}

export type VersionPublications = ReturnType<typeof useVersionPublications>;
