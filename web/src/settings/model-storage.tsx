import { useCallback, useEffect, useState } from "react";
import {
  cancelModelDownload,
  fetchModelStorage,
  prefetchModel,
  removeModel,
} from "../api/api-service";
import type { ManagedModelArtifact, ModelStorageStatus } from "../api/api-system-types";
import { formatBytes } from "../shared/display";
import { useCan } from "../auth/auth-gate";
import { ModelDownloads } from "./model-downloads";

/** Show model availability and manage artifact downloads and removal. */
export function ModelStorage() {
  const mayView = useCan("models.inventory.view");
  return mayView ? <ModelInventory /> : <ModelDownloads />;
}

function ModelInventory() {
  const mayDownload = useCan("models.download");
  const mayCancel = useCan("models.download.cancel");
  const mayDelete = useCan("models.delete");
  const [storage, setStorage] = useState<ModelStorageStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setStorage(await fetchModelStorage()); setMessage(null); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Unable to load model storage."); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!storage?.artifacts.some((artifact) => artifact.state.state === "downloading")) return;
    const timer = window.setInterval(() => void load(), 1000);
    return () => window.clearInterval(timer);
  }, [load, storage]);
  const act = async (key: string, action: () => Promise<{ status: ModelStorageStatus | null }>) => {
    setWorking(key); setMessage(null);
    try { setStorage((await action()).status); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Unable to update model storage."); }
    finally { setWorking(null); }
  };
  const capability = (backendId: string) => storage?.model_capabilities.find((item) => item.id === backendId);
  return <section className="settings-card model-storage" aria-labelledby="model-storage-heading">
    <div className="section-heading"><div><h2 id="model-storage-heading">Model Storage</h2><p>Weights are downloaded on demand into Editarr’s managed cache. Removing a model never removes scans, settings, or credentials.</p></div><button className="btn-secondary compact-button" onClick={() => void load()} disabled={working !== null}>Refresh</button></div>
    {message && <p className="form-status" role="status">{message}</p>}
    {!storage && !message && <p className="loading-state">Loading model storage…</p>}
    {storage?.artifacts.map((artifact: ManagedModelArtifact) => {
      const backends = artifact.backend_ids.map((id) => capability(id)).filter(Boolean);
      const unavailable = backends.length > 0 && backends.every((item) => item?.availability === "unavailable");
      const downloading = artifact.state.state === "downloading";
      const stateLabel = artifact.state.state.replace(/_/g, " ");
      const progress = artifact.state.bytes_total && artifact.state.bytes_downloaded !== null
        ? Math.min(100, (artifact.state.bytes_downloaded / artifact.state.bytes_total) * 100) : null;
      const backendId = artifact.backend_ids[0];
      return <article className="model-artifact" key={artifact.artifact_id} aria-label={`${artifact.artifact_id} model`}>
        <div className="model-artifact-heading"><div><h3>{backends.map((item) => item?.display_name).filter(Boolean).join(" · ") || artifact.artifact_id}</h3><p>{artifact.artifact_id} · {formatBytes(artifact.expected_size)}</p></div><span className={`model-state model-state-${artifact.state.state}`}>{stateLabel}</span></div>
        <dl className="model-details"><div><dt>Stage</dt><dd>{[...new Set(backends.map((item) => item?.stage).filter(Boolean))].join(", ") || "Managed artifact"}</dd></div><div><dt>Device</dt><dd>{[...new Set(backends.map((item) => item?.device).filter(Boolean))].join(", ") || "Unavailable"}</dd></div><div><dt>License</dt><dd><a href={artifact.source_url} target="_blank" rel="noreferrer">{artifact.license_spdx || "Review required"}</a>{artifact.license_status === "license-review-required" ? " · release review required" : ""}</dd></div></dl>
        {downloading && <div className="model-progress"><progress value={progress ?? undefined} max="100" aria-label={`${artifact.artifact_id} download progress`} /> <span>{formatBytes(artifact.state.bytes_downloaded)} of {formatBytes(artifact.state.bytes_total)}</span></div>}
        {artifact.state.error_detail && <p className="form-error" role="alert">{artifact.state.error_detail}</p>}
        {unavailable && <p className="muted-copy">Unavailable here: {backends.map((item) => item?.reason?.replace(/_/g, " ")).filter(Boolean).join(", ")}. Windows and Linux retain CPU fallback; CUDA is shown only when the packaged backend and host NVIDIA driver pass the runtime probe.</p>}
        <div className="button-row"><a className="btn-secondary compact-button" href={artifact.source_url} target="_blank" rel="noreferrer">Source</a>{downloading ? mayCancel && <button className="danger-button compact-button" onClick={() => void act(`cancel-${artifact.artifact_id}`, () => cancelModelDownload(artifact.artifact_id))} disabled={working !== null}>{working === `cancel-${artifact.artifact_id}` ? "Cancelling…" : "Cancel download"}</button> : mayDownload && artifact.removable && backendId && artifact.state.state !== "ready" && !unavailable && <button className="btn-primary compact-button" onClick={() => void act(`download-${backendId}`, () => prefetchModel(backendId))} disabled={working !== null}>{working === `download-${backendId}` ? "Starting…" : "Download"}</button>}{mayDelete && artifact.state.state === "ready" && artifact.removable && <button className="danger-button compact-button" onClick={() => void act(`remove-${artifact.artifact_id}`, () => removeModel(artifact.artifact_id))} disabled={working !== null}>{working === `remove-${artifact.artifact_id}` ? "Removing…" : "Remove"}</button>}</div>
      </article>;
    })}
    {storage?.model_capabilities.some((item) => item.enrichment_behavior === "detector_findings_only") && <p className="muted-copy">On Windows and Linux, scans retain detector findings when Qwen enrichment is unavailable. Supported models use CUDA when available, with CPU as a fallback.</p>}
  </section>;
}
