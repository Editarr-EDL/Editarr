import { useCallback, useEffect, useState } from "react";
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

/** Read diagnostic log settings and load an on-demand log tail. */
export function DiagnosticsPanel() {
  const mayView = useCan("logs.view");
  const maySeePaths = useCan("media.paths.view");
  const mayEdit = useCan("logs.settings.edit");
  const mayReadTail = mayView && maySeePaths;
  const [settings, setSettings] = useState<DiagnosticLogSettings | null>(null);
  const [channel, setChannel] = useState<DiagnosticLogChannel>("application");
  const [tail, setTail] = useState<DiagnosticLogTail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async (nextChannel = channel) => {
    setLoading(true);
    setError(null);
    try {
      const [nextSettings, nextTail] = await Promise.all([
        mayView ? fetchDiagnosticLogSettings() : Promise.resolve(null),
        mayReadTail ? fetchDiagnosticLogTail(nextChannel) : Promise.resolve(null),
      ]);
      setSettings(nextSettings);
      setTail(nextTail);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load diagnostics.");
    } finally {
      setLoading(false);
    }
  }, [channel, mayView, mayReadTail]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const changeChannel = (value: DiagnosticLogChannel) => {
    setChannel(value);
    void load(value);
  };
  const saveDebug = async (debugEnabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await saveDiagnosticLogSettings({ debug_enabled: debugEnabled }));
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save diagnostics settings.");
    } finally {
      setSaving(false);
    }
  };

  if (!mayView && !mayEdit) return null;
  return <section className="settings-card diagnostics-panel" aria-labelledby="diagnostics-heading">
    <div className="section-heading"><div><h2 id="diagnostics-heading">Diagnostics</h2></div><button className="btn-secondary compact-button" onClick={() => setOpen((value) => !value)}>{open ? "Close diagnostics" : mayReadTail ? "View logs" : "Logging settings"}</button></div>
    {open && <>
      {mayView && <div className="button-row"><button className="btn-secondary compact-button" onClick={() => void load()} disabled={loading || saving}>{loading ? "Refreshing…" : "Refresh logs"}</button></div>}
      {mayEdit && settings && <label className="security-choice"><input type="checkbox" checked={settings.debug_enabled} onChange={(event) => void saveDebug(event.target.checked)} disabled={loading || saving} /> Enable debug logging</label>}
      {!mayEdit && settings && <p>Debug logging {settings.debug_enabled ? "enabled" : "disabled"}.</p>}
      <p className="muted-copy">Debug logging applies immediately to the API. New scanner workers use it automatically; restart Editarr to update an already-running worker or controller.</p>
      {mayReadTail && <label className="field diagnostics-source"><span>Log source</span><select aria-label="Log source" value={channel} onChange={(event) => changeChannel(event.target.value as DiagnosticLogChannel)} disabled={loading || saving}><option value="application">Application</option><option value="http">HTTP API</option><option value="worker">Scanner worker</option><option value="controller">Service controller</option></select></label>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {tail?.truncated && <p className="muted-copy">Showing the most recent retained log lines.</p>}
      {mayReadTail && <pre className="diagnostics-log" tabIndex={0} aria-label={`${channel} log`}>{tail ? (tail.lines.join("\n") || "No log entries yet.") : "Loading logs…"}</pre>}
    </>}
  </section>;
}
