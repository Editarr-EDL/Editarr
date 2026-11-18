import { useCallback, useEffect, useId, useState } from "react";
import {
  fetchDiagnosticLogSettings,
  fetchDiagnosticLogTail,
  saveDiagnosticLogSettings,
} from "../api/api-service";
import type {
  DiagnosticLogChannel,
  DiagnosticLogSettings,
  DiagnosticLogTail,
} from "../api/api-system-types";
import { useCan } from "../auth/auth-gate";
import { GeneralSettingsSection } from "./general-settings-section";

/** Read diagnostic log settings and load an on-demand log tail. */
export function DiagnosticsPanel({ initiallyOpen }: { initiallyOpen?: boolean }) {
  const mayView = useCan("logs.view");
  const maySeePaths = useCan("media.paths.view");
  const mayEdit = useCan("logs.settings.edit");
  const mayReadTail = mayView && maySeePaths;
  const optionsId = useId();
  const [settings, setSettings] = useState<DiagnosticLogSettings | null>(null);
  const [channel, setChannel] = useState<DiagnosticLogChannel>("application");
  const [tail, setTail] = useState<DiagnosticLogTail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadSettings = useCallback(async () => {
    if (!mayView) return;
    setLoadingSettings(true);
    setSettingsError(null);
    try {
      setSettings(await fetchDiagnosticLogSettings());
    } catch (reason) {
      setSettingsError(reason instanceof Error ? reason.message : "Unable to load diagnostics settings.");
    } finally {
      setLoadingSettings(false);
    }
  }, [mayView]);
  useEffect(() => { void loadSettings(); }, [loadSettings]);

  const loadTail = useCallback(async () => {
    if (!mayReadTail) return;
    setLoading(true);
    setError(null);
    try {
      setTail(await fetchDiagnosticLogTail(channel));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load diagnostics.");
    } finally {
      setLoading(false);
    }
  }, [channel, mayReadTail]);

  useEffect(() => {
    if (open) void loadTail();
  }, [open, loadTail]);

  const refresh = () => {
    void loadSettings();
    void loadTail();
  };
  const saveDebug = async (debugEnabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await saveDiagnosticLogSettings({ debug_enabled: debugEnabled }));
      await loadTail();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save diagnostics settings.");
    } finally {
      setSaving(false);
    }
  };

  if (!mayView && !mayEdit) return null;
  const summary = mayView && (settings
    ? `Debug logging ${settings.debug_enabled ? "on" : "off"}`
    : settingsError ? "Logging settings unavailable" : "Loading…");
  const busy = loading || loadingSettings || saving;
  const currentTail = tail?.channel === channel ? tail : null;
  return <GeneralSettingsSection id="diagnostics" title="Diagnostics" value={summary} initiallyOpen={initiallyOpen}>
    <div className="general-settings-actions">
      <button className="btn-secondary compact-button" type="button" onClick={() => setOpen((value) => !value)}
        aria-expanded={open} aria-controls={optionsId}>
        {mayReadTail ? (open ? "Hide logs" : "View logs") : (open ? "Hide logging settings" : "Logging settings")}
      </button>
    </div>
    <div id={optionsId} className="general-settings-inline diagnostics-options" hidden={!open}>
      {open && <>
        {mayView && mayEdit && settings && <label className="general-setting-toggle">
          <span>Enable debug logging</span>
          <input type="checkbox" checked={settings.debug_enabled}
            onChange={(event) => void saveDebug(event.target.checked)} disabled={busy} />
        </label>}
        {mayView && !mayEdit && settings && <p>Debug logging {settings.debug_enabled ? "enabled" : "disabled"}.</p>}
        <p className="muted-copy general-settings-note">Debug logging applies immediately to the API. New scanner workers use it automatically; restart Editarr to update an already-running worker or controller.</p>
        {mayView && <div className="general-settings-actions diagnostics-toolbar">
          {mayReadTail && <label className="field diagnostics-source">
            <span>Log source</span>
            <select value={channel} onChange={(event) => setChannel(event.target.value as DiagnosticLogChannel)} disabled={busy}>
              <option value="application">Application</option>
              <option value="http">HTTP API</option>
              <option value="worker">Scanner worker</option>
              <option value="controller">Service controller</option>
            </select>
          </label>}
          <button className="btn-secondary compact-button" type="button" onClick={refresh} disabled={busy}>
            {loading || loadingSettings ? "Refreshing…" : "Refresh logs"}
          </button>
        </div>}
        {settingsError && <p className="form-error" role="alert">{settingsError}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        {currentTail?.truncated && <p className="muted-copy general-settings-note">Showing the most recent retained log lines.</p>}
        {mayReadTail && !(!currentTail && error) && <pre className="diagnostics-log" tabIndex={0} aria-label={`${channel} log`}>
          {currentTail ? (currentTail.lines.join("\n") || "No log entries yet.") : "Loading logs…"}
        </pre>}
      </>}
    </div>
  </GeneralSettingsSection>;
}
