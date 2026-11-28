import { useCallback, useState } from "react";
import type { SavedReview } from "../api/api-review-types";
import { useMcfRegistry } from "../shared/mcf-registry";
import { ReviewFilterTimeFields } from "./review-filter-time-fields";
import { McfClassificationFields, McfRegistryStatus } from "./review-mcf";
import { CensoredAudioWord } from "./review-results";

/** Edit one review filter's classification and timing.
 * @param props Filter values, registry, and change callbacks.
 */
export function ReviewFilterEditor({
  filter,
  onChange,
  onDone,
  onInvalidTiming,
  resetTiming,
  severityLocked = false,
  cuesEditable,
  metadataEditable,
}: {
  filter: SavedReview["filters"][number];
  onChange: (patch: Partial<SavedReview["filters"][number]>) => void;
  onDone: () => void;
  onInvalidTiming?: (invalid: boolean) => void;
  resetTiming?: number;
  severityLocked?: boolean;
  cuesEditable: boolean;
  metadataEditable: boolean;
}) {
  const { registry, error: registryError } = useMcfRegistry();
  const [invalidTiming, setInvalidTiming] = useState(false);
  const onTimingChange = useCallback((invalid: boolean) => {
    setInvalidTiming(invalid);
    onInvalidTiming?.(invalid);
  }, [onInvalidTiming]);
  const description = filter.mcf_comment ?? filter.details ?? "";
  return <fieldset className="saved-review-filter">
    <legend>{filter.source === "audio" ? "Audio" : "Video"} filter</legend>
    <fieldset className="review-fieldset" disabled={!metadataEditable}>
    {!filter.category && <p className="mcf-needs-classification">Needs classification</p>}
    {registry ? <McfClassificationFields controlId={`Filter ${filter.id}`} registry={registry} value={filter} onChange={onChange} allowClear severityLocked={severityLocked} /> : <McfRegistryStatus error={registryError} />}
    {filter.source === "audio" && !metadataEditable ? <>
      <div className="field" aria-label="Audio filter label"><span>Label</span><CensoredAudioWord word={filter.label || ""} context={filter.details} /></div>
      <div className="field" aria-label="Audio filter description"><span>Description</span><CensoredAudioWord word={description} /></div>
    </> : <>
      <label className="field"><span>Label</span><input aria-label={`Filter ${filter.id} label`} value={filter.label || ""} onChange={(event) => onChange({ label: event.target.value || null })} /></label>
      <label className="field"><span>Description</span><textarea aria-label={`Filter ${filter.id} description`} value={description} onChange={(event) => onChange({ details: event.target.value || null, mcf_comment: null })} rows={2} /></label>
    </>}
    </fieldset>
    <fieldset className="review-fieldset" disabled={!cuesEditable}>
    <ReviewFilterTimeFields key={resetTiming} controlId={`Filter ${filter.id}`} value={filter}
      onInvalidChange={onTimingChange} onChange={onChange} />
    <label className="field"><span>EDL action</span><select aria-label={`Filter ${filter.id} action`} value={filter.edit_action} onChange={(event) => onChange({ edit_action: event.target.value as typeof filter.edit_action })}><option value="mute">Mute</option><option value="skip">Skip</option></select></label>
    </fieldset>
    <div className="button-row"><button type="button" className="btn-secondary compact-button" onClick={onDone} disabled={invalidTiming}>Done</button></div>
  </fieldset>;
}
