import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { MasterPreferences } from "../api/api-community-master-types";
import { createPreferenceStore, PREFERENCE_SAVE_DELAY_MS, useMasterPreferences } from "./master-preferences-store";

const prefs = (confidenceThreshold: number, showLowConfidence = false): MasterPreferences => ({ confidenceThreshold, showLowConfidence });
beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("shows changes at once and saves only the last one after the pause", async () => {
  const save = vi.fn(async (value: MasterPreferences) => value);
  const store = createPreferenceStore(save);
  store.seed(prefs(0));
  store.change(prefs(0.1));
  store.change(prefs(0.2));
  expect(store.get()).toMatchObject({ displayed: prefs(0.2), saved: prefs(0), pending: true });
  expect(store.synced()).toBe(false);
  await act(async () => { vi.advanceTimersByTime(PREFERENCE_SAVE_DELAY_MS); });
  expect(save).toHaveBeenCalledExactlyOnceWith(prefs(0.2));
  expect(store.get()).toMatchObject({ saved: prefs(0.2), pending: false, error: null });
  expect(store.synced()).toBe(true);
  store.seed(prefs(0.9));
  expect(store.get().displayed).toEqual(prefs(0.2));
});

it("flushes a pending save when the last card unmounts", () => {
  const save = vi.fn(async (value: MasterPreferences) => value);
  const store = createPreferenceStore(save);
  const { result, unmount } = renderHook(() => useMasterPreferences(store));
  act(() => result.current.change(prefs(0.3, true)));
  expect(save).not.toHaveBeenCalled();
  unmount();
  expect(save).toHaveBeenCalledExactlyOnceWith(prefs(0.3, true));
  vi.advanceTimersByTime(PREFERENCE_SAVE_DELAY_MS);
  expect(save).toHaveBeenCalledOnce();
});

it("ignores a response that arrives after a newer save", async () => {
  const replies: ((value: MasterPreferences) => void)[] = [];
  const store = createPreferenceStore(() => new Promise((resolve) => { replies.push(resolve); }));
  store.change(prefs(0.1));
  store.flush();
  store.change(prefs(0.5));
  store.flush();
  await act(async () => replies[1](prefs(0.5)));
  await act(async () => replies[0](prefs(0.1)));
  expect(store.get()).toMatchObject({ saved: prefs(0.5), displayed: prefs(0.5), pending: false });
});

it("keeps the shown value and reports a failed save", async () => {
  const store = createPreferenceStore(() => Promise.reject(Object.assign(new Error("x"), { code: "service_unavailable" })));
  store.seed(prefs(0));
  store.change(prefs(0.4));
  await act(async () => { vi.advanceTimersByTime(PREFERENCE_SAVE_DELAY_MS); });
  expect(store.get()).toMatchObject({ displayed: prefs(0.4), saved: prefs(0), pending: false,
    error: "Community service is unavailable. Try again later." });
  expect(store.synced()).toBe(false);
});
