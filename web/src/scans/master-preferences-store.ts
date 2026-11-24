import { useEffect, useSyncExternalStore } from "react";
import { saveMasterPreferences } from "../api/api-community-masters";
import type { MasterPreferences } from "../api/api-community-master-types";
import { usePrincipal } from "../auth/auth-gate";
import { masterErrorText } from "./community-master-format";

export const PREFERENCE_SAVE_DELAY_MS = 400;

export type PreferenceSnapshot = {
  /** Last value the server confirmed (or reported in a master view). */
  saved: MasterPreferences | null;
  /** Value shown in the UI, including unsaved changes. */
  displayed: MasterPreferences | null;
  /** True while a change waits for its debounced save or a save is in flight. */
  pending: boolean;
  error: string | null;
};
const empty: PreferenceSnapshot = { saved: null, displayed: null, pending: false, error: null };
const same = (a: MasterPreferences | null, b: MasterPreferences | null) =>
  !!a && !!b && a.confidenceThreshold === b.confidenceThreshold && a.showLowConfidence === b.showLowConfidence;

/**
 * Account preference store shared by every master link card. Changes show at once, save after a
 * short pause, flush immediately on `flush()`, and only the latest save's response is applied.
 */
export function createPreferenceStore(save: typeof saveMasterPreferences = saveMasterPreferences) {
  let snapshot = empty;
  let owner: number | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let queued: MasterPreferences | null = null;
  let sequence = 0;
  const listeners = new Set<() => void>();
  const set = (next: Partial<PreferenceSnapshot>) => {
    snapshot = { ...snapshot, ...next };
    listeners.forEach((listener) => listener());
  };
  const send = (value: MasterPreferences) => {
    const request = ++sequence;
    void save(value).then((response) => {
      if (request !== sequence) return;
      const saved = response && typeof response.confidenceThreshold === "number"
        ? { confidenceThreshold: response.confidenceThreshold, showLowConfidence: response.showLowConfidence } : value;
      set({ saved, pending: queued !== null, error: null });
    }).catch((reason) => {
      if (request === sequence) set({ pending: queued !== null, error: masterErrorText(reason) });
    });
  };
  const flush = () => {
    clearTimeout(timer);
    timer = undefined;
    if (!queued) return;
    const value = queued;
    queued = null;
    send(value);
  };
  return {
    subscribe(listener: () => void) { listeners.add(listener); return () => listeners.delete(listener); },
    get: () => snapshot,
    /** Switch accounts: save the previous owner's pending change, then start empty. */
    setOwner(next: number | null) {
      if (next === owner) return;
      flush();
      owner = next;
      sequence++;
      snapshot = empty;
    },
    /** Adopt server preferences reported by a master view when nothing is known or pending yet. */
    seed(value: MasterPreferences) {
      if (!snapshot.saved && !snapshot.pending) set({ saved: value, displayed: value });
    },
    change(value: MasterPreferences) {
      queued = value;
      set({ displayed: value, pending: true, error: null });
      clearTimeout(timer);
      timer = setTimeout(flush, PREFERENCE_SAVE_DELAY_MS);
    },
    flush,
    /** Whether the shown preferences are the saved ones, so server-computed `active` flags apply. */
    synced: () => !snapshot.pending && same(snapshot.displayed, snapshot.saved),
  };
}

export const masterPreferences = createPreferenceStore();

/** Read and change the shared preferences; pending saves flush on unmount and on page hide. */
export function useMasterPreferences(store = masterPreferences) {
  const owner = usePrincipal()?.userId ?? null;
  store.setOwner(owner);
  const snapshot = useSyncExternalStore(store.subscribe, store.get);
  useEffect(() => {
    window.addEventListener("pagehide", store.flush);
    return () => { window.removeEventListener("pagehide", store.flush); store.flush(); };
  }, [store]);
  return { ...snapshot, synced: store.synced(), change: store.change, seed: store.seed };
}
