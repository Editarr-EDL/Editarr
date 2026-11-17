import { useCallback, useEffect, useState } from "react";
import {
  cancelModelDownload,
  fetchModelStorage,
  prefetchModel,
  removeModel,
} from "../api/api-service";
import type { ManagedModelArtifact, ModelCapability, ModelStorageStatus } from "../api/api-system-types";
import { formatBytes } from "../shared/display";
import { useCan } from "../auth/auth-gate";
import { ModelDownloads } from "./model-downloads";

const unavailabilityMessages: Record<string, string> = {
  unsupported_platform: "This backend does not support the server’s operating system.",
  unsupported_architecture: "This backend does not support the server’s processor architecture.",
  missing_package: "This build is missing a required runtime package.",
  missing_provider: "A required inference provider is unavailable.",
  deferred_gap: "This backend is unavailable in this build.",
};

type ModelStage = ModelCapability["stage"] | "supporting";

const stages: ReadonlyArray<[ModelStage, string]> = [
  ["audio", "Audio"],
  ["detector", "Video Detection"],
  ["enrichment", "Enrichment"],
  ["supporting", "Supporting Packages"],
];

const deviceNames: Record<Exclude<ModelCapability["device"], "none">, string> = {
  cpu: "CPU",
  cuda: "GPU (CUDA)",
  coreml: "Core ML",
  metal: "GPU (Metal)",
  mlx: "GPU (MLX)",
};

type ModelRow = { artifact: ManagedModelArtifact; backends: ModelCapability[] };

function groupArtifacts({ artifacts, model_capabilities }: ModelStorageStatus) {
  const capabilities = new Map(model_capabilities.map((backend) => [backend.id, backend]));
  const groups: Record<ModelStage, ModelRow[]> = {
    audio: [], detector: [], enrichment: [], supporting: [],
  };
  for (const artifact of artifacts) {
    const backends = artifact.backend_ids.map((id) => capabilities.get(id))
      .filter((backend) => backend !== undefined);
    const usage = new Set(backends.map(({ stage }) => stage));
    if (usage.size === 0) groups.supporting.push({ artifact, backends });
    for (const stage of usage) groups[stage].push({ artifact, backends });
  }
  return groups;
}

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
  const groups = storage && groupArtifacts(storage);
  const storageUsed = storage?.artifacts.reduce((total, { state, expected_size }) => {
    if (state.state === "ready") return total + (state.bytes_downloaded ?? expected_size ?? 0);
    if (state.state === "downloading") return total + (state.bytes_downloaded ?? 0);
    return total;
  }, 0);
  return (
    <section className="model-storage" aria-label="Model inventory">
      <div className="model-storage-toolbar">
        <p className="muted-copy">Editarr downloads model weights on demand.</p>
        <button className="btn-secondary compact-button" onClick={() => void load()} disabled={working !== null}>Refresh</button>
      </div>
      {message && <p className="form-status" role="status">{message}</p>}
      {!storage && !message && <p className="loading-state">Loading model storage…</p>}
      {storageUsed !== undefined && (
        <dl className="model-storage-total">
          <dt>Estimated Total Storage Used</dt>
          <dd>{formatBytes(storageUsed)}</dd>
        </dl>
      )}
      {groups && stages.map(([stage, title]) => {
        const rows = groups[stage];
        if (rows.length === 0) return null;
        const downloading = rows.filter(({ artifact }) => artifact.state.state === "downloading").length;
        return (
          <details className="model-stage" key={stage}>
            <summary>
              <span className="model-stage-title">{title}</span>
              <span className="model-stage-count">{rows.length} {rows.length === 1 ? "item" : "items"}</span>
              {downloading > 0 && <span className="model-state model-state-downloading">{downloading} downloading</span>}
            </summary>
            {rows.map(({ artifact, backends }) => (
              <ModelArtifactRow
                key={artifact.artifact_id}
                artifact={artifact}
                backends={backends}
                working={working}
                act={act}
                mayDownload={mayDownload}
                mayCancel={mayCancel}
                mayDelete={mayDelete}
              />
            ))}
          </details>
        );
      })}
      {storage?.model_capabilities.some((item) => item.enrichment_behavior === "detector_findings_only") && (
        <p className="muted-copy">Video scans retain detector findings when Qwen enrichment is unavailable.</p>
      )}
    </section>
  );
}

