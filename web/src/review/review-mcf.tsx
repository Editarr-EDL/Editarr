import { useEffect, useState } from "react";

import type {
  McfCategory,
  McfCategoryFamily,
  McfRegistry,
  Severity,
} from "../api/api-mcf-types";
import type { Scan } from "../api/api-scan-types";
import type {
  McfMetadataSeedCandidate,
  McfMetadataSeedRequest,
  ReviewFilter,
  ReviewMcfDocument,
} from "../api/api-review-types";

export function familyLabel(registry: McfRegistry | null, family: McfCategoryFamily | null): string {
  return registry?.families.find((item) => item.token === family)?.label || family || "Unclassified";
}

export function categoryLabel(registry: McfRegistry | null, token: McfCategory | null): string {
  for (const family of registry?.families || []) {
    const category = family.categories.find((item) => item.token === token);
    if (category) return category.label;
  }
  return token || "Needs classification";
}

export function classificationIsValid(
  registry: McfRegistry | null,
  category: McfCategoryFamily | null,
  subcategory: McfCategory | null,
): boolean {
  if (!category || !subcategory) return false;
  return Boolean(registry?.families.find((family) => (
    family.token === category && family.categories.some((item) => item.token === subcategory)
  )));
}

interface ClassificationValue {
  category: McfCategoryFamily | null;
  subcategory: McfCategory | null;
  severity: Severity | null;
}

/** Edit an MCF family and subcategory using the server registry.
 * @param props Control identity, registry, selection, and change callback.
 */
export function McfClassificationFields({
  controlId,
  registry,
  value,
  onChange,
  allowClear = false,
  severityLocked = false,
}: {
  controlId: string;
  registry: McfRegistry;
  value: ClassificationValue;
  onChange: (patch: Partial<ClassificationValue>) => void;
  allowClear?: boolean;
  severityLocked?: boolean;
}) {
  const family = registry.families.find((item) => item.token === value.category);
  const subcategoryValid = classificationIsValid(registry, value.category, value.subcategory);
  const listId = `${controlId}-subcategories`;
  return <div className="mcf-classification-fields">
    <label className="field"><span>Family</span><select aria-label={`${controlId} family`} value={value.category || ""} onChange={(event) => { const next = (event.target.value || null) as McfCategoryFamily | null; onChange({ category: next, subcategory: null }); }}><option value="">Unclassified</option>{registry.families.map((item) => <option key={item.token} value={item.token}>{item.label}</option>)}</select></label>
    <label className="field"><span>Subcategory</span><input type="search" list={listId} aria-label={`${controlId} subcategory`} value={value.subcategory || ""} disabled={!family} aria-invalid={Boolean(value.subcategory && !subcategoryValid)} onChange={(event) => onChange({ subcategory: event.target.value || null })} /><datalist id={listId}>{family?.categories.map((item) => <option key={item.token} value={item.token}>{item.label}</option>)}</datalist>{value.subcategory && !subcategoryValid && <small className="form-error">Choose a subcategory from {family?.label || "the selected family"}.</small>}</label>
    <fieldset className="mcf-severity-control" disabled={severityLocked}><legend>Severity{severityLocked ? " - locked by instance policy" : ""}</legend>{(["low", "medium", "high"] as const).map((severity) => <label key={severity}><input type="radio" name={`${controlId}-severity`} value={severity} checked={value.severity === severity} onChange={() => onChange({ severity })} /> {severity[0].toUpperCase() + severity.slice(1)}</label>)}</fieldset>
    {allowClear && <button type="button" className="btn-secondary compact-button mcf-clear-classification" onClick={() => onChange({ category: null, subcategory: null })}>Clear classification</button>}
  </div>;
}

/** Show MCF registry loading progress, or the load failure instead of waiting indefinitely. */
export function McfRegistryStatus({ error }: { error: string | null }) {
  return error ? <p className="form-error" role="alert">{error}</p> : <p className="loading-state">Loading MCF categories…</p>;
}

export function McfFilterSummary({ filter, registry }: { filter: ReviewFilter; registry: McfRegistry | null }) {
  if (!filter.category || !filter.subcategory) return <span className="mcf-needs-classification">Needs classification</span>;
  return <span className="mcf-filter-summary">
    <span>{familyLabel(registry, filter.category)} / {categoryLabel(registry, filter.subcategory)}</span>
    <code>{filter.subcategory}</code>
    <span>{filter.severity ? filter.severity[0].toUpperCase() + filter.severity.slice(1) : "No severity"}</span>
    {filter.mcf_comment && <span className="mcf-comment-summary">{filter.mcf_comment}</span>}
  </span>;
}

