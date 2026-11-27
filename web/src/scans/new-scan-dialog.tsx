import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import type { JellyfinMediaVersion } from "../api/api-media-types";
import type { ScanModelCapability } from "../api/api-system-types";
import { AudioTrackField } from "./audio-track-field";
import { buildModelOptions, type ModelOption } from "./model-catalog";
import { ModelPicker } from "./model-picker";
import { NewScanAdvanced } from "./new-scan-advanced";
import { NewScanTypeCards, ScanTypeIcon } from "./new-scan-type-cards";
import { AudioLanguageHelp, QwenModelField } from "./qwen-model-field";
import { ScanCapabilityDisclosure } from "./scan-capability-disclosure";
import type { NewScanForm, NewScanType } from "./use-new-scan";

const focusableSelector = "input, select, button, summary";

/** Keyboard-reachable dialog controls in DOM order: skips disabled controls, unchecked radios, and collapsed details. */
function focusableControls(root: HTMLElement | null): HTMLElement[] {
  return Array.from(root?.querySelectorAll<HTMLElement>(focusableSelector) || []).filter((element) =>
    !element.matches(":disabled")
    && !(element instanceof HTMLInputElement && element.type === "radio" && !element.checked)
    && !element.closest("details:not([open]) > :not(summary), [hidden]"));
}

/** The option chosen by default before the user picks: `preferred` when offered, otherwise the first model. */
function preferredDefault(models: ScanModelCapability[] | undefined, preferred: string): string | undefined {
  return models?.some((model) => model.id === preferred) ? preferred : models?.[0]?.id;
}

/** Short model name for the footer summary; adds the variant only when another option shares the name. */
function optionName(options: ModelOption[], id: string): string {
  const option = options.find((candidate) => candidate.id === id);
  if (!option) return id || "Default model";
  const shared = options.some((candidate) => candidate.id !== id && candidate.name === option.name);
  return shared && option.variant ? `${option.name} ${option.variant}` : option.name;
}

function Section({ type, title, note, children }: { type: NewScanType; title: string; note?: string; children: ReactNode }) {
  return <section className="new-scan-section" aria-labelledby={`new-scan-section-${type}`}>
    <div className="new-scan-section-head">
      <span className="new-scan-icon" aria-hidden="true"><ScanTypeIcon type={type} /></span>
      <h3 id={`new-scan-section-${type}`}>{title}</h3>{note && <p>{note}</p>}
    </div>
    {children}
  </section>;
}

/**
 * The New Scan dialog for one catalog item. `form` comes from `useNewScan`; the caller owns the selected
 * version (`mediaSourceId`) and mounts this only while the form is open.
 */
