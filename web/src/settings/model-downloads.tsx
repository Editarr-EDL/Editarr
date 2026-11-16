import { useEffect, useState } from "react";
import { fetchCapabilities, prefetchModel } from "../api/api-service";
import type { ScanModelCapability } from "../api/api-system-types";
import { useCan } from "../auth/auth-gate";

type Catalog = { state: "loading" } | { state: "unavailable"; message: string } | { state: "ready"; models: ScanModelCapability[] };

/** Acquire weights without reading installed artifacts or global download progress. */
export function ModelDownloads() {
  const mayDownload = useCan("models.download");
  const mayReadCatalog = useCan("scans.start");
  const [catalog, setCatalog] = useState<Catalog>({ state: "loading" });
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!mayDownload || !mayReadCatalog) return;
    let disposed = false;
    setCatalog({ state: "loading" });
    setSelected("");
    void fetchCapabilities().then((catalog) => {
      if (disposed) return;
      const choices = catalog.model_capabilities?.filter((model) => model.weight_requirement === "on_demand" && model.availability === "available")
        ?? [...catalog.audio_models, ...catalog.video_models, ...catalog.qwen_models];
      setCatalog({ state: "ready", models: [...new Map(choices.map((model) => [model.id, { id: model.id, display_name: model.display_name }])).values()] });
    }).catch((error) => { if (!disposed) setCatalog({ state: "unavailable", message: error instanceof Error ? error.message : "Unable to load models." }); });
    return () => { disposed = true; };
  }, [mayDownload, mayReadCatalog]);

  if (!mayDownload) return null;
  async function download() {
    if (!selected.trim() || (mayReadCatalog && (catalog.state !== "ready" || !catalog.models.some(model => model.id === selected)))) return;
    setBusy(true); setMessage(null);
    try {
      const result = await prefetchModel(selected);
      setMessage(result.accepted ? "Model download requested." : "Model is already available or downloading.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Unable to download model."); }
    finally { setBusy(false); }
  }

  return <section className="settings-card" aria-labelledby="model-downloads-heading">
    <h2 id="model-downloads-heading">Download models</h2>
    {mayReadCatalog && catalog.state === "unavailable" ? <p role="status">Model catalog unavailable. {catalog.message}</p>
      : mayReadCatalog && catalog.state === "loading" ? <p role="status">Loading model catalog...</p>
      : <>
    <label className="field"><span>Model</span>{mayReadCatalog
      ? <select aria-label="Model" value={selected} onChange={(event) => setSelected(event.target.value)}><option value="">Select a model</option>{catalog.state === "ready" && catalog.models.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select>
      : <input value={selected} onChange={(event) => setSelected(event.target.value)} autoComplete="off" />}</label>
    <button className="btn-primary" disabled={busy || !selected.trim()} onClick={() => void download()}>{busy ? "Requesting..." : "Download"}</button>
    </>}
    {message && <p role="status" className="form-status">{message}</p>}
  </section>;
}