/** Return field errors for invalid MCF document metadata.
 * @param document Metadata to validate before saving or export.
 */
export function validateMcfDocument(document: ReviewMcfDocument): Record<string, string> {
  const errors: Record<string, string> = {};
  const httpUrl = (value: string | null) => {
    if (!value) return true;
    try { const parsed = new URL(value); return parsed.protocol === "http:" || parsed.protocol === "https:"; }
    catch { return false; }
  };
  if (!httpUrl(document.source_url)) errors.source_url = "Use an absolute HTTP(S) URL.";
  if (!httpUrl(document.imdb_url) || (document.imdb_url && !new URL(document.imdb_url).hostname.endsWith("imdb.com"))) errors.imdb_url = "Use an IMDb HTTP(S) URL.";
  for (const field of ["title", "release", "comment"] as const) {
    const value = document[field];
    if (value !== null && (!value.trim() || value.length > 4096 || /[\r\n]/.test(value))) errors[field] = "Use non-empty single-line text up to 4,096 characters.";
  }
  if (document.year !== null && document.year < 0) errors.year = "Year must be zero or greater.";
  if (document.season !== null && document.season <= 0) errors.season = "Season must be positive.";
  if (document.episode !== null && document.episode <= 0) errors.episode = "Episode must be positive.";
  if (document.material_start_seconds !== null && (!Number.isFinite(document.material_start_seconds) || document.material_start_seconds < 0)) errors.material_start_seconds = "Use HH:MM:SS.MMM at or after 00:00:00.000.";
  if (document.material_end_seconds !== null && (!Number.isFinite(document.material_end_seconds) || document.material_end_seconds <= 0)) errors.material_end_seconds = "Use HH:MM:SS.MMM after 00:00:00.000.";
  if (document.material_start_seconds !== null && document.material_end_seconds !== null && document.material_end_seconds <= document.material_start_seconds) errors.material_end_seconds = "Material end must be after material start.";
  return errors;
}

const metadataFields: Array<keyof ReviewMcfDocument> = ["title", "year", "work_type", "season", "episode", "source_url", "imdb_url", "release", "comment", "material_start_seconds", "material_end_seconds"];

export function formatMcfTimestamp(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "";
  const totalMilliseconds = Math.round(seconds * 1000);
  const hours = Math.floor(totalMilliseconds / 3_600_000);
  const minutes = Math.floor((totalMilliseconds % 3_600_000) / 60_000);
  const wholeSeconds = Math.floor((totalMilliseconds % 60_000) / 1000);
  const milliseconds = totalMilliseconds % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

export function parseMcfTimestamp(value: string): number {
  const match = /^(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})$/.exec(value);
  if (!match) return Number.NaN;
  const [, hours, minutes, seconds, milliseconds] = match;
  const parsed = Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds) + Number(milliseconds) / 1000;
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

/** Edit a timestamp while retaining a readable formatted value.
 * @param props Field label, seconds, error, and change callback.
 */
function McfTimestampField({ label, value, error, onChange }: { label: "Material start" | "Material end"; value: number | null; error?: string; onChange: (value: number | null) => void }) {
  const formattedValue = formatMcfTimestamp(value);
  const [text, setText] = useState(formattedValue);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setText(formattedValue);
  }, [editing, formattedValue]);
  return <label className="field"><span>{label}</span><input
    aria-label={`MCF ${label.toLowerCase()}`}
    aria-invalid={Boolean(error)}
    type="text"
    inputMode="numeric"
    placeholder="HH:MM:SS.MMM"
    value={text}
    onFocus={() => setEditing(true)}
    onBlur={() => setEditing(false)}
    onChange={(event) => {
      const next = event.target.value;
      setText(next);
      onChange(next === "" ? null : parseMcfTimestamp(next));
    }}
  />{error && <small className="form-error">{error}</small>}</label>;
}

/** Edit review MCF metadata and show metadata from imported scans.
 * @param document Current document metadata.
 * @param onChange Replaces the edited document.
 * @param scans Scans that may supply imported metadata.
 */
