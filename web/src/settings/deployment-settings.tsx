import { useCallback, useEffect, useState } from "react";

import { fetchDeploymentConfiguration, saveDeploymentConfiguration } from "../api/api-service";
import type {
  DeploymentConfiguration,
  DeploymentSnapshot,
  DeploymentUpdate,
} from "../api/api-system-types";
import { useCan } from "../auth/auth-gate";
import { GeneralSettingsSection } from "./general-settings-section";
import { MacOSDockSettings } from "./macos-dock-settings";

function baseUrlDraft(configuration: DeploymentConfiguration): string {
  return configuration.saved.base_url
    ?? (configuration.next_start.base_url_source === "local_default" ? "" : configuration.next_start.base_url);
}

function portDraft(configuration: DeploymentConfiguration): string {
  return String(configuration.saved.port ?? configuration.next_start.listening_port);
}

function baseUrlSource(source: DeploymentSnapshot["base_url_source"]): string {
  if (source === "saved") return "Saved override";
  if (source === "environment") return "Environment seed";
  return "Localhost default";
}

function portSource(source: DeploymentSnapshot["port_source"]): string {
  if (source === "cli") return "CLI override";
  if (source === "saved") return "Saved override";
  return "Default";
}

function DeploymentSnapshotSummary({ title, snapshot, active = false }: {
  title: string;
  snapshot: DeploymentSnapshot;
  active?: boolean;
}) {
  return <section className="deployment-snapshot" role="region" aria-label={title}>
    <h3><span className={active ? "deployment-dot active" : "deployment-dot"} aria-hidden="true" />{title}</h3>
    <p className="deployment-snapshot-url">{snapshot.base_url}</p>
    <p className="general-settings-note">Port {snapshot.listening_port}</p>
  </section>;
}

function DeploymentConnectionDetails({ title, snapshot }: { title: string; snapshot: DeploymentSnapshot }) {
  return <section className="deployment-connection-details" role="region" aria-label={title}>
    <h3>{title}</h3>
    <dl className="general-settings-facts">
      <div><dt>Public origin</dt><dd>{snapshot.public_origin}</dd></div>
      <div><dt>Base path</dt><dd>{snapshot.prefix || "/"}</dd></div>
      <div><dt>Base URL source</dt><dd>{baseUrlSource(snapshot.base_url_source)}</dd></div>
      <div><dt>Port source</dt><dd>{portSource(snapshot.port_source)}</dd></div>
    </dl>
  </section>;
}

/**
 * Keep deployment edits local until saved for the next service start.
 * Restart and reconnection use the saved target separately.
 */
