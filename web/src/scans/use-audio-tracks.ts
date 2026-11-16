import { useEffect, useRef, useState } from "react";
import { confirmAudioTrack, fetchAudioTracks, type AudioTarget, type AudioTrackInventory, type AudioTrackSelection } from "../api/api-audio-tracks";

type State = {
  source: string; inventory: AudioTrackInventory | null;
  selection: AudioTrackSelection | null; loading: boolean; error: string | null;
};
const empty: State = { source: "", inventory: null, selection: null, loading: false, error: null };

/** Load one file's inventory; discard late responses and never substitute a stale choice. */
export function useAudioTracks(target: AudioTarget, enabled: boolean) {
  const source = JSON.stringify(target);
  const [state, setState] = useState<State>(empty);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const currentSource = useRef(source);
  currentSource.current = source;
  useEffect(() => {
    const token = ++generation.current;
    const controller = new AbortController();
    setState({ ...empty, source, loading: enabled });
    if (!enabled) return () => { controller.abort(); generation.current++; };
    const timer = window.setTimeout(() => {
      void fetchAudioTracks(target, controller.signal).then((inventory) => {
        if (token !== generation.current || currentSource.current !== source) return;
        setState({ source, inventory, selection: inventory.selection, loading: false,
          error: inventory.selection_stale ? "Audio selection changed. Choose a track again." : null });
      }).catch((reason) => {
        if (controller.signal.aborted || token !== generation.current) return;
        setState({ ...empty, source, error: reason instanceof Error ? reason.message : "Unable to load audio tracks" });
      });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); generation.current++; };
  }, [source, enabled, revision]);

  const visible = enabled && state.source === source ? state : { ...empty, loading: enabled };
  const confirm = async (selection: AudioTrackSelection) => {
    const token = generation.current;
    const snapshot = visible;
    if (!snapshot.inventory || selection.file_version !== snapshot.inventory.file_version) throw new Error("Choose an audio track");
    setState({ ...snapshot, loading: true, error: null });
    try {
      const confirmed = await confirmAudioTrack(target, selection);
      if (token !== generation.current || currentSource.current !== snapshot.source) throw new Error("Selected file changed");
      setState({ ...snapshot, selection: confirmed, loading: false, error: null });
      return confirmed;
    } catch (reason) {
      if (token === generation.current && currentSource.current === snapshot.source) {
        setState({ ...snapshot, selection: null, loading: false,
          error: reason instanceof Error ? reason.message : "Audio selection changed" });
      }
      throw reason;
    }
  };
  return {
    ...visible,
    ready: !!visible.selection && !visible.loading && !visible.error,
    onRetry: () => setRevision((value) => value + 1),
    onChange: (selection: AudioTrackSelection) => { void confirm(selection).catch(() => {}); },
    prepare: () => visible.selection ? confirm(visible.selection) : Promise.reject(new Error("Choose an audio track")),
  };
}