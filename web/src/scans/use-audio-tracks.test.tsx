import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../api/api-audio-tracks";
import { useAudioTracks } from "./use-audio-tracks";

vi.mock("../api/api-audio-tracks", () => ({ fetchAudioTracks: vi.fn(), confirmAudioTrack: vi.fn() }));
const main: api.AudioTrack = { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2,
  disposition: 1, start_seconds: 0, duration_seconds: 5, time_base: "1/48000" };
const inventory = (version: string): api.AudioTrackInventory => ({ file_version: version, tracks: [main],
  selection: { file_version: version, track: main }, selection_stale: false });
const first: api.AudioTarget = { item_id: "first" };
const second: api.AudioTarget = { item_id: "second", media_source_id: "version-2" };
beforeEach(() => {
  vi.mocked(api.fetchAudioTracks).mockImplementation(async (target) => inventory(target.item_id));
  vi.mocked(api.confirmAudioTrack).mockImplementation(async (_target, selection) => selection);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("uses only server preselection and confirms exact file-bound input before submission", async () => {
  const { result } = renderHook(() => useAudioTracks(first, true));
  await waitFor(() => expect(result.current.ready).toBe(true));
  await act(async () => { await result.current.prepare(); });
  expect(api.confirmAudioTrack).toHaveBeenCalledWith(first, { file_version: "first", track: main });
});

it("discards late inventory on a file change and cancellation", async () => {
  let resolveFirst!: (value: api.AudioTrackInventory) => void;
  const pending = new Promise<api.AudioTrackInventory>((resolve) => { resolveFirst = resolve; });
  vi.mocked(api.fetchAudioTracks).mockImplementation(async (target) => target.item_id === "first" ? pending : inventory(target.item_id));
  const { result, rerender } = renderHook(({ target, enabled }) => useAudioTracks(target, enabled), { initialProps: { target: first, enabled: true } });
  await waitFor(() => expect(api.fetchAudioTracks).toHaveBeenCalledOnce());
  const signal = vi.mocked(api.fetchAudioTracks).mock.calls[0][1];
  rerender({ target: second, enabled: true });
  expect(result.current.selection).toBeNull();
  expect(signal.aborted).toBe(true);
  await waitFor(() => expect(result.current.selection?.file_version).toBe("second"));
  await act(async () => { resolveFirst(inventory("first")); });
  expect(result.current.selection?.file_version).toBe("second");
  rerender({ target: second, enabled: false });
  expect(result.current.ready).toBe(false);
  expect(result.current.inventory).toBeNull();
});

it("clears rejected selectors without falling back, and retries inventory", async () => {
  const { result } = renderHook(() => useAudioTracks(first, true));
  await waitFor(() => expect(result.current.ready).toBe(true));
  vi.mocked(api.confirmAudioTrack).mockRejectedValueOnce(new Error("Track disappeared"));
  await act(async () => { await expect(result.current.prepare()).rejects.toThrow("disappeared"); });
  expect(result.current.selection).toBeNull();
  expect(result.current.ready).toBe(false);
  act(() => result.current.onRetry());
  await waitFor(() => expect(result.current.ready).toBe(true));
});

it("never promotes an ambiguous or stale remembered inventory to a default", async () => {
  vi.mocked(api.fetchAudioTracks).mockResolvedValue({ ...inventory("v1"), selection: null, selection_stale: true });
  const { result } = renderHook(() => useAudioTracks(first, true));
  await waitFor(() => expect(result.current.error).toContain("Choose a track"));
  expect(result.current.ready).toBe(false);
  act(() => result.current.onChange({ file_version: "v1", track: main }));
  await waitFor(() => expect(result.current.ready).toBe(true));
});