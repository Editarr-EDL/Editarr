import { useEffect, useState } from "react";
import { fetchDockSettings, saveDockSettings } from "../api/api-macos-settings";
import { useCan } from "../auth/auth-gate";

export function MacOSDockSettings() {
  const mayEdit = useCan("deployment.edit");
  const [showInDock, setShowInDock] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchDockSettings(controller.signal).then((settings) => {
      if (!controller.signal.aborted && typeof settings?.show_in_dock === "boolean") {
        setShowInDock(settings.show_in_dock);
      }
    }).catch(() => {});
    return () => controller.abort();
  }, []);

  async function save(visible: boolean) {
    setSaving(true);
    setError(null);
    try {
      setShowInDock((await saveDockSettings(visible)).show_in_dock);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save the Dock icon setting.");
    } finally {
      setSaving(false);
    }
  }

  if (showInDock === null) return null;
  return <section className="settings-card" aria-labelledby="macos-settings-heading">
    <div className="section-heading"><div><h2 id="macos-settings-heading">macOS</h2></div></div>
    <label className="deployment-choice">
      <input
        type="checkbox"
        checked={showInDock}
        disabled={!mayEdit || saving}
        onChange={(event) => void save(event.target.checked)}
      />
      Show app icon in the Dock
    </label>
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}
