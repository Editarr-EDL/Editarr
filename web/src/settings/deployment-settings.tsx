import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchDeploymentConfiguration, saveDeploymentConfiguration } from "../api/api-service";
import type {
  DeploymentConfiguration,
  DeploymentSnapshot,
  DeploymentUpdate,
} from "../api/api-system-types";
import { useCan } from "../auth/auth-gate";

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

function DeploymentSnapshotDetails({ title, snapshot }: { title: string; snapshot: DeploymentSnapshot }) {
  return <section className="deployment-snapshot" role="region" aria-label={title}>
    <h3>{title}</h3>
    <dl>
      <div><dt>Base URL</dt><dd>{snapshot.base_url}</dd></div>
      <div><dt>Public origin</dt><dd>{snapshot.public_origin}</dd></div>
      <div><dt>Base path</dt><dd>{snapshot.prefix}</dd></div>
      <div><dt>Listening Port</dt><dd>{snapshot.listening_port}</dd></div>
      <div><dt>Base URL source</dt><dd>{baseUrlSource(snapshot.base_url_source)}</dd></div>
      <div><dt>Port source</dt><dd>{portSource(snapshot.port_source)}</dd></div>
    </dl>
  </section>;
}

/**
 * Keep deployment edits local until saved for the next service start.
 * Restart and reconnection use the saved target separately.
 */
export function DeploymentSettings({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
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

  const savedSummary = useMemo(() => {
    if (!configuration) return null;
    const base = configuration.saved.base_url === null
      ? "Base URL has no saved override."
      : `Saved Base URL override: ${configuration.saved.base_url}`;
    const savedPort = configuration.saved.port === null
      ? "Listening Port has no saved override."
      : `Saved Listening Port override: ${configuration.saved.port}`;
    return `${base} ${savedPort}`;
  }, [configuration]);

  return <section className="settings-card deployment-settings" aria-labelledby="deployment-heading">
    <div className="section-heading"><div><h2 id="deployment-heading">Deployment</h2><p>Set Editarr’s public Base URL and its upstream listening port. Saved changes apply only when Editarr restarts.</p></div></div>
    {loading && <p className="loading-state">Loading deployment settings…</p>}
    {!loading && error && !configuration && <div className="load-error" role="alert"><span>{error}</span><button className="btn-secondary" type="button" onClick={() => void load()}>Retry</button></div>}
    {configuration && <>
      {mayEdit && <form className="deployment-form" onSubmit={(event) => void save(event)}>
        <label className="field"><span>Base URL</span><input name="deployment_base_url" type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://editarr.example.com/editarr" autoComplete="url" disabled={saving} /><small>Optional. Its path becomes Editarr’s Base path; leave it blank to clear a saved override and follow the environment seed or localhost default.</small></label>
        {configuration.saved.base_url === null && configuration.next_start.base_url_source === "environment" && <label className="deployment-choice"><input type="checkbox" checked={saveDisplayedBaseUrl} onChange={(event) => setSaveDisplayedBaseUrl(event.target.checked)} disabled={saving} /> Save the displayed Base URL as an override</label>}
        <label className="field"><span>Listening Port</span><input name="deployment_port" type="text" inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} placeholder="3348" disabled={saving} aria-describedby="deployment-port-help" /><small id="deployment-port-help">This is Editarr’s upstream port. It does not change the public port in the Base URL.</small></label>
        <div className="button-row"><button className="btn-primary" type="submit" disabled={saving || !canSave}>{saving ? "Saving…" : "Save deployment settings"}</button></div>
      </form>}
      <p className="deployment-saved-summary">{savedSummary}</p>
      {configuration.next_start.cli_port_override !== null && <div className="deployment-warning" role="status"><strong>CLI port override is active.</strong><span>--port {configuration.next_start.cli_port_override} masks the saved Listening Port until Editarr is started without that override.</span></div>}
      {configuration.restart_required && <div className="deployment-warning" role="status"><strong>Restart required.</strong><span>The active service keeps its current URL, routes, cookies, and listener until restart.</span></div>}
      <div className="deployment-snapshots">
        <DeploymentSnapshotDetails title="Active now" snapshot={configuration.active} />
        <DeploymentSnapshotDetails title="Next start" snapshot={configuration.next_start} />
      </div>
      <p className="muted-copy deployment-proxy-note">If this Base URL changes, update the external reverse proxy separately so it preserves the same path. Saving here never changes the current browser URL.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-status" role="status">{notice}</p>}
    </>}
  </section>;
}
