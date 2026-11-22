import type { AudioTrack, AudioTrackInventory, AudioTrackSelection } from "../api/api-audio-tracks";
export type { AudioTrack, AudioTrackInventory, AudioTrackSelection } from "../api/api-audio-tracks";

export function audioTrackLabel(track: AudioTrack): string {
  return [
    `Stream ${track.stream_index}`,
    track.language || "Unknown language",
    track.title || "Untitled",
    track.codec,
    track.channels === null ? null : `${track.channels} channels`,
    track.disposition & 1 ? "Default" : null,
  ].filter(Boolean).join(" / ");
}

/** Controlled field: callers own file-bound loading, preselection, and memory. */
export function AudioTrackField({
  inventory, selection, loading = false, error = null, disabled = false,
  label = "Audio track", showSelectedLabel = true,
  onChange, onRetry,
}: {
  inventory: AudioTrackInventory | null;
  selection: AudioTrackSelection | null;
  loading?: boolean;
  error?: string | null;
  disabled?: boolean;
  label?: string;
  showSelectedLabel?: boolean;
  onChange: (selection: AudioTrackSelection) => void;
  onRetry: () => void;
}) {
  const selected = selection && inventory?.file_version === selection.file_version
    ? inventory.tracks.find((track) => Object.keys(track).every(
      (key) => track[key as keyof AudioTrack] === selection.track[key as keyof AudioTrack],
    )) : null;
  return (
    <div>
      <label className="field">
        <span>{label}</span>
        <select
          aria-label={label}
          value={selected?.stream_index ?? ""}
          disabled={disabled || loading || !inventory?.tracks.length}
          onChange={(event) => {
            const track = inventory?.tracks.find(
              (candidate) => String(candidate.stream_index) === event.target.value,
            );
            if (inventory && track) onChange({ file_version: inventory.file_version, track });
          }}
        >
          <option value="">Choose an audio track</option>
          {inventory?.tracks.map((track) => (
            <option key={track.stream_index} value={track.stream_index}>
              {audioTrackLabel(track)}
            </option>
          ))}
        </select>
      </label>
      {selected && showSelectedLabel && <p style={{ overflowWrap: "anywhere" }}>{audioTrackLabel(selected)}</p>}
      {loading && <p role="status">Loading audio tracks...</p>}
      {error && <><p role="alert">{error}</p><button type="button" disabled={disabled || loading} onClick={onRetry}>Retry</button></>}
      {!loading && !error && inventory?.tracks.length === 0 && <p role="status">No audio tracks available.</p>}
      {!loading && !error && selection && !selected && <p role="alert">Audio selection changed. Choose a track again.</p>}
    </div>
  );
}
