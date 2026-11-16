import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { logoutAuthentication } from "../api/api-auth";
import { readAccount, type AccountDetail } from "../api/api-users";
import { AccountAvatar } from "../settings/profile/profile-editor";
import { useOptionalAuthenticationGate } from "./auth-gate";
import { can, type Principal } from "./session";

type MenuIdentity = Pick<AccountDetail, "id" | "avatar" | "display_name" | "username">;

/**
 * The signed-in account's avatar button and its menu. The account is read once
 * per principal for its username and avatar; without that read the menu falls
 * back to the session's display name.
 */
export function AccountMenu({ principal, onOpenProfile }: { principal: Principal; onOpenProfile: () => void }) {
  const authenticationGate = useOptionalAuthenticationGate();
  const [account, setAccount] = useState<MenuIdentity | null>(null);
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const mayReadAccount = can(principal, "profile.view.own");

  useEffect(() => {
    if (!mayReadAccount) return;
    const controller = new AbortController();
    void readAccount(principal.userId, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setAccount(next); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [mayReadAccount, principal.userId]);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const identity: MenuIdentity = account ?? { id: principal.userId, avatar: null, display_name: principal.displayName, username: null };
  const username = account?.username || principal.displayName;

  const close = () => { setOpen(false); trigger.current?.focus(); };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const current = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "Tab") setOpen(false);
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(current + step + items.length) % items.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      items[event.key === "Home" ? 0 : items.length - 1]?.focus();
    }
  };
  const openProfile = () => { setOpen(false); onOpenProfile(); };
  const signOut = async () => {
    if (!authenticationGate || signingOut) return;
    setSigningOut(true);
    setError(null);
    try {
      authenticationGate.acceptAuthenticationStatus(await logoutAuthentication());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign out.");
      setSigningOut(false);
    }
  };

  return <div className="account-menu" ref={root}>
    <button
      ref={trigger}
      type="button"
      className="account-menu-trigger"
      aria-label={`Account menu for ${username}`}
      aria-haspopup="menu"
      aria-expanded={open}
      aria-controls={open ? menuId : undefined}
      onClick={() => setOpen((value) => !value)}
      onKeyDown={(event) => { if (event.key === "ArrowDown" && !open) { event.preventDefault(); setOpen(true); } }}
    ><AccountAvatar account={identity} /></button>
    {open && <div ref={menu} id={menuId} className="account-menu-popup" role="menu" aria-label={`Account: ${username}`} onKeyDown={onMenuKeyDown}>
      <p className="account-menu-name" role="none">{username}</p>
      <button type="button" role="menuitem" onClick={openProfile}>My Profile</button>
      <button type="button" role="menuitem" onClick={() => void signOut()} disabled={signingOut}>{signingOut ? "Signing out…" : "Sign Out"}</button>
      {error && <p className="account-menu-error" role="alert">{error}</p>}
    </div>}
  </div>;
}
