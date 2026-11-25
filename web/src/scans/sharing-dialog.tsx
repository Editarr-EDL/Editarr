import { useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import {
  approveSharingPreview, cancelPublication, checkPublication, discardSharingPreview,
  fetchSharingContext, retryPublication,
  type PublicationJob, type SharingContext, type SharingDraft, type SharingSource,
} from "../api/api-community-sharing";
import type { EntityType, IdentityProvider, MediaIdentity } from "../api/api-media-identity";
import { ModalDialog } from "../ui/ModalDialog";
import { failureMessage } from "../settings/community/community-state";
import { audioTrackLabel } from "./audio-track-field";
import { SharingPreviewDetails } from "./sharing-preview";
import { sharingAccessLost, sharingErrorCode, useSharingSummary } from "./use-sharing-summary";
import { useCan, useOptionalAuthenticationGate, usePrincipal } from "../auth/auth-gate";
import { useCommunityLifetime } from "../settings/community/community-access";
import { versionChoice, type PickerValue } from "./community-master-format";
import { VersionPicker } from "./version-picker";
import "../styles/community-sharing.css";

const messages: Record<string, string> = {
  preview_not_configured: "Ask your Editarr instance operator to configure sharing limits.",
  not_connected: "Connect this installation in Settings before sharing.",
  preview_stale: "The source changed. Review the updated summary before sharing.",
  preview_expired: "The summary expired. Review the updated summary before sharing.",
  preview_capacity: "Too many sharing summaries are open. Close unused dialogs and try again.",
  media_identity_required: "Enter a supported TMDB/TVDB association with the matching episode details.",
  publication_author_mismatch: "The signing identity changed. Review the updated summary before sharing.",
  preview_invalid: "This selection cannot be shared. Check the classification, timing and audio source.",
  source_unavailable: "This source is unavailable or still running. Refresh after it settles.",
  source_duration_unknown: "Source duration is unknown. Complete its timing metadata before sharing.",
  invalid_media_reference: "Enter a supported TMDB/TVDB ID or URL with matching episode context.",
  source_permission_unknown: "Original community reuse permission is unknown. Local review remains available.",
  source_permission_incompatible: "Original community terms do not permit this reuse. Local review remains available.",
  sharing_consent_required: "The terms changed. Review the updated summary before sharing.",
  publication_limit: "The contribution exceeds configured upload bounds. Share a smaller revision.",
  operation_conflict: "The saved publication conflicts with service state. Check its outcome before proceeding.",
  work_identity_required: "Add a TMDB or TVDB ID (with season and episode for TV) before sharing.",
  link_unavailable: "The community filter set for this file changed. Close this dialog and try again.",
};
function errorText(code: string) {
  return messages[code] || failureMessage(code);
}

function associationLabel(identity: MediaIdentity) {
  const identifiers = [...identity.identifiers, ...identity.series_identifiers].map(
    ({ provider, entity_type, value }) => provider.toUpperCase() + " " + entity_type + " " + value,
  );
  const episodes = identity.episode_span.length ? identity.episode_span : identity.episode_numbers;
  for (const { scheme, season, number } of episodes) {
    if (season !== null) identifiers.push("S" + String(season).padStart(2, "0") + "E" + String(number).padStart(2, "0"));
    else identifiers.push(scheme.toUpperCase() + " episode " + number);
  }
  return identifiers.join(" · ");
}

function SharingDialog({ source, initialName, onClose, returnFocusRef }: {
  source: SharingSource; initialName: string; onClose: () => void; returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const heading = useId();
  const termsId = useId();
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
  const [editing, setEditing] = useState(false);
  const [audioSource, setAudioSource] = useState("");
  const [version, setVersion] = useState<PickerValue>({ selection: "", otherText: "" });
  const [job, setJob] = useState<PublicationJob | null>(null);
  const [busy, setBusy] = useState(true);
  const [activity, setActivity] = useState("Checking community access...");
  const [cancelling, setCancelling] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const running = useRef(false);
  const cancelRunning = useRef(false);
  const active = useRef(true);
  const accessLost = useRef(false);
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
    accessLost.current = false;
    setContext({ ...value });
    setAudioSource((selected) => value.audioSources.length === 1 ? value.audioSources[0].scanId
      : value.audioSources.some((item) => item.scanId === selected) ? selected : "");
  }
  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    void reload(controller.signal).catch((reason) => {
      if (current() && !controller.signal.aborted) setErrorCode(sharingErrorCode(reason));
    }).finally(() => { if (current() && !controller.signal.aborted) setBusy(false); });
    return () => {
      active.current = false;
      controller.abort();
    };
  }, [source.kind, source.id]);

  function clearPrivate(code: string) {
    accessLost.current = true;
    setErrorCode(code);
    setContext(null);
    setJob(null);
    setName("");
    setReference("");
    gate?.refreshAuthenticationStatus?.();
  }
  async function run(action: () => Promise<void>, label = "Updating contribution...") {
    if (!current() || running.current) return;
    running.current = true; setBusy(true); setActivity(label); setErrorCode(null);
    try { await action(); }
    catch (reason) {
      if (!current()) return;
      const code = sharingErrorCode(reason);
      if (sharingAccessLost(reason)) {
        clearPrivate(code);
      } else {
        setErrorCode(code);
        if (["preview_stale", "preview_expired", "publication_author_mismatch", "sharing_consent_required"].includes(code)) {
          setContext(null);
          try { await reload(); }
          catch (failure) { if (current()) setErrorCode(sharingErrorCode(failure)); }
        }
      }
    } finally { running.current = false; if (current()) setBusy(false); }
  }

  const savedMedia = context?.mediaIdentity && !context.mediaIdentity.input_requirement ? context.mediaIdentity : null;
  const usingSaved = !!savedMedia && useSaved;
  const chosenAudio = context?.audioSources.find((item) => item.scanId === audioSource);
  const choice = useMemo(() => versionChoice(version), [version]);
  const draft = useMemo<SharingDraft | null>(() => {
    if (!context?.accessVerified || !mayPublish || job || !name.trim() || !choice ||
      (!usingSaved && !reference.trim()) || (context.audioSources.length > 0 && !chosenAudio)) return null;
    const airedEpisode = !usingSaved && entity === "episode" && (season !== "" || episode !== "");
    if (airedEpisode && ![season, episode].every((number) => /^[0-9]+$/.test(number) && Number.isSafeInteger(Number(number)))) return null;
    return {
      // This normalizes a candidate association for a private summary. Share approves the displayed artifact.
      name: name.trim(), identity_confirmed: true, audio_source_scan_id: chosenAudio?.scanId ?? null,
      link_id: null, version_choice: choice,
      media: usingSaved ? null : {
        provider, entity_type: entity, reference: reference.trim(),
        ...(series.trim() ? { series_reference: series.trim() } : {}),
        ...(airedEpisode ? { episode_numbers: [{ scheme: "aired", season: Number(season), number: Number(episode) }] } : {}),
      },
    };
  }, [context, mayPublish, job, name, choice, usingSaved, reference, chosenAudio, entity, season, episode, provider, series]);
  const { preview, error: summaryError, loading: loadingSummary, retry: retrySummary } =
    useSharingSummary(source, draft, accessLost, clearPrivate);
  const error = errorCode || summaryError || context?.accessError;
  const displayMedia = preview?.preview.artifact.matching?.media ?? (usingSaved ? savedMedia : null);

  async function share() {
    if (!mayPublish || !current() || !preview || !context?.accessVerified) return;
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
      const code = sharingErrorCode(reason);
      if (sharingAccessLost(reason)) clearPrivate(code);
      else setErrorCode(code);
    }
    finally { cancelRunning.current = false; if (current()) setCancelling(false); }
  }
  function close() {
    if (preview && current()) void discardSharingPreview(preview.previewId).catch(() => {});
    onClose();
  }

  return <ModalDialog labelledBy={heading} className="sharing-dialog" returnFocusRef={returnFocusRef} onClose={busy ? undefined : close}>
    <div className="sharing-heading"><h2 id={heading}>Share {source.kind === "review" ? "review" : "scan"}</h2>
      <button type="button" className="btn-secondary compact-button" onClick={close} disabled={busy}>Close</button></div>
    {busy && <p role="status">{activity}</p>}
    {cancelling && <p role="status">Requesting local cancellation...</p>}
    {error && <p role="alert" className="form-error">{errorText(error)}</p>}
    {error === "not_connected" && <a href="#/settings/profile">Community connection settings</a>}
    {(!context || !context.accessVerified) && !busy && error !== "preview_not_configured" &&
      <div className="button-row"><button type="button" className="btn-secondary" onClick={() => void run(() => reload())}>Refresh source</button></div>}
    {mayPublish && context && !job && <>
      <div className="sharing-description">
        <div><p className="sharing-name">{preview?.preview.artifact.name ?? name}</p>
          {displayMedia && <p className="muted-copy">{associationLabel(displayMedia)}</p>}
          {context.audioSources.length === 1 && chosenAudio?.track && <p className="muted-copy">
            {[chosenAudio.track.title || chosenAudio.track.language, chosenAudio.track.codec?.toUpperCase()].filter(Boolean).join(" · ")
              || "Audio stream " + chosenAudio.track.stream_index}
          </p>}
          {source.kind === "review" && context.sourceOwnerId !== principal?.userId && preview &&
            <p className="muted-copy">Sharing as {preview.preview.artifact.author.contributor_id}</p>}
        </div>
        <button type="button" className="btn-secondary compact-button" aria-expanded={editing} disabled={busy}
          onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit details"}</button>
      </div>
      {editing && <label className="field"><span>Contribution name</span><input value={name} maxLength={128} disabled={busy}
        onChange={(event) => setName(event.target.value)} /></label>}
      {editing && savedMedia && <div className="button-row"><button type="button" className="btn-secondary" disabled={busy}
        onClick={() => setUseSaved(!usingSaved)}>{usingSaved ? "Use another media ID" : "Use saved media ID"}</button></div>}
      {!usingSaved && <div className="sharing-fields">
        <label className="field"><span>Provider</span><select value={provider} disabled={busy} onChange={(event) => setProvider(event.target.value as IdentityProvider)}><option value="tmdb">TMDB</option><option value="tvdb">TVDB</option></select></label>
        <label className="field"><span>Entity</span><select value={entity} disabled={busy} onChange={(event) => { setEntity(event.target.value as EntityType); setSeries(""); setSeason(""); setEpisode(""); }}><option value="movie">Movie</option><option value="series">Series</option><option value="episode">Episode</option></select></label>
        <label className="field sharing-wide"><span>TMDB/TVDB ID or URL</span><input value={reference} disabled={busy} maxLength={2048} onChange={(event) => setReference(event.target.value)} /></label>
        {entity === "episode" && <>
          <label className="field sharing-wide"><span>{provider === "tvdb" ? "TVDB series ID" : "Series ID or URL"}</span><input value={series} disabled={busy} maxLength={2048} onChange={(event) => setSeries(event.target.value)} /></label>
          <label className="field"><span>Aired season</span><input type="number" min={0} value={season} disabled={busy} onChange={(event) => setSeason(event.target.value)} /></label>
          <label className="field"><span>Aired episode</span><input type="number" min={0} value={episode} disabled={busy} onChange={(event) => setEpisode(event.target.value)} /></label>
        </>}
      </div>}
      <VersionPicker value={version} onChange={setVersion} disabled={busy} required label="Which version is this?" />
      {context.audioSources.length > 1 && <label className="field"><span>Contribution audio source</span>
        <select value={audioSource} disabled={busy} onChange={(event) => setAudioSource(event.target.value)}>
          <option value="">Choose one analyzed source</option>{context.audioSources.map((item) => <option key={item.scanId} value={item.scanId}>{item.track ? audioTrackLabel(item.track) : "Unknown analyzed track / Review required"}</option>)}
        </select>
      </label>}
      {loadingSummary && !busy && <p role="status">Preparing filter summary…</p>}
      {summaryError && <div className="button-row"><button type="button" className="btn-secondary" disabled={busy}
        onClick={() => { setErrorCode(null); retrySummary(); }}>Retry summary</button></div>}
      {preview && <SharingPreviewDetails value={preview} />}
      <div className="sharing-confirm">
        {preview && <p id={termsId} className="muted-copy">{preview.terms.text}</p>}
        <div className="button-row"><button type="button" className="btn-primary" aria-describedby={preview ? termsId : undefined}
          disabled={busy || !preview || !context.accessVerified}
          onClick={() => void run(share, "Sharing contribution…")}>Share contribution</button></div>
      </div>
    </>}
      {context && !job && context.jobs.length > 0 && <section aria-label="Saved publications"><h3>Saved publications</h3>
        {context.jobs.map((saved) => <button type="button" className="btn-secondary" key={saved.operationId}
          onClick={() => acceptJob(saved)} disabled={busy}>{saved.state} · {saved.operationId.slice(0, 12)}</button>)}
      </section>}
    {job && <section aria-label="Publication status"><h3>{job.state === "published" ? "Published" : job.state.replace(/_/g, " ")}</h3>
      <dl className="sharing-facts"><dt>Original contributor</dt><dd>{job.contributorId}</dd><dt>Operation</dt><dd>{job.operationId}</dd><dt>Phase</dt><dd>{job.phase}</dd></dl>
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
  const trigger = useRef<HTMLButtonElement>(null);
  if (!(source.kind === "scan" ? publishScan : publishOwn || publishOther) && !checkOwn && !checkOther) return null;
  return <><button type="button" ref={trigger} className="btn-secondary compact-button" disabled={disabled}
    aria-label={`Share ${name}`} onClick={() => setOpen(true)}>Share</button>
    {open && <SharingDialog key={source.kind + ":" + source.id} source={source} initialName={name} returnFocusRef={trigger} onClose={() => setOpen(false)} />}</>;
}
