import type { LinkPreview, ProofBasis } from "../../api/api-account-link";
import { blockerText } from "./account-link-flow";

const BASIS: Record<ProofBasis, string> = {
  actor_authentication: "Proven by your own sign-in and the Jellyfin sign-in.",
  admin_attested: "Admin attested. You confirm that this Jellyfin user is the same person, on your fresh sign-in and the Jellyfin sign-in. The audit log records your attestation.",
};
const MEDIA = { local: "Editarr local media access", jellyfin: "Jellyfin library access", suspended: "Suspended until relinked" } as const;
const CAUSES = { local: "disabled by an admin", upstream_disabled: "disabled in Jellyfin", upstream_deleted: "deleted in Jellyfin", unlinked: "no Jellyfin link" } as const;

function status({ status: { current, after, cause } }: LinkPreview): string {
  const before = current === "disabled" && cause ? `disabled (${CAUSES[cause]})` : current;
  if (current === after) return `${before}. Unchanged.`;
  return after === "active" ? `${before} becomes active. Its existing API key works again.` : `${before} becomes ${after}.`;
}

function libraries(preview: LinkPreview): string {
  const scope = preview.media.all_libraries ? "all Jellyfin libraries" : `${preview.media.libraries} Jellyfin ${preview.media.libraries === 1 ? "library" : "libraries"}`;
  return `${scope}${preview.media.parental_limit ? ", with a parental rating limit" : ""}`;
}

/** The MU-35 impact preview, read-only. It shows identifiers and counts, never paths or secrets. */
export function LinkPreviewView({ preview }: { preview: LinkPreview }) {
  const exportsMove = preview.export_id.current !== preview.export_id.after;
  return <div className="link-preview">
    <p className={`link-basis link-basis-${preview.proof_basis}`}><strong>Proof basis.</strong> {BASIS[preview.proof_basis]}</p>
    {preview.blockers.length > 0 && <section className="link-blockers" aria-label="Link blockers">
      <h3>Linking is blocked</h3>
      <ul>{preview.blockers.map(code => <li key={code}>{blockerText(code)}</li>)}</ul>
    </section>}
    <dl className="link-changes">
      <div><dt>Jellyfin user</dt><dd>{preview.profile.name.after} <span className="link-id">({preview.identity.after.user_id})</span></dd></div>
      <div><dt>Server</dt><dd>{preview.identity.current && preview.identity.current.server_id !== preview.identity.after.server_id
        ? <>From <span className="link-id">{preview.identity.current.server_id}</span> to <span className="link-id">{preview.identity.after.server_id}</span></>
        : <span className="link-id">{preview.identity.after.server_id}</span>}</dd></div>
      <div><dt>Export ID</dt><dd>{exportsMove ? <><span className="link-id">{preview.export_id.current}</span> becomes <span className="link-id">{preview.export_id.after}</span></> : "Unchanged"}</dd></div>
      <div><dt>Role</dt><dd>{preview.role.after}. Kept regardless of Jellyfin.</dd></div>
      <div><dt>Media access</dt><dd>{MEDIA[preview.media.current]} becomes {libraries(preview)}.</dd></div>
      <div><dt>Name and avatar</dt><dd>Name becomes {preview.profile.name.after}{preview.profile.avatar === "jellyfin" ? "; avatar comes from Jellyfin" : "; the local avatar is removed because Jellyfin has none"}. Both become read-only. Email and timezone stay editable.</dd></div>
      <div><dt>Status</dt><dd>{status(preview)}</dd></div>
    </dl>
    <section aria-label="Personal exports">
      <h3>Personal exports</h3>
      {preview.exports.length === 0 ? <p>No personal exports to move.</p> : <>
        <p>New files are written under the new export ID first. Old files are removed only after every new file is in place. If anything fails, the old exports stay.</p>
        <ul className="link-exports">{preview.exports.map(move => <li key={move.export_id}>
          <span>{move.format.toUpperCase()} for media {move.media_item_id}</span>
          <span>{move.action === "move" ? "File moves to the new export ID" : "Record moves; no file is written"}</span>
          {move.conflict && <span className="link-conflict">{blockerText(move.conflict)}</span>}
        </li>)}</ul>
      </>}
    </section>
  </div>;
}
