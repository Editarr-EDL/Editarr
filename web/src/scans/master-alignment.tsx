import { useId, useState } from "react";
import { approveMasterAlignment, previewMasterAlignment } from "../api/api-community-masters";
import type { Alignment, AlignmentPreview, AlignmentPreviewRequest, MasterTarget } from "../api/api-community-master-types";
import { AudioTrackField } from "./audio-track-field";
import { filterName, formatMs, formatRange, masterErrorText } from "./community-master-format";
import { useAudioTracks } from "./use-audio-tracks";
import { FilterClipPlayer, rangePreview, useFilterClip } from "./use-filter-clip";

export type AlignableFilter = { filterId: string; category: string; subcategory: string | null; startMs: number; endMs: number; action: string };
type Anchor = { filterId: string; seconds: string };
const MAX_ANCHORS = 50;

/**
 * Fuzzy-tier alignment gate (tier 4 and below). The user shifts every filter by an offset or places
 * anchors, previews the aligned times with clips, then approves the transform for `forRevision`.
 * `prefill` seeds the offset from the link's stored transform.
 */
export function MasterAlignment({ linkId, target, filters, forRevision, prefill, onApproved }: {
  linkId: string; target: MasterTarget; filters: AlignableFilter[]; forRevision: number;
  prefill: Alignment | null; onApproved: () => void;
}) {
  const heading = useId();
  const [mode, setMode] = useState<"offset" | "anchors">("offset");
  const [offset, setOffset] = useState(prefill ? String(prefill.offsetMs / 1000) : "0");
  const [anchors, setAnchors] = useState<Anchor[]>([{ filterId: filters[0]?.filterId ?? "", seconds: "" }]);
  const [preview, setPreview] = useState<AlignmentPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tracks = useAudioTracks(target, true);
  const clips = useFilterClip(target);
  const byId = new Map(filters.map((filter) => [filter.filterId, filter]));

  const change = <T,>(update: (value: T) => void) => (value: T) => { update(value); setPreview(null); setError(null); };
  const request = (): AlignmentPreviewRequest | string => {
    const track = tracks.ready ? tracks.selection?.track.stream_index ?? null : null;
    if (mode === "offset") {
      const seconds = Number(offset);
      if (offset.trim() === "" || !Number.isFinite(seconds)) return "Enter the offset in seconds.";
      return { anchors: null, offset_ms: Math.round(seconds * 1000), audio_track_index: track };
    }
    const filled = anchors.filter((anchor) => anchor.seconds.trim());
    if (!filled.length) return "Place at least one anchor.";
    if (filled.some((anchor) => !byId.has(anchor.filterId) || !(Number(anchor.seconds) >= 0))) return "Enter a nonnegative time for each anchor.";
    return { anchors: filled.map((anchor) => ({ filter_id: anchor.filterId, local_start_ms: Math.round(Number(anchor.seconds) * 1000) })),
      offset_ms: null, audio_track_index: track };
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await action(); } catch (reason) { setError(masterErrorText(reason)); } finally { setBusy(false); }
  };
  const previewAlignment = () => {
    const body = request();
    if (typeof body === "string") { setError(body); return; }
    void run(async () => setPreview(await previewMasterAlignment(linkId, body)));
  };
  const approve = () => preview && void run(async () => {
    await approveMasterAlignment(linkId, preview.token, forRevision);
    onApproved();
  });

  return <section className="master-alignment" aria-labelledby={heading}>
    <h4 id={heading}>Align before applying</h4>
    <p className="muted-copy">This version only loosely matches your file, so its filter times may be off. Line them up with your file before any of them apply.</p>
    <fieldset className="master-alignment-mode" disabled={busy}>
      <legend>Alignment method</legend>
      <label><input type="radio" name={`${heading}-mode`} checked={mode === "offset"} onChange={() => change(setMode)("offset")} /> Shift every filter</label>
      <label><input type="radio" name={`${heading}-mode`} checked={mode === "anchors"} onChange={() => change(setMode)("anchors")} /> Match filters to times on your file</label>
    </fieldset>
    {mode === "offset" && <label className="field"><span>Offset (seconds, negative moves earlier)</span>
      <input type="number" step="any" value={offset} disabled={busy} onChange={(event) => change(setOffset)(event.target.value)} /></label>}
    {mode === "anchors" && <fieldset className="verification-anchors" disabled={busy}>
      <legend>Anchors</legend>
      <p className="muted-copy">Play a filter on your file and enter where it starts. Three or more anchors spread over at least 5 minutes also correct drift.</p>
      {anchors.map((anchor, index) => <div className="verification-fields" key={index}>
        <label className="field"><span>Filter {index + 1}</span>
          <select value={anchor.filterId} onChange={(event) => change(setAnchors)(anchors.map((item, position) =>
            position === index ? { ...item, filterId: event.target.value } : item))}>
            {filters.map((filter) => <option key={filter.filterId} value={filter.filterId}>{formatMs(filter.startMs)} · {filterName(filter)}</option>)}
          </select></label>
        <label className="field"><span>Starts on your file at (seconds)</span>
          <input type="number" min="0" step="any" value={anchor.seconds} onChange={(event) => change(setAnchors)(anchors.map((item, position) =>
            position === index ? { ...item, seconds: event.target.value } : item))} /></label>
      </div>)}
      <button type="button" className="btn-secondary compact-button" disabled={anchors.length >= MAX_ANCHORS || !filters.length}
        onClick={() => change(setAnchors)([...anchors, { filterId: filters[0]?.filterId ?? "", seconds: "" }])}>Add anchor</button>
    </fieldset>}
    <AudioTrackField {...tracks} disabled={busy} label="Audio track for previews" showSelectedLabel={false} />
    {error && <p className="form-error" role="alert">{error}</p>}
    {busy && <p role="status" className="form-status">Working…</p>}
    {preview && <div className="master-alignment-preview">
      <p role="status"><strong>Offset {(preview.alignment.offsetMs / 1000).toFixed(3)} s</strong>
        {preview.alignment.scale !== 1 && ` · drift ×${preview.alignment.scale.toFixed(5)}`} · {preview.alignment.anchors} {preview.alignment.anchors === 1 ? "anchor" : "anchors"}</p>
      <div className="scan-verification-cues" tabIndex={0} role="region" aria-label="Aligned filter times">
        <table className="library-table"><thead><tr><th scope="col">Filter</th><th scope="col">Aligned time</th><th scope="col">Clip</th></tr></thead>
          <tbody>{preview.rows.map((row) => {
            const filter = byId.get(row.filterId);
            const range = { startMs: row.alignedStartMs, endMs: row.alignedEndMs };
            return <tr key={row.filterId}><td>{filter ? filterName(filter) : row.filterId}</td><td>{formatRange(range)}</td>
              <td><button type="button" className="btn-secondary compact-button" disabled={clips.loading !== null}
                aria-label={`Play clip at ${formatMs(row.alignedStartMs)}`}
                onClick={() => void clips.play(row.filterId, rangePreview({ ...range, action: filter?.action ?? "skip" }))}>
                {clips.loading === row.filterId ? "Loading…" : "▶ Play"}</button></td></tr>;
          })}</tbody></table>
      </div>
      <FilterClipPlayer clip={clips.clip} error={clips.error} />
    </div>}
    <div className="button-row">
      <button type="button" className="btn-secondary" disabled={busy} onClick={previewAlignment}>Preview alignment</button>
      <button type="button" className="btn-primary" disabled={busy || !preview} onClick={approve}>Approve alignment</button>
    </div>
  </section>;
}
