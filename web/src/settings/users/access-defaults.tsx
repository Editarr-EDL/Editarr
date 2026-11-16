import { useEffect, useState } from "react";
import { readMediaDefault, readRoleDefaults, saveMediaDefault, saveRoleDefault, type AssignedRole, type MediaScope, type RoleDefaults, type ScopeChoice } from "../../api/api-user-access";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import { can, type Principal } from "../../auth/session";
import { MediaScopeEditor } from "./media-scope-editor";
import { PolicySection } from "./policy-section";

export function AccessDefaults({ principal }: { principal: Principal }) {
  return <>
    {principal.role === "owner" && can(principal, "roles.defaults.edit") && <PolicySection title="Role permission defaults"><RoleDefaultsEditor /></PolicySection>}
    {can(principal, "media_access.defaults.edit") && <PolicySection title="Local media default"><MediaDefaultEditor /></PolicySection>}
  </>;
}
function RoleDefaultsEditor() {
  const [defaults, setDefaults] = useState<RoleDefaults | null>(null);
  const [role, setRole] = useState<AssignedRole>("user");
  const [capability, setCapability] = useState("");
  const [grant, setGrant] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); void readRoleDefaults(controller.signal).then(next => {
    if (!controller.signal.aborted) { setDefaults(next); setCapability(next.user[0]?.capability ?? ""); }
  }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read role defaults."); }); return () => controller.abort(); }, []);
  const row = defaults?.[role].find(entry => entry.capability === capability);
  const selectedGrant = grant ?? (row?.explicit ? row.granted ? "allow" : "deny" : "inherit");
  return <>
    {error && <p role="alert">{error}</p>}
    {defaults && <>
      <label className="field"><span>Default role</span><select value={role} onChange={event => { setRole(event.target.value as AssignedRole); setGrant(null); }}><option value="user">User</option><option value="admin">Admin</option></select></label>
      <label className="field"><span>Default permission</span><select value={capability} onChange={event => { setCapability(event.target.value); setGrant(null); }}>{defaults[role].map(entry => <option key={entry.capability}>{entry.capability}</option>)}</select></label>
      {row && <p>Effective role default: {row.granted ? "Allow" : "Deny"}. Catalog default: {row.catalog_default ? "Allow" : "Deny"}.</p>}
      <label className="field"><span>Role permission value</span><select value={selectedGrant} onChange={event => setGrant(event.target.value)}><option value="inherit">Catalog default</option><option value="allow">Allow</option><option value="deny">Deny</option></select></label>
      <button className="btn-secondary" disabled={!row} onClick={() => setPending(true)}>Save role default</button>
    </>}
    {pending && <FreshAuthenticationDialog title="Save role permission default" body="Accounts without an individual override follow this change immediately." actionLabel="Confirm role default" onClose={() => setPending(false)} onConfirm={async password => { setDefaults(await saveRoleDefault(role, capability, selectedGrant === "inherit" ? null : selectedGrant === "allow", password)); setGrant(null); setPending(false); }} />}
  </>;
}
function MediaDefaultEditor() {
  const [scope, setScope] = useState<MediaScope | null>(null);
  const [choice, setChoice] = useState<ScopeChoice | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); void readMediaDefault(controller.signal).then(next => { if (!controller.signal.aborted) setScope(next); }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read media default."); }); return () => controller.abort(); }, []);
  return <>{error && <p role="alert">{error}</p>}{scope && <MediaScopeEditor key={JSON.stringify(scope)} scope={scope} allowInherit={false} onSave={setChoice} />}
    {choice && <FreshAuthenticationDialog title="Save local media default" body="Local accounts without an individual scope follow this change immediately." actionLabel="Confirm media default" onClose={() => setChoice(null)} onConfirm={async password => { setScope(await saveMediaDefault(choice, password)); setChoice(null); }} />}
  </>;
}