interface ModelArtifactRowProps extends ModelRow {
  working: string | null;
  act: (key: string, action: () => Promise<{ status: ModelStorageStatus | null }>) => Promise<void>;
  mayDownload: boolean;
  mayCancel: boolean;
  mayDelete: boolean;
}

function ModelArtifactRow({ artifact, backends, working, act, mayDownload, mayCancel, mayDelete }: ModelArtifactRowProps) {
  const unavailable = backends.length > 0 && backends.every((item) => item.availability === "unavailable");
  const { artifact_id: id, state, removable, source_url: source } = artifact;
  const downloading = state.state === "downloading";
  const ready = state.state === "ready";
  const displayState = unavailable && ready ? "cached"
    : unavailable && (state.state === "not_downloaded" || state.state === "not_required") ? "unavailable"
      : state.state;
  const devices = new Set(artifact.device
    ? [artifact.device]
    : backends.map(({ device }) => device).filter((device) => device !== "none"));
  const deviceLabel = unavailable ? "Unavailable" : [...devices].map((device) => deviceNames[device]).join(", ") || "—";
  const license = artifact.license_spdx
    ?? (artifact.license_status === "license-review-required" ? "Review required" : "Not declared");
  const subtitle = [
    backends.length > 0 ? id : null,
    artifact.expected_size !== null ? formatBytes(artifact.expected_size) : null,
  ].filter(Boolean).join(" · ");
  const availabilityNotes = new Set(backends.map(({ reason }) =>
    unavailabilityMessages[reason ?? "deferred_gap"] ?? unavailabilityMessages.deferred_gap
  ));
  const progress = state.bytes_total && state.bytes_downloaded !== null
    ? Math.min(100, (state.bytes_downloaded / state.bytes_total) * 100) : null;
  const backendId = artifact.backend_ids[0];

  return (
    <article className="model-artifact" aria-label={`${id} model`}>
      <div className="model-artifact-heading">
        <h2>{backends.map((item) => item.display_name).join(" · ") || id}</h2>
        {subtitle && <p>{subtitle}</p>}
        <span className={`model-state model-state-${displayState}`}>
          {displayState === "not_required" ? "Included" : displayState.replace(/_/g, " ")}
        </span>
      </div>
      <dl className="model-details">
        <div><dt>Preferred device</dt><dd>{deviceLabel}</dd></div>
        <div><dt>License</dt><dd><a href={source} target="_blank" rel="noreferrer">{license}</a></dd></div>
      </dl>
      <div className="button-row model-actions">
        <a className="btn-secondary compact-button model-source-link" href={source} target="_blank" rel="noreferrer">Source</a>
        {downloading && mayCancel && (
          <button className="danger-button compact-button" onClick={() => void act(`cancel-${id}`, () => cancelModelDownload(id))} disabled={working !== null}>
            {working === `cancel-${id}` ? "Cancelling…" : "Cancel download"}
          </button>
        )}
        {!downloading && mayDownload && removable && backendId && !ready && !unavailable && (
          <button className="btn-primary compact-button" onClick={() => void act(`download-${backendId}`, () => prefetchModel(backendId))} disabled={working !== null}>
            {working === `download-${backendId}` ? "Starting…" : "Download"}
          </button>
        )}
        {mayDelete && ready && removable && (
          <button className="danger-button compact-button" onClick={() => void act(`remove-${id}`, () => removeModel(id))} disabled={working !== null}>
            {working === `remove-${id}` ? "Removing…" : "Remove"}
          </button>
        )}
      </div>
      {downloading && (
        <div className="model-progress">
          <progress value={progress ?? undefined} max="100" aria-label={`${id} download progress`} />
          <span>{formatBytes(state.bytes_downloaded)} of {formatBytes(state.bytes_total)}</span>
        </div>
      )}
      {state.error_detail && <p className="form-error model-note" role="alert">{state.error_detail}</p>}
      {unavailable && <p className="muted-copy model-note">{[...availabilityNotes].join(" ")}</p>}
    </article>
  );
}