export function DeploymentSettings({ onDirtyChange, initiallyOpen }: {
  onDirtyChange?: (dirty: boolean) => void;
  initiallyOpen?: boolean;
}) {
  const mayEdit = useCan("deployment.edit");
  const [configuration, setConfiguration] = useState<DeploymentConfiguration | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [port, setPort] = useState("");
  const [saveDisplayedBaseUrl, setSaveDisplayedBaseUrl] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const acceptConfiguration = useCallback((next: DeploymentConfiguration) => {
    setConfiguration(next);
    setBaseUrl(baseUrlDraft(next));
    setPort(portDraft(next));
    setSaveDisplayedBaseUrl(false);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      acceptConfiguration(await fetchDeploymentConfiguration());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load deployment settings.");
    } finally {
      setLoading(false);
    }
  }, [acceptConfiguration]);

  useEffect(() => { void load(); }, [load]);

  const baseUrlDirty = configuration !== null && baseUrl !== baseUrlDraft(configuration);
  const portDirty = configuration !== null && port !== portDraft(configuration);
  const canSave = mayEdit && Boolean(baseUrlDirty || portDirty || saveDisplayedBaseUrl);

  useEffect(() => {
    onDirtyChange?.(canSave);
  }, [canSave, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!configuration || saving || !canSave) return;

    const update: DeploymentUpdate = {};
    if (baseUrlDirty || saveDisplayedBaseUrl) update.base_url = baseUrl.trim() || null;
    if (portDirty) {
      if (!port.trim()) {
        update.port = null;
      } else if (!/^\d+$/.test(port.trim()) || Number(port) < 1 || Number(port) > 65535) {
        setError("Listening Port must be a whole number from 1 through 65535.");
        return;
      } else {
        update.port = Number(port);
      }
    }

    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const next = await saveDeploymentConfiguration(update);
      acceptConfiguration(next);
      setNotice(next.restart_required
        ? "Deployment settings saved. Restart Editarr to apply the pending values."
        : "Deployment settings saved. The active service already matches these values.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save deployment settings.");
    } finally {
      setSaving(false);
    }
  };

  const activePort = configuration ? `Port ${configuration.active.listening_port}` : null;
  const summary = configuration?.restart_required ? `${activePort} · Restart required` : activePort;

  return <>
  <GeneralSettingsSection id="deployment" title="Deployment" value={summary ?? (loading ? "Loading…" : "Unavailable")}
    attention={configuration?.restart_required} initiallyOpen={initiallyOpen}>
    {loading && <p className="loading-state">Loading deployment settings…</p>}
    {!loading && error && !configuration && <div className="load-error" role="alert"><span>{error}</span><button className="btn-secondary" type="button" onClick={() => void load()}>Retry</button></div>}
    {configuration && <>
      {mayEdit && <form className="deployment-form" onSubmit={(event) => void save(event)}>
        <label className="field"><span>Base URL</span><input name="deployment_base_url" type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder={configuration.next_start.base_url} autoComplete="url" disabled={saving} /><small>Leave blank to use the environment or localhost default.</small></label>
        <label className="field"><span>Listening Port</span><input name="deployment_port" type="text" inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} placeholder={String(configuration.next_start.listening_port)} disabled={saving} /><small>Upstream port used by Editarr.</small></label>
        {configuration.saved.base_url === null && configuration.next_start.base_url_source === "environment" && <label className="deployment-choice"><input type="checkbox" checked={saveDisplayedBaseUrl} onChange={(event) => setSaveDisplayedBaseUrl(event.target.checked)} disabled={saving} /> Save the displayed Base URL as an override</label>}
        <div className="general-settings-actions deployment-actions">
          <button className="btn-primary compact-button" type="submit" disabled={saving || !canSave}>{saving ? "Saving…" : "Save deployment settings"}</button>
          <p className="general-settings-note">Saved changes take effect after restart.</p>
        </div>
      </form>}
      {configuration.next_start.cli_port_override !== null && <div className="deployment-warning" role="status"><strong>CLI port override is active.</strong><span>--port {configuration.next_start.cli_port_override} masks the saved Listening Port until Editarr is started without that override.</span></div>}
      {configuration.restart_required && <p className="deployment-restart-required" role="status">Restart required to apply the next-start settings.</p>}
      <div className="deployment-snapshots">
        <DeploymentSnapshotSummary title="Active now" snapshot={configuration.active} active />
        <DeploymentSnapshotSummary title="Next start" snapshot={configuration.next_start} />
      </div>
      <details className="general-settings-more">
        <summary>Connection details</summary>
        <div className="general-settings-more-content">
          <p className="general-settings-note">
            {configuration.saved.base_url === null ? "Base URL has no saved override." : `Saved Base URL override: ${configuration.saved.base_url}`}
            {" "}
            {configuration.saved.port === null ? "Listening Port has no saved override." : `Saved Listening Port override: ${configuration.saved.port}`}
          </p>
          <div className="deployment-connection-columns">
            <DeploymentConnectionDetails title="Active connection" snapshot={configuration.active} />
            <DeploymentConnectionDetails title="Next connection" snapshot={configuration.next_start} />
          </div>
          <p className="general-settings-note">The Base URL path sets Editarr’s Base path. If it changes, update the reverse proxy to use the same path.</p>
        </div>
      </details>
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-status" role="status">{notice}</p>}
    </>}
  </GeneralSettingsSection>
  {configuration?.macos_dock_supported && <MacOSDockSettings />}
  </>;
}
