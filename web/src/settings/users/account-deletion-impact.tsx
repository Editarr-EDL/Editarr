import type { AccountDeletionPreview, Disposition, FileChoices } from "../../api/api-account-deletion";
import type { ExportPublication } from "../../api/api-mcf-types";
import { ExportConditionText } from "../../review/export-publication-view";
import { scanStays, blockerText, consequences, transferEffectText } from "./account-deletion-copy";

const fileName = (publication: ExportPublication) => `${publication.format.toUpperCase()} · ${publication.media_item_id}${publication.scope === "default" ? " · item default" : ""}`;

/**
 * Every consequence of one previewed disposition: the account, reviews, files, scans,
 * jobs, community history, and blockers. In delete mode each visible file gets an
 * explicit keep/remove choice. Hidden resources appear only as counts.
 */
export function DeletionImpact({ preview, disposition, name, choices, disabled = false, onChoice }: {
  preview: AccountDeletionPreview;
  disposition: Disposition;
  name: string;
  choices: FileChoices;
  disabled?: boolean;
  onChoice: (exportId: number, choice: "retain" | "remove") => void;
}) {
  const listed = preview.mode === "delete" ? [] : preview.publications.items;
  const destination = disposition.mode === "transfer" ? disposition.destination.name : null;
  return <div className="deletion-impact">
    {preview.blockers.length > 0 && <section className="deletion-blockers" aria-label={`Deletion blockers for ${name}`}>
      <h4>Blocked</h4>
      <ul>{preview.blockers.map(blocker => <li key={blocker.code}>{blockerText(blocker)}</li>)}</ul>
    </section>}
    <dl className="deletion-consequences">
      {consequences(preview, disposition, name).map(line => <div key={line.label}><dt>{line.label}</dt><dd>{line.text}</dd></div>)}
    </dl>
    {listed.length > 0 && <section aria-label={`Published files of ${name}`}>
      <h4>{destination === null ? "Published files that stay" : "Published files affected"}</h4>
      <ul className="deletion-files">{listed.map(publication => <li key={publication.id}>{fileName(publication)}{destination !== null && `: ${transferEffectText(preview, publication, destination)}`}</li>)}</ul>
    </section>}
    {preview.mode === "delete" && preview.files.length > 0 && <fieldset className="deletion-file-choices" disabled={disabled}>
      <legend>Published files from {name}'s reviews</legend>
      {preview.files.map(({ publication, choices: allowed }) => <div key={publication.id} className="deletion-file" role="group" aria-label={fileName(publication)}>
        <div><strong>{fileName(publication)}</strong><ExportConditionText publication={publication} source={`${name}'s review`} /></div>
        <div className="deletion-file-options">
          <label><input type="radio" name={`account-file-${preview.user_id}-${publication.id}`} checked={choices[publication.id] === "retain"} onChange={() => onChoice(publication.id, "retain")} /> Keep file</label>
          {allowed.includes("remove")
            ? <label><input type="radio" name={`account-file-${preview.user_id}-${publication.id}`} checked={choices[publication.id] === "remove"} onChange={() => onChoice(publication.id, "remove")} /> Remove file</label>
            : <small>You can't remove this file, so it is kept.</small>}
        </div>
      </div>)}
      <p className="muted-copy">Kept files stay published, detached from the deleted review, with their owner and policy unchanged.</p>
    </fieldset>}
    {preview.mode === "delete" && preview.scans.items.length > 0 && <section aria-label={`Scans of ${name}`}>
      <h4>Scans</h4>
      <ul className="deletion-files">{preview.scans.items.map(scan => <li key={scan.scan_id}>Scan {scan.scan_id.slice(0, 8)} ({scan.status}): {scan.required ? "kept, still needed" : `deleted unless ${scanStays(true)}`}</li>)}</ul>
    </section>}
    {preview.publications.hidden > 0 && <p className="muted-copy">{preview.publications.hidden} more published {preview.publications.hidden === 1 ? "file is" : "files are"} hidden from you.</p>}
  </div>;
}
