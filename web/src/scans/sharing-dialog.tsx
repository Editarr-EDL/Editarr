import { useEffect, useRef, useState } from "react";
import { CommunityApiError } from "../api/api-community";
import {
  approveSharingPreview, cancelPublication, checkPublication, discardSharingPreview,
  fetchSharingContext, prepareSharingPreview, retryPublication,
  type PublicationJob, type SharingContext, type SharingPreview, type SharingSource,
} from "../api/api-community-sharing";
import type { EntityType, IdentityProvider } from "../api/api-media-identity";
import { ModalDialog } from "../ui/ModalDialog";
import { failureMessage } from "../settings/community/community-state";
import { audioTrackLabel } from "./audio-track-field";
import { SharingPreviewDetails } from "./sharing-preview";
import { useCan, useOptionalAuthenticationGate, usePrincipal } from "../auth/auth-gate";
import { useCommunityLifetime } from "../settings/community/community-access";
import "../styles/community-sharing.css";

const messages: Record<string, string> = {
  preview_not_configured: "Ask your Editarr instance operator to configure sharing limits.",
  not_connected: "Connect this installation in Settings before sharing.",
  preview_stale: "The source changed. Refresh the source and generate a new preview.",
  preview_expired: "The preview expired. Generate a new preview.",
  preview_capacity: "Too many previews are open. Close unused dialogs and retry.",
  media_identity_required: "Confirm a supported TMDB/TVDB association before sharing.",
  publication_author_mismatch: "The signing identity changed. Generate a new preview.",
  preview_invalid: "This source or choice cannot be shared. Check classification, source timeline and audio source, then generate a new preview.",
  source_unavailable: "This source is unavailable or still running. Refresh after it settles.",
  source_duration_unknown: "Source duration is unknown. Complete its timing metadata before sharing.",
  invalid_media_reference: "Enter a supported TMDB/TVDB ID or URL with matching episode context.",
  source_permission_unknown: "Original community reuse permission is unknown. Local review remains available.",
  source_permission_incompatible: "Original community terms do not permit this reuse. Local review remains available.",
  sharing_consent_required: "Sharing consent changed. Generate and approve a new preview.",
  publication_limit: "The contribution exceeds configured upload bounds. Generate a smaller new revision.",
  operation_conflict: "The saved publication conflicts with service state. Check its outcome before proceeding.",
};
function errorText(code: string) {
  return messages[code] || failureMessage(code);
}

function apiErrorCode(reason: unknown) {
  return reason instanceof CommunityApiError ? reason.code : "network_unavailable";
}

