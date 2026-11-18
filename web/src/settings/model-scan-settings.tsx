import { useEffect, useState, type FormEvent } from "react";
import { fetchModelScanSettings, saveModelScanSettings } from "../api/api-model-scan-settings";
import { useCan } from "../auth/auth-gate";

// Shift the decimal exponent so values such as 1.001 seconds display as 1001 ms.
function shiftDecimal(value: number, places: number): number {
  const [coefficient, exponent = "0"] = String(value).split("e");
  return Number(`${coefficient}e${Number(exponent) + places}`);
}

export function ModelScanSettings() {
  const mayView = useCan("models.inventory.view");
  return mayView ? <ScanSettingsCard /> : null;
}

function ScanSettingsCard() {
  const mayEdit = useCan("deployment.edit");
  const [savedMilliseconds, setSavedMilliseconds] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const milliseconds = draft.trim() === "" ? NaN : Number(draft);
  const canSave = mayEdit && savedMilliseconds !== null && Number.isFinite(milliseconds) &&
    milliseconds >= 0 && milliseconds !== savedMilliseconds && !saving;

  function loadPadding(seconds: number) {
    const milliseconds = shiftDecimal(seconds, 3);
    setSavedMilliseconds(milliseconds);
    setDraft(String(milliseconds));
  }

  useEffect(() => {
    const controller = new AbortController();
    void fetchModelScanSettings(controller.signal).then((settings) => {
      if (controller.signal.aborted) return;
      loadPadding(settings.audio_padding_seconds);
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Unable to load scan settings.");
      }
    });
    return () => controller.abort();
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const settings = await saveModelScanSettings(shiftDecimal(milliseconds, -3));
      loadPadding(settings.audio_padding_seconds);
      setMessage("Scan settings saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save scan settings.");
    } finally {
      setSaving(false);
    }
  }

  return <section className="settings-card" aria-labelledby="model-scan-settings-heading">
    <div className="section-heading"><h2 id="model-scan-settings-heading">Scan Settings</h2></div>
    {savedMilliseconds === null
      ? (!error && <p role="status">Loading scan settings…</p>)
      : <form className="model-scan-settings-form" onSubmit={(event) => void save(event)}>
        <label className="field">
          <span>Audio padding (milliseconds)</span>
          <input
            type="number"
            min={0}
            step="any"
            required
            value={draft}
            readOnly={!mayEdit}
            disabled={saving}
            aria-describedby="audio-padding-description"
            onChange={(event) => { setDraft(event.target.value); setMessage(null); }}
          />
        </label>
        <p className="muted-copy" id="audio-padding-description">Time added before and after each finding in a new audio scan.</p>
        {mayEdit && <div className="button-row">
          <button type="submit" className="btn-primary" disabled={!canSave} aria-label="Save scan settings">
            {saving ? "Saving…" : "Save"}
          </button>
        </div>}
      </form>}
    {error && <p role="alert" className="form-error">{error}</p>}
    {message && <p role="status" className="form-status">{message}</p>}
  </section>;
}