export function McfDocumentEditor({ document, onChange, scans }: { document: ReviewMcfDocument; onChange: (document: ReviewMcfDocument) => void; scans: Scan[] }) {
  const errors = validateMcfDocument(document);
  const imported = scans.filter((scan) => scan.mcf_document);
  const update = (field: keyof ReviewMcfDocument, value: unknown) => onChange({ ...document, [field]: value });
  const summary = [document.title, document.year, document.work_type, document.release].filter(Boolean).join(" · ") || "Incomplete metadata";
  return <details className="mcf-document-editor">
    <summary><span><strong>MCF document metadata</strong><small>{summary}</small></span><span>{formatMcfTimestamp(document.material_start_seconds) || "?"}–{formatMcfTimestamp(document.material_end_seconds) || "?"}</span></summary>
    <div className="mcf-document-fields">
      <label className="field"><span>Title</span><input aria-label="MCF title" value={document.title || ""} onChange={(event) => update("title", event.target.value || null)} /></label>
      <label className="field"><span>Year</span><input aria-label="MCF year" type="number" min="0" value={document.year ?? ""} onChange={(event) => update("year", event.target.value === "" ? null : Number(event.target.value))} />{errors.year && <small className="form-error">{errors.year}</small>}</label>
      <label className="field"><span>Type</span><select aria-label="MCF type" value={document.work_type || ""} onChange={(event) => update("work_type", event.target.value || null)}><option value="">Not set</option><option value="movie">Movie</option><option value="series">Series</option><option value="episode">Episode</option></select></label>
      {(document.work_type === "series" || document.work_type === "episode") && <label className="field"><span>Season</span><input aria-label="MCF season" type="number" min="1" value={document.season ?? ""} onChange={(event) => update("season", event.target.value === "" ? null : Number(event.target.value))} />{errors.season && <small className="form-error">{errors.season}</small>}</label>}
      {document.work_type === "episode" && <label className="field"><span>Episode</span><input aria-label="MCF episode" type="number" min="1" value={document.episode ?? ""} onChange={(event) => update("episode", event.target.value === "" ? null : Number(event.target.value))} />{errors.episode && <small className="form-error">{errors.episode}</small>}</label>}
      <label className="field"><span>Source URL</span><input aria-label="MCF source URL" type="url" value={document.source_url || ""} onChange={(event) => update("source_url", event.target.value || null)} />{errors.source_url && <small className="form-error">{errors.source_url}</small>}</label>
      <label className="field"><span>IMDb URL</span><input aria-label="MCF IMDb URL" type="url" value={document.imdb_url || ""} onChange={(event) => update("imdb_url", event.target.value || null)} />{errors.imdb_url && <small className="form-error">{errors.imdb_url}</small>}</label>
      <label className="field"><span>Release</span><input aria-label="MCF release" value={document.release || ""} onChange={(event) => update("release", event.target.value || null)} /></label>
      <label className="field mcf-metadata-comment"><span>Comment</span><textarea aria-label="MCF document comment" value={document.comment || ""} onChange={(event) => update("comment", event.target.value || null)} rows={2} /></label>
      <McfTimestampField label="Material start" value={document.material_start_seconds} error={errors.material_start_seconds} onChange={(value) => update("material_start_seconds", value)} />
      <McfTimestampField label="Material end" value={document.material_end_seconds} error={errors.material_end_seconds} onChange={(value) => update("material_end_seconds", value)} />
    </div>
    {imported.map((scan) => <details className="mcf-source-comparison" key={scan.id}><summary>MCF metadata · {scan.name || `Scan #${scan.scan_id}`} · {scan.mcf_document?.source_version}</summary><dl>{metadataFields.map((field) => <div key={field}><dt>{field.replace(/_/g, " ")}</dt><dd>{String(scan.mcf_document?.[field as keyof typeof scan.mcf_document] ?? "Not set")}</dd></div>)}</dl></details>)}
  </details>;
}

export function McfMetadataSourceSelector({ candidates, value, onChange }: { candidates: McfMetadataSeedCandidate[]; value: McfMetadataSeedRequest | null; onChange: (value: McfMetadataSeedRequest) => void }) {
  if (!candidates.length) return null;
  return <fieldset className="mcf-metadata-source"><legend>Choose MCF metadata source</legend><p>These variants describe the same selected physical file. Choose one editable starting point.</p>{candidates.map((candidate) => { const candidateValue = candidate.kind === "catalog" ? "catalog" : `scan:${candidate.scan_id}`; const selectedValue = value?.kind === "catalog" ? "catalog" : value?.kind === "scan" ? `scan:${value.scan_id}` : ""; return <label key={candidateValue}><input type="radio" name="mcf-metadata-source" value={candidateValue} checked={selectedValue === candidateValue} onChange={() => onChange(candidate.kind === "catalog" ? { kind: "catalog" } : { kind: "scan", scan_id: candidate.scan_id || "" })} /><span><strong>{candidate.label}</strong><small>{candidate.kind === "catalog" ? "Catalog metadata" : "MCF metadata"} · {candidate.mcf_document.title || "Untitled"}</small></span></label>; })}</fieldset>;
}
