import { useId } from "react";
import type { JellyfinReturn } from "../../api/api-account-deletion";

/** Whether a deleted account's Jellyfin user may come back; shown only when a target is linked. */
export function JellyfinReturnChoice({ value, onChange, many = false }: { value: JellyfinReturn; onChange: (value: JellyfinReturn) => void; many?: boolean }) {
  const name = useId();
  const who = many ? "their Jellyfin users" : "their Jellyfin user";
  return <fieldset className="deletion-jellyfin">
    <legend>After deletion, {who}</legend>
    <label className="account-checkbox"><input type="radio" name={name} checked={value === "allow"} onChange={() => onChange("allow")} />
      <span><strong>Allow them back</strong><br />Their next Jellyfin sign-in creates a fresh Editarr account.</span></label>
    <label className="account-checkbox"><input type="radio" name={name} checked={value === "block"} onChange={() => onChange("block")} />
      <span><strong>Block them</strong><br />They can't sign in or be imported until the owner unblocks them in Users.</span></label>
  </fieldset>;
}
