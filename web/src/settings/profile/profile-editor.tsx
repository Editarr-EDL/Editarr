import { useId, useRef, useState, type FormEvent } from "react";
import { accountAvatarUrl, editAccount, removeAccountAvatar, uploadAccountAvatar, type AccountDetail } from "../../api/api-users";

export function accountName(account: { display_name: string | null; username: string | null; id: number }) {
  return account.display_name || account.username || `Account ${account.id}`;
}

/** A Jellyfin account's stored username; without one, its Jellyfin name, labeled so it never reads as this account's username. */
export function jellyfinUsername(account: { display_name: string | null; username: string | null }) {
  if (account.username) return account.username;
  return account.display_name ? `None (Jellyfin name: ${account.display_name})` : "None";
}

export function AccountAvatar({ account }: { account: AccountDetail | Pick<AccountDetail, "id" | "avatar" | "display_name" | "username"> }) {
  return account.avatar
    ? <img className="account-avatar" src={accountAvatarUrl(account)} alt="" width="40" height="40" />
    : <span className="account-avatar account-avatar-placeholder" aria-hidden="true">{accountName(account).slice(0, 1).toUpperCase()}</span>;
}

/** Edit an account's preferences; a Jellyfin-linked account's username, name, and avatar are shown read-only. */
export function ProfileEditor({ account, editable, onSaved }: {
  account: AccountDetail; editable: boolean; onSaved: (account: AccountDetail) => void;
}) {
  const id = useId();
  const [displayName, setDisplayName] = useState(account.display_name ?? "");
  const [email, setEmail] = useState(account.email ?? "");
  const [timezone, setTimezone] = useState(account.timezone ?? "");
  const [language, setLanguage] = useState(account.language ?? "en");
  const [image, setImage] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const imageInput = useRef<HTMLInputElement>(null);
  const fromJellyfin = account.read_only_fields.includes("username");
  const syncedName = account.read_only_fields.includes("display_name");
  const syncedAvatar = account.read_only_fields.includes("avatar");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || !editable) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const next = await editAccount(account.id, {
        ...(!syncedName ? { display_name: displayName.trim() || null } : {}),
        email: email.trim() || null, timezone: timezone.trim() || null, language,
      });
      onSaved(next); setMessage("Profile saved.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save profile."); }
    finally { setBusy(false); }
  }

  async function changeAvatar(remove: boolean) {
    if (busy || !editable || syncedAvatar || (!remove && !image)) return;
    if (!remove && image && image.size > 5 * 1024 * 1024) { setError("Avatar images must be 5 MiB or smaller."); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      const next = remove ? await removeAccountAvatar(account.id) : await uploadAccountAvatar(account.id, image!);
      setImage(null);
      if (imageInput.current) imageInput.current.value = "";
      onSaved(next); setMessage(remove ? "Avatar removed." : "Avatar saved.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save avatar."); }
    finally { setBusy(false); }
  }

  return <section className="profile-editor" aria-label="Profile details">
    <div className="account-identity"><AccountAvatar account={account} /><div><h2>{accountName(account)}</h2><p>{account.source === "jellyfin" ? "Jellyfin" : "Local"} account</p></div></div>
    {syncedAvatar ? <p>Avatar from Jellyfin</p> : editable && <div className="profile-avatar-controls">
      <label className="field" htmlFor={`${id}-avatar`}><span>Avatar image</span><input ref={imageInput} id={`${id}-avatar`} type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={busy} onChange={(event) => { setImage(event.target.files?.[0] ?? null); setError(""); }} /></label>
      <div className="button-row"><button type="button" className="btn-secondary" disabled={busy || !image} onClick={() => void changeAvatar(false)}>Upload avatar</button>
        {account.avatar && <button type="button" className="btn-secondary" disabled={busy} onClick={() => void changeAvatar(true)}>Remove avatar</button>}</div>
    </div>}
    <form onSubmit={(event) => void save(event)}>
      <div className="account-fields">
        {fromJellyfin && <div><span className="account-field-label">Jellyfin username</span><p>{jellyfinUsername(account)}</p></div>}
        {syncedName ? null : !editable ? <div><span className="account-field-label">Display name</span><p>{account.display_name ?? "Not set"}</p></div>
          : <label className="field"><span>Display name</span><input value={displayName} maxLength={100} disabled={busy} autoComplete="nickname" onChange={(event) => setDisplayName(event.target.value)} /></label>}
        <label className="field"><span>Email</span><input type="email" value={email} maxLength={254} readOnly={!editable} disabled={busy} autoComplete="email" onChange={(event) => setEmail(event.target.value)} /></label>
        <label className="field"><span>Timezone</span><input value={timezone} maxLength={100} readOnly={!editable} disabled={busy} placeholder="America/New_York" onChange={(event) => setTimezone(event.target.value)} /></label>
        <label className="field"><span>Display language</span><select value={language} disabled={!editable || busy} onChange={(event) => setLanguage(event.target.value)}>
          {account.available_languages.map((code) => <option key={code} value={code}>{code === "en" ? "English" : code}</option>)}
        </select></label>
      </div>
      {editable && <button type="submit" className="btn-primary" disabled={busy}>Save profile</button>}
    </form>
    {message && <p role="status">{message}</p>}{error && <p role="alert" className="form-status">{error}</p>}
  </section>;
}