function SharingDialog({ source, initialName, onClose }: {
  source: SharingSource; initialName: string; onClose: () => void;
}) {
  const principal = usePrincipal();
  const gate = useOptionalAuthenticationGate();
  const lifetime = useCommunityLifetime();
  const publishOwn = useCan("community.publish.own_review");
  const publishOther = useCan("community.publish.other_review");
  const publishScan = useCan("community.publish.scan_evidence");
  const retryOwn = useCan("community.publications.retry.own");
  const retryOther = useCan("community.publications.retry.other");
  const [context, setContext] = useState<SharingContext | null>(null);
  const [name, setName] = useState(initialName);
  const [provider, setProvider] = useState<IdentityProvider>("tmdb");
  const [entity, setEntity] = useState<EntityType>("movie");
  const [reference, setReference] = useState("");
  const [series, setSeries] = useState("");
  const [season, setSeason] = useState("");
  const [episode, setEpisode] = useState("");
  const [useSaved, setUseSaved] = useState(true);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
  const [audioSource, setAudioSource] = useState("");
  const [preview, setPreview] = useState<SharingPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [job, setJob] = useState<PublicationJob | null>(null);
  const [busy, setBusy] = useState(true);
  const [activity, setActivity] = useState("Checking community access...");
  const [cancelling, setCancelling] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const livePreview = useRef<string | null>(null);
  const running = useRef(false);
  const cancelRunning = useRef(false);
  const active = useRef(true);
  const mayPublish = source.kind === "scan" ? publishScan : context?.sourceOwnerId === principal?.userId ? publishOwn : publishOther;
  const jobScope = job?.ownerId === principal?.userId ? "own" : "other";
  const jobHeld = job?.errorCode === "not_connected" && !(jobScope === "own" && context?.accessVerified);
  const mayCheck = useCan(`community.publications.check.${jobScope}`);
  const mayRetry = useCan(`community.publications.retry.${jobScope}`);
  const mayRetract = useCan(`community.publications.retract.${jobScope}`);
  const current = () => active.current && lifetime.current();

  async function reload(signal = lifetime.signal()) {
    if (!current()) return;
    const value = await fetchSharingContext(source, signal);
    if (!current() || signal?.aborted) return;
    setContext(value);
    setUseSaved(Boolean(value.mediaIdentity));
    if (value.audioSources.length === 1) setAudioSource(value.audioSources[0].scanId);
    if (value.accessError) setErrorCode(value.accessError);
  }
  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    void reload(controller.signal).catch((reason) => {
      if (current() && !controller.signal.aborted) setErrorCode(apiErrorCode(reason));
    }).finally(() => { if (current() && !controller.signal.aborted) setBusy(false); });
    return () => {
      active.current = false;
      controller.abort();
      livePreview.current = null;
    };
  }, [source.kind, source.id]);

  function invalidate(discard = true) {
    if (discard && current() && livePreview.current) void discardSharingPreview(livePreview.current, lifetime.signal()).catch(() => {});
    livePreview.current = null; setPreview(null); setConfirmed(false);
  }
  async function run(action: () => Promise<void>, label = "Updating contribution...") {
    if (!current() || running.current) return;
    running.current = true; setBusy(true); setActivity(label); setErrorCode(null);
    try { await action(); }
    catch (reason) {
      if (!current()) return;
      setErrorCode(apiErrorCode(reason));
      if (reason instanceof CommunityApiError && [403, 404].includes(reason.status)) {
        invalidate(false); setContext(null); setJob(null); setName(""); setReference(""); setIdentityConfirmed(false);
        gate?.refreshAuthenticationStatus?.();
      } else if (reason instanceof CommunityApiError && ["preview_stale", "preview_expired", "publication_author_mismatch"].includes(reason.code)) {
        invalidate(false); setContext(null); setIdentityConfirmed(false);
        try { await reload(lifetime.signal()); } catch {}
      }
    } finally { running.current = false; if (current()) setBusy(false); }
  }
  async function prepare() {
    if (!mayPublish || !current()) return;
    invalidate();
    const value = await prepareSharingPreview(source, {
      name: name.trim(), identity_confirmed: identityConfirmed, audio_source_scan_id: audioSource || null,
      media: useSaved && context?.mediaIdentity ? null : {
        provider, entity_type: entity, reference,
        ...(series ? { series_reference: series } : {}),
        ...(entity === "episode" && season !== "" && episode !== "" ? {
          episode_numbers: [{ scheme: "aired", season: Number(season), number: Number(episode) }],
        } : {}),
      },
    }, lifetime.signal());
    if (!current()) return;
    livePreview.current = value.previewId; setPreview(value);
  }
  async function share() {
    if (!mayPublish || !current() || !preview || !confirmed) return;
    const accepted = await approveSharingPreview(preview.previewId, lifetime.signal());
    if (!current()) return;
    acceptJob(accepted);
    const canRetryAccepted = accepted.ownerId === principal?.userId ? retryOwn : retryOther;
    if (canRetryAccepted && current()) acceptJob(await retryPublication(accepted.operationId, lifetime.signal()));
  }
  function acceptJob(value: PublicationJob) {
    if (!current()) return;
    setJob((current) => current?.operationId === value.operationId && current.state === "published" ? current : value);
  }
  async function cancelJob() {
    if (!mayRetract || !current() || !job || cancelRunning.current) return;
    cancelRunning.current = true; setCancelling(true); setErrorCode(null);
    try { acceptJob(await cancelPublication(job.operationId, lifetime.signal())); }
    catch (reason) {
      if (!current()) return;
      setErrorCode(apiErrorCode(reason));
      if (reason instanceof CommunityApiError && [403, 404].includes(reason.status)) {
        invalidate(false); setContext(null); setJob(null); setName(""); setIdentityConfirmed(false);
        gate?.refreshAuthenticationStatus?.();
      }
    }
    finally { cancelRunning.current = false; if (current()) setCancelling(false); }
  }
  const ready = context && identityConfirmed && name.trim() && (useSaved && context.mediaIdentity || reference.trim())
    && (context.audioSources.length <= 1 || audioSource);

  return <ModalDialog labelledBy="sharing-heading" className="sharing-dialog" onClose={busy ? undefined : onClose}>
    <div className="sharing-heading"><h2 id="sharing-heading">Share {source.kind === "review" ? "review" : "scan"}</h2>
      <button type="button" className="btn-secondary compact-button" onClick={onClose} disabled={busy}>Close</button></div>
    {busy && <p role="status">{activity}</p>}
    {cancelling && <p role="status">Requesting local cancellation...</p>}
    {errorCode && <p role="alert" className="form-error">{errorText(errorCode)}</p>}
    {errorCode !== "preview_not_configured" && <a href="#/settings/profile">Community connection settings</a>}
    {!context && !busy && <button type="button" onClick={() => void run(() => reload())}>Refresh source</button>}
    {mayPublish && context && !job && <>
      <label className="field"><span>Contribution name</span><input value={name} maxLength={128} disabled={busy}
        onChange={(event) => { setName(event.target.value); invalidate(); }} /></label>
      {context.mediaIdentity && <label className="checkbox-label"><input type="checkbox" checked={useSaved} disabled={busy}
        onChange={(event) => { setUseSaved(event.target.checked); setIdentityConfirmed(false); invalidate(); }} />Use confirmed media association</label>}
      {useSaved && context.mediaIdentity ? <pre className="sharing-association" role="region" aria-label="Confirmed media association" tabIndex={0}>{JSON.stringify(context.mediaIdentity, null, 2)}</pre> : <div className="sharing-fields">
        <label className="field"><span>Provider</span><select value={provider} disabled={busy} onChange={(event) => { setProvider(event.target.value as IdentityProvider); invalidate(); setIdentityConfirmed(false); }}><option value="tmdb">TMDB</option><option value="tvdb">TVDB</option></select></label>
        <label className="field"><span>Entity</span><select value={entity} disabled={busy} onChange={(event) => { setEntity(event.target.value as EntityType); setSeries(""); setSeason(""); setEpisode(""); invalidate(); setIdentityConfirmed(false); }}><option value="movie">Movie</option><option value="series">Series</option><option value="episode">Episode</option></select></label>
        <label className="field sharing-wide"><span>TMDB/TVDB ID or URL</span><input value={reference} disabled={busy} maxLength={2048} onChange={(event) => { setReference(event.target.value); invalidate(); setIdentityConfirmed(false); }} /></label>
        {entity === "episode" && <>
          <label className="field sharing-wide"><span>{provider === "tvdb" ? "TVDB series ID" : "Series ID or URL"}</span><input value={series} disabled={busy} onChange={(event) => { setSeries(event.target.value); invalidate(); setIdentityConfirmed(false); }} /></label>
          <label className="field"><span>Aired season</span><input type="number" min={0} value={season} disabled={busy} onChange={(event) => { setSeason(event.target.value); invalidate(); setIdentityConfirmed(false); }} /></label>
          <label className="field"><span>Aired episode</span><input type="number" min={0} value={episode} disabled={busy} onChange={(event) => { setEpisode(event.target.value); invalidate(); setIdentityConfirmed(false); }} /></label>
        </>}
      </div>}
      <label className="checkbox-label"><input type="checkbox" checked={identityConfirmed} disabled={busy}
        onChange={(event) => { setIdentityConfirmed(event.target.checked); invalidate(); }} />I confirm this TMDB/TVDB association.</label>
      {context.audioSources.length > 0 && <label className="field"><span>Contribution audio source</span>
        <select aria-label="Contribution audio source" value={audioSource} disabled={busy || context.audioSources.length === 1}
          onChange={(event) => { setAudioSource(event.target.value); invalidate(); }}>
          <option value="">Choose one analyzed source</option>{context.audioSources.map((item) => <option key={item.scanId} value={item.scanId}>{item.track ? audioTrackLabel(item.track) : "Unknown analyzed track / Review required"}</option>)}
        </select>
        <span className="sharing-track">{context.audioSources.find((item) => item.scanId === audioSource)?.track
          ? audioTrackLabel(context.audioSources.find((item) => item.scanId === audioSource)!.track!) : "Unknown analyzed track / Review required"}</span>
      </label>}
      <div className="button-row"><button type="button" className="btn-secondary" disabled={busy || !ready}
        onClick={() => void run(prepare, "Generating preview...")}>Generate preview</button><button type="button" className="btn-secondary" disabled={busy}
        onClick={() => { invalidate(); setIdentityConfirmed(false); void run(() => reload(lifetime.signal())); }}>Refresh source</button></div>
      {preview && <>
        <SharingPreviewDetails value={preview} />
        <p>{preview.terms.text}</p>
        <label className="checkbox-label"><input type="checkbox" checked={confirmed} disabled={busy}
          onChange={(event) => setConfirmed(event.target.checked)} />I may share this data and its notes, and I approve this exact preview.</label>
        <button type="button" className="btn-primary" disabled={busy || !confirmed || !context.accessVerified}
          onClick={() => void run(share, "Publishing approved contribution...")}>Share contribution</button>
      </>}
    </>}
      {context && !job && context.jobs.length > 0 && <section aria-label="Saved publications"><h3>Saved publications</h3>
        {context.jobs.map((saved) => <button type="button" className="btn-secondary" key={saved.operationId}
          onClick={() => { invalidate(); acceptJob(saved); }} disabled={busy}>{saved.state} · {saved.revisionId.slice(0, 12)}</button>)}
      </section>}
    {job && <section aria-label="Publication status"><h3>{job.state === "published" ? "Published" : job.state.replace(/_/g, " ")}</h3>
      <dl className="sharing-facts"><dt>Original contributor</dt><dd>{job.contributorId}</dd><dt>Revision</dt><dd>{job.revisionId}</dd><dt>Phase</dt><dd>{job.phase}</dd></dl>
      {job.errorCode && (job.errorCode !== "not_connected" || jobHeld) && <p role="alert">{jobHeld
        ? "This contributor is disconnected. Saved publications are held until reconnect."
        : errorText(job.errorCode)}</p>}
      {job.state === "cancel_pending" && <p>The remote outcome is uncertain. Cancellation does not delete a published contribution.</p>}
      {job.result && <p>Published {new Date(job.result.committedAt * 1000).toLocaleString()}</p>}
      <div className="button-row">{mayCheck && <button type="button" className="btn-secondary" disabled={busy || job.state === "published" || jobHeld}
        onClick={() => void run(async () => acceptJob(await checkPublication(job.operationId, lifetime.signal())), "Checking publication...")}>Check outcome</button>}
        {mayRetry && <button type="button" className="btn-primary" disabled={busy || !["pending", "paused"].includes(job.state) || job.cancelRequested || jobHeld}
          onClick={() => void run(async () => acceptJob(await retryPublication(job.operationId, lifetime.signal())), "Publishing saved contribution...")}>Retry saved contribution</button>}
        {mayRetract && <button type="button" className="btn-secondary" disabled={cancelling || ["published", "cancelled", "cancel_pending"].includes(job.state)}
          onClick={() => void cancelJob()}>Cancel publication</button>}</div>
    </section>}
  </ModalDialog>;
}

export function ShareAction({ source, name, disabled = false }: { source: SharingSource; name: string; disabled?: boolean }) {
  const publishOwn = useCan("community.publish.own_review");
  const publishOther = useCan("community.publish.other_review");
  const publishScan = useCan("community.publish.scan_evidence");
  const checkOwn = useCan("community.publications.check.own");
  const checkOther = useCan("community.publications.check.other");
  const [open, setOpen] = useState(false);
  if (!(source.kind === "scan" ? publishScan : publishOwn || publishOther) && !checkOwn && !checkOther) return null;
  return <><button type="button" className="btn-secondary compact-button" disabled={disabled}
    aria-label={`Share ${name}`} onClick={() => setOpen(true)}>Share</button>
    {open && <SharingDialog source={source} initialName={name} onClose={() => setOpen(false)} />}</>;
}
