import { apiRequest, responseError } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export type AudioTrack = {
  stream_index: number; language: string | null; title: string | null;
  codec: string | null; channels: number | null; disposition: number;
  start_seconds: number | null; duration_seconds: number | null; time_base: string | null;
};
export type AudioTrackSelection = { file_version: string; track: AudioTrack };
export type AudioTrackInventory = {
  file_version: string; tracks: AudioTrack[];
  selection: AudioTrackSelection | null; selection_stale: boolean;
};

export type AudioTarget = { item_id: string; media_source_id?: string | null };

function targetFields(target: AudioTarget): Record<string, string> {
  return { item_id: target.item_id, ...(target.media_source_id ? { media_source_id: target.media_source_id } : {}) };
}

export async function fetchAudioTracks(target: AudioTarget, signal: AbortSignal): Promise<AudioTrackInventory> {
  const response = await apiRequest(`${runtimeApiBasePath()}/media/audio-tracks?${new URLSearchParams(targetFields(target))}`, { signal });
  if (!response.ok) throw await responseError(response, "Unable to load audio tracks");
  const inventory = await response.json() as AudioTrackInventory;
  if (!inventory || typeof inventory.file_version !== "string" || !Array.isArray(inventory.tracks)) {
    throw new Error("Invalid audio inventory response");
  }
  return inventory;
}

export async function confirmAudioTrack(target: AudioTarget, selection: AudioTrackSelection) {
  const response = await apiRequest(`${runtimeApiBasePath()}/media/audio-tracks/selection`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...targetFields(target), selection }),
  });
  if (!response.ok) throw await responseError(response, "Audio selection changed; refresh and choose a track again");
  return response.json() as Promise<AudioTrackSelection>;
}
