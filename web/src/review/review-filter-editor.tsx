import type { SavedReview } from "../api/api-review-types";
import { useMcfRegistry } from "../shared/mcf-registry";
import { McfClassificationFields, McfRegistryStatus } from "./review-mcf";
import { CensoredAudioWord } from "./review-results";

/** Edit one review filter's classification and timing.
 * @param props Filter values, registry, and change callbacks.
 */
export function ReviewFilterEditor({
  filter,
  onChange,
  onDone,
  required = false,
  severityLocked = false,
  filtersEditable,
  cuesEditable,
  metadataEditable,
}: {
  filter: SavedReview["filters"][number];
  onChange: (patch: Partial<SavedReview["filters"][number]>) => void;
  onDone: () => void;
  required?: boolean;
  severityLocked?: boolean;
  filtersEditable: boolean;
  cuesEditable: boolean;
  metadataEditable: boolean;
}) {
  const { registry, error: registryError } = useMcfRegistry();
  const description = filter.mcf_comment ?? filter.details ?? "";
  return <fieldset className="saved-review-filter">
    <legend>{filter.source === "audio" ? "Audio" : "Video"} filter</legend>
    <label><input type="checkbox" checked={filter.selected} disabled={required || !filtersEditable} onChange={(event) => onChange({ selected: event.target.checked })} /> Selected{required ? " - required by instance policy" : ""}</label>
    <fieldset className="review-fieldset" disabled={!metadataEditable}>
    {!filter.category && <p className="mcf-needs-classification">Needs classification</p>}
    {registry ? <McfClassificationFields controlId={`Filter ${filter.id}`} registry={registry} value={filter} onChange={onChange} allowClear severityLocked={severityLocked} /> : <McfRegistryStatus error={registryError} />}
    {filter.source === "audio" && !metadataEditable ? <>
      <div className="field" aria-label="Audio filter label"><span>Label</span><CensoredAudioWord word={filter.label || ""} context={filter.details} /></div>
      <div className="field" aria-label="Audio filter description"><span>Description</span><CensoredAudioWord word={description} /></div>
      <div className="field" aria-label="Audio filter group"><span>Group</span><CensoredAudioWord word={filter.group_key || ""} /></div>
    </> : <>
      <label className="field"><span>Label</span><input aria-label={`Filter ${filter.id} label`} value={filter.label || ""} onChange={(event) => onChange({ label: event.target.value || null })} /></label>
      <label className="field"><span>Description</span><textarea aria-label={`Filter ${filter.id} description`} value={description} onChange={(event) => onChange({ details: event.target.value || null, mcf_comment: null })} rows={2} /></label>
      <label className="field"><span>Group</span><input aria-label={`Filter ${filter.id} group`} value={filter.group_key || ""} onChange={(event) => onChange({ group_key: event.target.value || null })} /></label>
    </>}
    </fieldset>
    <fieldset className="review-fieldset" disabled={!cuesEditable}>
    <div className="finding-timing-fields"><label className="field"><span>Start seconds</span><input aria-label={`Filter ${filter.id} start seconds`} type="number" min="0" step="0.1" value={filter.start_seconds} onChange={(event) => onChange({ start_seconds: Number(event.target.value) })} /></label><label className="field"><span>End seconds</span><input aria-label={`Filter ${filter.id} end seconds`} type="number" min="0" step="0.1" value={filter.end_seconds ?? ""} onChange={(event) => onChange({ end_seconds: event.target.value === "" ? null : Number(event.target.value) })} /></label></div>
    <label className="field"><span>EDL action</span><select aria-label={`Filter ${filter.id} action`} value={filter.edit_action} onChange={(event) => onChange({ edit_action: event.target.value as typeof filter.edit_action })}><option value="mute">Mute</option><option value="skip">Skip</option></select></label>
    </fieldset>
    <div className="button-row"><button type="button" className="btn-secondary compact-button" onClick={onDone}>Done</button></div>
  </fieldset>;
}
