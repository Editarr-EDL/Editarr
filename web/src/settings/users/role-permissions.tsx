import { useEffect, useState } from "react";
import { assignAccountRole, readPermissions, readRoleDefaults, savePermissions, type AssignedRole, type Override, type PermissionView, type RoleDefault } from "../../api/api-user-access";
import type { AccountDetail } from "../../api/api-users";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import type { Principal } from "../../auth/session";
import { mayManageAccount } from "./account-actions";

export function RolePermissions({ account, principal, onChanged }: {
  account: AccountDetail; principal: Principal; onChanged: (account?: AccountDetail) => void;
}) {
  const allowed = principal.role === "owner" && mayManageAccount(principal, account, "permissions.overrides.edit");
  return allowed ? <RolePermissionsEditor key={`${account.id}:${account.role}`} account={account} principal={principal} onChanged={onChanged} /> : null;
}

function RolePermissionsEditor({ account, principal, onChanged }: {
  account: AccountDetail; principal: Principal; onChanged: (account?: AccountDetail) => void;
}) {
  const [view, setView] = useState<PermissionView | null>(null);
  const [defaults, setDefaults] = useState<RoleDefault[]>([]);
  const [role, setRole] = useState<AssignedRole>(account.role === "admin" ? "admin" : "user");
  const [capability, setCapability] = useState("");
  const [effect, setEffect] = useState<Override>("inherit");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState<"role" | "override" | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([readPermissions(account.id, controller.signal), readRoleDefaults(controller.signal)]).then(([permissions, roles]) => {
      if (controller.signal.aborted) return;
      setView(permissions); const rows = roles[permissions.role]; setDefaults(rows);
      setCapability(rows[0]?.capability ?? ""); setEffect(permissions.overrides[rows[0]?.capability] ?? "inherit");
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to read permissions."); });
    return () => controller.abort();
  }, [account.id]);
  function selectCapability(name: string) { setCapability(name); setEffect(view?.overrides[name] ?? "inherit"); }
  return <section className="account-policy-section" aria-label="Role and permissions">
    {mayManageAccount(principal, account, "roles.assign") && <div className="button-row"><label className="field"><span>Assigned role</span><select value={role} onChange={event => setRole(event.target.value as AssignedRole)}><option value="user">User</option><option value="admin">Admin</option></select></label><button className="btn-secondary" onClick={() => setPending("role")}>Assign role</button></div>}
    {error && <p role="alert">{error}</p>}
    {!view && !error && <p role="status">Loading permissions...</p>}
    {view && <>
      <label className="field"><span>Permission</span><select value={capability} onChange={event => selectCapability(event.target.value)}>{defaults.map(row => <option key={row.capability} value={row.capability}>{row.capability}</option>)}</select></label>
      {capability && <><p>Role default: {defaults.find(row => row.capability === capability)?.granted ? "Allow" : "Deny"}. Effective: {view.effective.includes(capability) ? "Allow" : "Deny"}. Stored override: {view.overrides[capability] ?? "inherit"}.</p>
        <label className="field"><span>Permission override</span><select value={effect} onChange={event => setEffect(event.target.value as Override)}><option value="inherit">Inherit</option><option value="allow">Allow</option><option value="deny">Deny</option></select></label><button className="btn-secondary" onClick={() => setPending("override")}>Save permission override</button></>}
      <details><summary>Effective permissions</summary><ul>{view.effective.map(name => <li key={name}>{name}</li>)}</ul></details>
    </>}
    {message && <p role="status">{message}</p>}
    {pending && <FreshAuthenticationDialog title={pending === "role" ? "Assign account role" : "Save permission override"} body="This change applies to this account immediately." actionLabel="Confirm change" onClose={() => setPending(null)} onConfirm={async password => {
      if (pending === "override") { setView(await savePermissions(account.id, { [capability]: effect }, password)); onChanged(); }
      else onChanged(await assignAccountRole(account.id, role, password));
      setPending(null); setMessage("Account permissions saved.");
    }} />}
  </section>;
}
