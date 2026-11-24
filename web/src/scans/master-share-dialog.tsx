import { useEffect, useId, useRef, useState } from "react";
import {
  discardSharingPreview, fetchSharingContext, type PublicationJob, type SharingContext, type SharingSource,
} from "../api/api-community-sharing";
import { approveMasterSharingPreview, prepareMasterSharingPreview } from "../api/api-community-updates";
import type { MasterSharingPreview } from "../api/api-community-master-types";
import { useCan, usePrincipal } from "../auth/auth-gate";
import { ModalDialog } from "../ui/ModalDialog";
import { audioTrackLabel } from "./audio-track-field";
import { estimateText, masterErrorText, versionChoice, type PickerValue } from "./community-master-format";
import { VersionPicker } from "./version-picker";

/**
 * Share a scan or review into the community master set. The version picker is required; once chosen,
 * the preview shows estimated counts. Approval queues the existing publication job.
 */
export function MasterShareDialog({ source, linkId = null, onClose }: {
  source: SharingSource; linkId?: string | null; onClose: () => void;
}) {
  const heading = useId();
  const [context, setContext] = useState<SharingContext | null>(null);
  const [version, setVersion] = useState<PickerValue>({ selection: "", otherText: "" });
  const [audioSource, setAudioSource] = useState("");
  const [preview, setPreview] = useState<MasterSharingPreview | null>(null);
  const [job, setJob] = useState<PublicationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const retained = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchSharingContext(source, controller.signal).then((value) => {
      setContext(value);
      if (value.audioSources.length === 1) setAudioSource(value.audioSources[0].scanId);
    }).catch((reason) => { if (!controller.signal.aborted) setError(masterErrorText(reason)); });
    return () => controller.abort();
  }, [source.kind, source.id]);
  useEffect(() => () => { if (retained.current) void discardSharingPreview(retained.current).catch(() => {}); }, []);

  const choice = versionChoice(version);
  const audioReady = !context || context.audioSources.length === 0 || !!audioSource;
  const prepare = async () => {
    if (!choice || !audioReady) return;
    setBusy(true); setError(null); setPreview(null);
    if (retained.current) void discardSharingPreview(retained.current).catch(() => {});
    retained.current = null;
    try {
      const value = await prepareMasterSharingPreview(source, { identity_confirmed: true, media: null,
        audio_source_scan_id: audioSource || null, link_id: linkId, version_choice: choice });
      retained.current = value.previewId;
      setPreview(value);
    } catch (reason) { setError(masterErrorText(reason)); }
    finally { setBusy(false); }
  };
  const share = async () => {
    if (!preview) return;
    setBusy(true); setError(null);
    try {
      setJob(await approveMasterSharingPreview(preview.previewId));
      retained.current = null;
    } catch (reason) { setError(masterErrorText(reason)); }
    finally { setBusy(false); }
  };
  const edit = <T,>(update: (value: T) => void) => (value: T) => { update(value); setPreview(null); };

  return <ModalDialog labelledBy={heading} className="sharing-dialog master-share-dialog" onClose={busy ? undefined : onClose}>
    <div className="sharing-heading"><h2 id={heading}>Share to community filters</h2>
      <button type="button" className="btn-secondary compact-button" onClick={onClose} disabled={busy}>Close</button></div>
    {error && <p role="alert" className="form-error">{error}</p>}
    {job ? <section aria-label="Publication status"><h3>Queued for sharing</h3>
      <p className="muted-copy">Your contribution is queued. Its status appears with your saved publications.</p></section> : <>
      <VersionPicker value={version} onChange={edit(setVersion)} disabled={busy} required label="Which version is this?" />
      {context && context.audioSources.length > 1 && <label className="field"><span>Contribution audio source</span>
        <select value={audioSource} disabled={busy} onChange={(event) => edit(setAudioSource)(event.target.value)}>
          <option value="">Choose one analyzed source</option>
          {context.audioSources.map((item) => <option key={item.scanId} value={item.scanId}>
            {item.track ? audioTrackLabel(item.track) : "Unknown analyzed track"}</option>)}
        </select></label>}
      {preview && <section className="master-share-preview" aria-label="Sharing summary">
        <p className="sharing-name">{preview.work.title}{preview.work.year ? ` (${preview.work.year})` : ""} · {preview.versionChoice.label}</p>
        <p role="status"><strong>{estimateText(preview)}</strong></p>
        <p className="muted-copy">Counts are estimates. The community service makes the final match.</p>
        {preview.warnings.length > 0 && <ul className="verification-issues">{preview.warnings.map((warning) =>
          <li key={warning}>{warning.replace(/_/g, " ")}</li>)}</ul>}
      </section>}
      <div className="button-row">
        <button type="button" className="btn-secondary" disabled={busy || !choice || !audioReady} onClick={() => void prepare()}>
          {preview ? "Refresh summary" : "Preview summary"}</button>
        <button type="button" className="btn-primary" disabled={busy || !preview} onClick={() => void share()}>Share</button>
      </div>
    </>}
  </ModalDialog>;
}

/** Entry point next to the existing Share action for master-set sharing. */
export function MasterShareAction({ source, name, disabled = false }: { source: SharingSource; name: string; disabled?: boolean }) {
  const principal = usePrincipal();
  const publishOwn = useCan("community.publish.own_review");
  const publishOther = useCan("community.publish.other_review");
  const publishScan = useCan("community.publish.scan_evidence");
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  if (!principal || !(source.kind === "scan" ? publishScan : publishOwn || publishOther)) return null;
  return <>
    <button type="button" ref={trigger} className="btn-secondary compact-button" disabled={disabled}
      aria-label={`Share ${name} to community filters`} onClick={() => setOpen(true)}>Share filters</button>
    {open && <MasterShareDialog source={source} onClose={() => { setOpen(false); trigger.current?.focus(); }} />}
  </>;
}