export function NewScanDialog({ form, title, meta, versions, mediaSourceId, onVersionChange }: {
  form: NewScanForm;
  title: string;
  meta: string;
  versions: JellyfinMediaVersion[];
  mediaSourceId: string | null;
  onVersionChange: (sourceId: string | null) => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const scanNameRef = useRef<HTMLInputElement>(null);
  const { capabilities, scanType, scanAudio, scanVideo, adding } = form;
  const transcribers = buildModelOptions("transcriber", capabilities, { defaultId: preferredDefault(capabilities?.audio_models, "whisper") });
  const filters = buildModelOptions("filter", capabilities, { defaultId: preferredDefault(capabilities?.audio_filters, "better_profanity") });
  const videoModels = buildModelOptions("video", capabilities);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => focusableControls(dialogRef.current)
      .find((element) => !element.classList.contains("new-scan-close"))?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const trapFocus = (event: KeyboardEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    if (event.key === "Escape" && !adding) {
      event.preventDefault();
      form.close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableControls(dialogRef.current);
    if (!focusable.length) return;
    const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const nextIndex = event.shiftKey
      ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
      : (currentIndex === focusable.length - 1 ? 0 : currentIndex + 1);
    event.preventDefault();
    focusable[nextIndex].focus();
  };

  const audioSummary = <>{optionName(transcribers, form.audioModel)}<b aria-hidden="true"> → </b><span className="visually-hidden"> then </span>{optionName(filters, form.audioFilter)}</>;
  const separator = <b aria-hidden="true"> · </b>;
  const summary = scanType === "credits" ? <>Credits detection{separator}chapters + visual</>
    : scanType === "audio" ? audioSummary
      : scanType === "video" ? optionName(videoModels, form.videoModel)
        : <>{audioSummary}{separator}{optionName(videoModels, form.videoModel)}</>;

  return <div className="modal-backdrop new-scan-backdrop" role="presentation">
    <section className="new-scan-dialog" ref={dialogRef} onKeyDown={trapFocus} role="dialog" aria-modal="true"
      aria-labelledby="new-scan-heading" aria-describedby="new-scan-item">
      <header className="new-scan-header">
        <div className="new-scan-title">
          <h2 id="new-scan-heading" className="eyebrow">New Scan</h2>
          <p id="new-scan-item" className="new-scan-item">{title}</p>
          {meta && <p className="new-scan-meta">{meta}</p>}
        </div>
        {versions.length > 0 && <label className="new-scan-version"><span>Version</span>
          <select aria-label="Version" disabled={form.setupLocked} value={mediaSourceId || ""}
            onChange={(event) => { form.clearError(); onVersionChange(event.target.value || null); }}>
            {versions.map((version, index) => <option key={version.id || `${version.name || "version"}-${index}`} value={version.id || ""} disabled={!version.id}>
              {version.name || `Version ${index + 1}`}{version.resolution ? ` · ${version.resolution}` : ""}{version.media_format ? ` · ${version.media_format}` : ""}
            </option>)}
          </select>
        </label>}
        <button type="button" className="new-scan-close" aria-label="Close" onClick={form.close} disabled={adding}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </header>

      <div className="new-scan-body">
        <NewScanTypeCards value={scanType} disabled={form.setupLocked} onChange={form.setScanType} />

        {scanAudio && <Section type="audio" title="Audio" note="Dialogue & language">
          <div className="new-scan-track">
            <AudioTrackField {...form.audioTracks} disabled={adding} showSelectedLabel={false} />
            <p className="new-scan-hint">Tracks are read from the selected version.{" "}
              <button type="button" className="new-scan-link" disabled={adding || form.audioTracks.loading}
                onClick={form.refreshAudioTracks}>Reload tracks</button></p>
          </div>
          <div className="new-scan-two-col">
            <ModelPicker label="Transcriber" options={transcribers} value={form.audioModel} onChange={form.setAudioModel} />
            <ModelPicker label="Filter" options={filters} value={form.audioFilter} onChange={form.setAudioFilter}
              info={<AudioLanguageHelp />} />
          </div>
          <ScanCapabilityDisclosure capabilities={capabilities} backendId={form.audioModel} scanType="audio" includeScanName={false} />
        </Section>}

        {scanVideo && <Section type="video" title="Video" note="Scenes & visual content">
          <ModelPicker label="Video model" options={videoModels} value={form.videoModel} onChange={form.setVideoModel} />
          <ScanCapabilityDisclosure capabilities={capabilities} backendId={form.videoModel} scanType="video" includeScanName={false} />
          <NewScanAdvanced video={form.video} />
        </Section>}

        {scanType === "credits" ? <Section type="credits" title="Credits" note="No model download needed">
          <p className="new-scan-credits">Detect the start of closing credits using local chapter and visual analysis.</p>
        </Section> : <section className="new-scan-section" aria-labelledby="new-scan-section-details">
          <div className="new-scan-section-head">
            <span className="new-scan-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M4 6h16M4 12h10M4 18h7" /></svg></span>
            <h3 id="new-scan-section-details">Details</h3>
          </div>
          <div className="new-scan-two-col">
            <QwenModelField capabilities={capabilities} value={form.qwenModel} onChange={form.setQwenModel} />
            <label className="new-scan-field"><span>Scan name <em>(optional)</em></span>
              <input ref={scanNameRef} aria-label="Scan name (optional)" defaultValue="" placeholder="e.g. Director’s cut check" />
            </label>
          </div>
        </section>}
      </div>

      {form.submitError && <p className="form-error new-scan-error" role="alert">{form.submitError.message}</p>}
      <footer className="new-scan-footer">
        <div className="new-scan-summary" aria-live="polite"><small>Will run</small><span>{summary}</span></div>
        <div className="button-row">
          <button type="button" className="btn-secondary" onClick={form.close} disabled={adding}>Cancel</button>
          {form.needsAudioTrackRefresh && <button type="button" className="btn-secondary" onClick={form.refreshAudioTracks}>Refresh audio tracks</button>}
          <button type="button" className="btn-primary" onClick={() => void form.submit(scanNameRef.current?.value || "")}
            disabled={!form.canSubmit}>{adding ? "Adding…" : "Add to Queue"}</button>
        </div>
      </footer>
    </section>
  </div>;
}
