import { afterEach, expect, it, vi } from "vitest";
import { confirmAudioTrack, fetchAudioTracks } from "../api/api-audio-tracks";

vi.mock("../api/api-transport", () => ({
  apiRequest: (...args: Parameters<typeof fetch>) => fetch(...args),
  responseError: async () => new Error("Request failed"),
}));

afterEach(() => vi.unstubAllGlobals());

it("reads and confirms a Jellyfin item track by item id without a source or server path", async () => {
  const track = { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2,
    disposition: 1, start_seconds: 0, duration_seconds: 4, time_base: "1/48000" };
  const selection = { file_version: "synthetic-v1", track };
  const requests = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => new Response(JSON.stringify(
    init?.method === "POST" ? selection : { file_version: "synthetic-v1", tracks: [track], selection, selection_stale: false },
  ), { status: 200 }));
  vi.stubGlobal("fetch", requests);
  const target = { item_id: "visible", media_source_id: "version-2" };
  const inventory = await fetchAudioTracks(target, new AbortController().signal);
  expect(inventory.selection).toEqual(selection);
  await confirmAudioTrack(target, selection);
  expect(requests).toHaveBeenCalledTimes(2);
  const query = new URL(String(requests.mock.calls[0][0]), "http://localhost").searchParams;
  expect(Object.fromEntries(query)).toEqual(target);
  expect(JSON.parse(String(requests.mock.calls[1][1]?.body))).toEqual({ ...target, selection });
  expect(JSON.stringify(requests.mock.calls)).not.toMatch(/video_path|path-mappings|local_path|"source"|source=/);
});