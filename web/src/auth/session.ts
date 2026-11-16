import type { AuthenticationStatus, PrincipalSummary, SignInOptions } from "../api/api-system-types";
import { knownCapabilities, type Capability } from "./capabilities";

export type UserId = number & { readonly __brand: "UserId" };
export type PermissionRevision = number & { readonly __brand: "PermissionRevision" };

/** The signed-in account as the browser may rely on it. */
export interface Principal {
  userId: UserId;
  revision: PermissionRevision;
  role: PrincipalSummary["role"];
  linkState: PrincipalSummary["link_state"];
  displayName: string;
  capabilities: ReadonlySet<Capability>;
}

/**
 * The browser's whole authentication state. Every server status parses into
 * exactly one of these; the protected shell renders only for `signed_in`.
 */
export type Session =
  | { kind: "setup_required" }
  | { kind: "signed_out"; signIn: SignInOptions }
  | { kind: "signed_in"; principal: Principal };

const SIGNED_OUT_OPTIONS: SignInOptions = { jellyfin: false };

/**
 * Turn a status response into a `Session`. An authenticated response without a
 * capability payload or CSRF token is treated as signed out, never as signed in.
 */
export function parseSession(status: AuthenticationStatus): Session {
  if (status.state === "setup_required") return { kind: "setup_required" };
  const access = status.access;
  if (status.state !== "authenticated" || !access || typeof status.csrf_token !== "string") {
    return { kind: "signed_out", signIn: status.sign_in ?? SIGNED_OUT_OPTIONS };
  }
  const principal: Principal = {
    userId: access.principal.user_id as UserId,
    revision: access.permission_revision as PermissionRevision,
    role: access.principal.role,
    linkState: access.principal.link_state,
    displayName: status.display_name || "Signed-in user",
    capabilities: knownCapabilities(access.capabilities),
  };
  return { kind: "signed_in", principal };
}

export function sessionPrincipal(session: Session): Principal | null {
  return session.kind === "signed_in" ? session.principal : null;
}

/** Identity and permission revision; private browser state lives only as long as this key. */
export function sessionKey(session: Session): string | null {
  const principal = sessionPrincipal(session);
  return principal ? `${principal.userId}:${principal.revision}` : null;
}

/**
 * A background status for the same account with an older permission revision
 * arrived after a newer one and must not reopen controls the newer one hid.
 */
export function isOutdated(current: Session, next: Session): boolean {
  const before = sessionPrincipal(current);
  const after = sessionPrincipal(next);
  return before !== null && after !== null && before.userId === after.userId && after.revision < before.revision;
}

export function can(principal: Principal, capability: Capability): boolean {
  return principal.capabilities.has(capability);
}
