import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCommunityMaster, saveMasterPreferences } from "../api/api-community-masters";
import type { MasterFilter, MasterPreferences, MasterView } from "../api/api-community-master-types";
import { masterErrorText } from "./community-master-format";

const SAVE_DELAY_MS = 400;

/**
 * Load one master set view and keep the account's display preferences. Preference changes apply
 * locally at once and are saved after a short pause so dragging the slider sends one request.
 */
export function useMasterView(linkId: string) {
  const [view, setView] = useState<MasterView | null>(null);
  const [preferences, setPreferences] = useState<MasterPreferences | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const controller = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setError(null);
    try {
      const next = await fetchCommunityMaster(linkId, request.signal);
      if (request.signal.aborted) return;
      setView(next);
      setPreferences((current) => current ?? next.preferences);
    } catch (reason) { if (!request.signal.aborted) setError(masterErrorText(reason)); }
  }, [linkId]);
  useEffect(() => {
    void reload();
    return () => { controller.current?.abort(); window.clearTimeout(timer.current); };
  }, [reload]);

  const changePreferences = (next: MasterPreferences) => {
    setPreferences(next);
    setSaveError(null);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      saveMasterPreferences(next).catch((reason) => setSaveError(masterErrorText(reason)));
    }, SAVE_DELAY_MS);
  };
  const replaceFilter = (filter: MasterFilter) => setView((current) => current && {
    ...current, filters: current.filters.map((item) => item.filterId === filter.filterId ? filter : item),
  });

  return { view, preferences, error, saveError, reload, changePreferences, replaceFilter };
}
