import { useEffect, useRef, useState } from "react";
import { cancelCommunityProfileUpdate, renameCommunityProfile } from "../../api/api-community";
import type { CommunityConnection, CommunityRequest } from "../../api/api-community";
import { useCan } from "../../auth/auth-gate";

type Run = (operation: (options?: CommunityRequest) => Promise<CommunityConnection>, current?: boolean) => Promise<void>;

export function CommunityProfileEditor({ connection, busy, verified, run, mayEdit: permitted }: {
  connection: CommunityConnection; busy: boolean; verified: boolean; run: Run; mayEdit?: boolean;
}) {
  const ownEdit = useCan("community.identity.profile.own");
  const mayEdit = permitted ?? ownEdit;
  const profile = connection.profile;
  const pending = connection.pendingRename;
  const [name, setName] = useState(profile?.displayName ?? "");
  const intent = useRef<{ display_name: string; operation_id: string } | null>(null);
  useEffect(() => {
    setName(pending?.display_name ?? profile?.displayName ?? "");
    intent.current = pending ?? null;
  }, [profile?.displayName, profile?.profileRevision, pending?.display_name, pending?.operation_id]);
  if (!profile) return null;

  async function save() {
    const operation = pending ?? intent.current ?? { display_name: name, operation_id: crypto.randomUUID() };
    intent.current = operation;
    await run((options) => renameCommunityProfile(operation.display_name, operation.operation_id, options), true);
  }

  const length = Array.from(name.trim()).length;
  const valid = length >= 3 && length <= 40 && !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(name);
  return <div className="community-profile">
    <h3>Contributor profile</h3>
    {mayEdit ? <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <label className="field"><span>Contributor name</span><input value={name} onChange={(event) => { setName(event.target.value); intent.current = null; }} readOnly={Boolean(pending)} disabled={busy || !verified} autoComplete="off" aria-describedby="community-name-policy" /></label>
      <p id="community-name-policy" className="muted-copy">3-40 characters. Names are unique across contributors.</p>
      <div className="button-row"><button className="btn-primary" disabled={busy || !verified || !valid || (!pending && name === profile.displayName)}>{pending ? "Retry name change" : "Save name"}</button>
        {pending && <button type="button" className="btn-secondary" disabled={busy} onClick={() => void run(cancelCommunityProfileUpdate)}>Discard pending name change</button>}
      </div>
    </form> : <p>{profile.displayName}</p>}
    {pending && <p role="status">Name change pending: {pending.display_name}</p>}
    <dl className="community-identity"><div><dt>Contributor ID</dt><dd><code>{profile.contributorId}</code></dd></div><div><dt>Short ID</dt><dd><code>{profile.shortId}</code></dd></div></dl>
  </div>;
}