import { Fragment, useId, useMemo, useState } from "react";
import type { SharingFinding, SharingPreview } from "../api/api-community-sharing";
import { CommunityIcon } from "./community-icon";
import { categoryLabel } from "./community-result-state";
import { estimateText } from "./community-master-format";

type Counts = { total: number; scanned: number | null; added: number | null; edited: number | null };
type Subcategory = { name: string; counts: Counts };
type Category = { name: string; counts: Counts; subcategories: Map<string | null, Subcategory> };
const countKeys = ["total", "scanned", "added", "edited"] as const;
const labels = { total: "Total", scanned: "Scanned", added: "Manually added", edited: "Manually edited" };
const emptyCounts = (): Counts => ({ total: 0, scanned: 0, added: 0, edited: 0 });
const add = (count: number | null, value: boolean | null) =>
  count === null || value === null ? null : count + Number(value);

function countFinding(counts: Counts, added: boolean | null, edited: boolean | null) {
  counts.total++;
  counts.scanned = add(counts.scanned, added === null ? null : !added);
  counts.added = add(counts.added, added);
  counts.edited = add(counts.edited, edited);
}

function summarize(findings: SharingFinding[], scan: boolean) {
  const categories = new Map<string | null, Category>();
  const totals = emptyCounts();
  const notes = new Set<string>();
  let hasAudio = false;
  for (const finding of findings) {
    const { category, subcategory, manual, note } = finding;
    // A raw scan has no manual changes, including previews from before tracking was introduced.
    const added = scan ? false : manual?.added ?? null;
    const edited = scan ? false : manual?.edited ?? null;
    const group = categories.get(category) ?? {
      name: category ? categoryLabel(category) : "Unclassified", counts: emptyCounts(), subcategories: new Map(),
    };
    categories.set(category, group);
    const sub = group.subcategories.get(subcategory) ?? {
      name: subcategory && subcategory !== category ? categoryLabel(subcategory) : "General", counts: emptyCounts(),
    };
    group.subcategories.set(subcategory, sub);
    for (const counts of [totals, group.counts, sub.counts]) countFinding(counts, added, edited);
    if (finding.channel === "audio" || finding.channel === "both") hasAudio = true;
    if (note) notes.add(note);
  }
  return { totals, categories, notes: [...notes], hasAudio };
}

function Count({ value }: { value: number | null }) {
  return value === null ? <><span aria-hidden="true">—</span><span className="visually-hidden">Unknown</span></> : value;
}

function CountCells({ counts }: { counts: Counts }) {
  return <>{countKeys.map((key) => <td key={key}><Count value={counts[key]} /></td>)}</>;
}

function CategoryCounts({ categories }: { categories: Map<string | null, Category> }) {
  const id = useId();
  const [expanded, setExpanded] = useState<ReadonlySet<string | null>>(() => new Set());
  return <table className="sharing-category-table" aria-label="Filter counts by category">
    <colgroup><col /><col /><col /><col /><col /></colgroup>
    <thead><tr><th scope="col">Category</th>{countKeys.map((key) =>
      <th scope="col" key={key} aria-label={labels[key]}>{key === "added" ? "Added" : key === "edited" ? "Edited" : labels[key]}</th>)}
    </tr></thead>
    {[...categories].sort(([, a], [, b]) => b.counts.total - a.counts.total || a.name.localeCompare(b.name))
      .map(([key, { name, counts, subcategories }], index) => {
        const open = expanded.has(key);
        const region = id + "-" + index;
        return <Fragment key={key === null ? "unclassified" : "category-" + key}>
          <tbody><tr className="sharing-category">
            <th scope="row"><button type="button" aria-expanded={open} aria-controls={region}
              onClick={() => setExpanded((previous) => {
                const next = new Set(previous);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })}><CommunityIcon name="expand" />{name}</button></th><CountCells counts={counts} />
          </tr></tbody>
          <tbody id={region} hidden={!open}>
            {[...subcategories].sort(([, a], [, b]) => a.name.localeCompare(b.name)).map(([key, sub]) =>
              <tr className="sharing-subcategory" key={key ?? "unclassified"}><th scope="row">
                <span className="visually-hidden">{name}, </span>{sub.name}</th><CountCells counts={sub.counts} /></tr>)}
          </tbody>
        </Fragment>;
      })}
  </table>;
}

export function SharingPreviewDetails({ value }: { value: SharingPreview }) {
  const { artifact, exclusions, warnings } = value.preview;
  const { totals, categories, hasAudio, notes } = useMemo(
    () => summarize(artifact.findings, artifact.kind === "scan"), [artifact.findings, artifact.kind]);
  const issueCounts = new Map<string, number>();
  for (const { reason, channel } of exclusions) {
    const label = reason === "other_audio_track" ? "Audio from another track" :
      [channel ? categoryLabel(channel) : "", categoryLabel(reason)].filter(Boolean).join(" · ");
    issueCounts.set(label, (issueCounts.get(label) ?? 0) + 1);
  }
  const notices = warnings.filter((warning) => warning !== "audio_track_unknown" || artifact.audio_association !== "unknown");
  const hasUnknownCounts = countKeys.some((key) => totals[key] === null);
  return <section className="sharing-preview" aria-label="Filter summary">
    <h3>Filter summary</h3>
    {value.estimates && <div className="sharing-master-estimates">
      {value.work && <p className="sharing-name">{value.work.title}{value.work.year ? ` (${value.work.year})` : ""}
        {value.versionChoice && ` · ${value.versionChoice.label}`}</p>}
      <p role="status"><strong>{estimateText({ estimates: value.estimates })}</strong></p>
      <p className="muted-copy">Counts are estimates. The community service makes the final match.</p>
    </div>}
    <dl className="sharing-totals">{countKeys.map((key) => <div key={key}>
      <dt>{labels[key]}</dt><dd><Count value={totals[key]} /></dd>
    </div>)}</dl>
    {categories.size ? <CategoryCounts categories={categories} /> : <p>No filters to share.</p>}
    {(totals.edited !== 0 || hasUnknownCounts) && categories.size > 0 && <p className="muted-copy">
      {[totals.edited !== 0 && "Edited filters can also count as scanned or added.",
        hasUnknownCounts && "A dash indicates an unknown count."].filter(Boolean).join(" ")}
    </p>}
    {artifact.audio_association === "unknown" && <p role="status">Audio track unknown. Review required.</p>}
    {notices.length > 0 && <ul aria-label="Sharing warnings">{notices.map((notice) => <li key={notice}>
      {notice === "audio_fingerprint_unsupported"
        ? "Audio fingerprint unavailable for this track. Recipients must verify the audio track and timing."
        : categoryLabel(notice)}
    </li>)}</ul>}
    {issueCounts.size > 0 && <ul aria-label="Excluded filters">{[...issueCounts].map(([label, count]) =>
      <li key={label}>{label} excluded from {count} {count === 1 ? "filter" : "filters"}</li>)}</ul>}
    {hasAudio && <p className="muted-copy">Shared audio filters include time ranges, classifications, and any public notes. View detected words in the local scan.</p>}
    {notes.length > 0 && <details className="sharing-public-notes">
      <summary tabIndex={0}>Public notes ({notes.length})</summary>
      <ul>{notes.map((note) => <li key={note}>{note}</li>)}</ul>
    </details>}
  </section>;
}
