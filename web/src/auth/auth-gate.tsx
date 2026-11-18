import {
  Fragment,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { fetchAuthenticationStatus } from "../api/api-auth";
import { setCsrfToken, subscribeToAuthenticationState } from "../api/api-transport";
import type { AuthenticationStatus, SignInOptions } from "../api/api-system-types";
import { resetPageTitle } from "../shared/display";
import { bindUserStorage } from "../shared/user-storage";
import type { Capability } from "./capabilities";
import { OperationReceipts } from "./operation-receipts";
import { can, isOutdated, parseSession, sessionKey, sessionPrincipal, type Principal, type Session, type UserId } from "./session";

type GateView = { kind: "loading" } | { kind: "error" } | Session;

export interface AuthenticationGateContextValue {
  /** The last server-confirmed status response. */
  status: AuthenticationStatus | null;
  /** The parsed session the gate is rendering. */
  session: Session | null;
  isAuthenticationCurrent: () => boolean;
  /** Accept the status returned by a setup, sign-in, sign-out, or account change. */
  acceptAuthenticationStatus: (status: AuthenticationStatus) => void;
  /** Reload the status in the foreground after a transient failure. */
  retryAuthenticationStatus: () => void;
  /** Reload the status and show `notice` on the sign-in screen it leads to. */
  continueToSignIn: (notice: string) => void;
  /** The message `continueToSignIn` left for the sign-in screen, until someone signs in. */
  signInNotice: string | null;
  /** Reread the status in the background, keeping the current view until it answers. */
  refreshAuthenticationStatus: () => void;
}

const AuthenticationGateContext = createContext<AuthenticationGateContextValue | null>(null);

/**
 * Read the current authentication boundary from setup and login views.
 * The context keeps credentials and return routes out of browser storage.
 */
export function useAuthenticationGate(): AuthenticationGateContextValue {
  const context = useContext(AuthenticationGateContext);
  if (context === null) throw new Error("useAuthenticationGate must be used within AuthenticationGate");
  return context;
}

/** Return the gate state when a view is rendered within an authentication boundary. */
export function useOptionalAuthenticationGate(): AuthenticationGateContextValue | null {
  return useContext(AuthenticationGateContext);
}

/** The signed-in account, or null outside a signed-in gate. */
export function usePrincipal(): Principal | null {
  const session = useOptionalAuthenticationGate()?.session;
  return session?.kind === "signed_in" ? session.principal : null;
}

/** Whether the signed-in account may attempt `capability`; false while signed out. */
export function useCan(capability: Capability): boolean {
  const principal = usePrincipal();
  return principal !== null && can(principal, capability);
}

/** Render `children` only for an account that holds `capability`. */
export function Can({ capability, children }: { capability: Capability; children: ReactNode }) {
  return useCan(capability) ? <>{children}</> : null;
}

/**
 * Preserve only routes that the hash-based application router can interpret. A hash
 * is never treated as a URL or a server-provided redirect target.
 */
export function safeInternalHash(hash: string): string | null {
  if (!hash.startsWith("#")) return null;
  const route = hash.slice(1);
  if (!route || route.startsWith("//") || !/^[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$/.test(route)) return null;
  const normalized = route.replace(/^\//, "");
  if (
    normalized === "settings"
    || normalized === "audit"
    || normalized.startsWith("audit/")
    || normalized.startsWith("settings/")
    || normalized.startsWith("library/")
  ) {
    return `#${route}`;
  }
  return null;
}

/** A route to reopen after sign-in, and whose it was; a null `owner` means nobody was signed in. */
interface PendingRoute {
  hash: string;
  owner: UserId | null;
}

function AuthenticationLoading() {
  return <main className="auth-gate" aria-busy="true" aria-live="polite">
    <p>Checking authentication…</p>
  </main>;
}

function AuthenticationError({ onRetry }: { onRetry: () => void }) {
  return <main className="auth-gate" aria-live="polite">
    <h1>Unable to check authentication</h1>
    <p>Check your connection and try again.</p>
    <button type="button" onClick={onRetry}>Retry</button>
  </main>;
}

function SetupRequired() {
  return <main className="auth-gate">
    <h1>Jellyfin setup required</h1>
  </main>;
}

function LoginRequired() {
  return <main className="auth-gate">
    <h1>Sign in required</h1>
  </main>;
}

const NO_SIGN_IN_OPTIONS: SignInOptions = { jellyfin: false };

/**
 * Keep protected children mounted only while the server reports a signed-in account.
 * Children remount whenever the account or its permission revision changes, so no
 * draft, query, or preview outlives the identity and permissions it was made under.
 * Completed operation receipts survive revision refreshes for the same identity.
 * @param children Protected application content.
 * @param setupRequired Optional first-run setup view.
 * @param unauthenticated Optional sign-in view.
 */
export function AuthenticationGate({
  children,
  setupRequired,
  unauthenticated,
}: {
  children: ReactNode;
  setupRequired?: ReactNode;
  unauthenticated?: ReactNode;
}) {
  const [view, setView] = useState<GateView>({ kind: "loading" });
  const [status, setStatus] = useState<AuthenticationStatus | null>(null);
  const [initialRoute] = useState<PendingRoute | null>(() => {
    const hash = safeInternalHash(window.location.hash);
    return hash === null ? null : { hash, owner: null };
  });
  const pendingRoute = useRef<PendingRoute | null>(initialRoute);
  const lastUser = useRef<UserId | null>(null);
  const current = useRef<Session | null>(null);
  const lifetime = useRef(0);
  const requestVersion = useRef(0);
  const refreshing = useRef<"idle" | "running" | "again">("idle");
  const signInOptions = useRef<SignInOptions>(NO_SIGN_IN_OPTIONS);
  const [signInNotice, setSignInNotice] = useState<string | null>(null);

  const rememberCurrentRoute = useCallback(() => {
    const hash = safeInternalHash(window.location.hash);
    pendingRoute.current = hash === null ? null : { hash, owner: lastUser.current };
  }, []);

  const showSession = useCallback((next: Session) => {
    if (next.kind !== current.current?.kind || sessionKey(next) !== (current.current && sessionKey(current.current))) {
      lifetime.current += 1;
      resetPageTitle();
    }
    bindUserStorage(sessionKey(next));
    current.current = next;
    setView(next);
  }, []);

  const applyStatus = useCallback((nextStatus: AuthenticationStatus) => {
    const next = parseSession(nextStatus);
    const principal = sessionPrincipal(next);
    if (nextStatus.sign_in) signInOptions.current = nextStatus.sign_in;
    if (principal === null && current.current !== null && sessionPrincipal(current.current) !== null) {
      rememberCurrentRoute();
    }
    if (next.kind === "signed_in") {
      setSignInNotice(null);
      const pending = pendingRoute.current;
      pendingRoute.current = null;
      if (pending !== null && (pending.owner === null || pending.owner === next.principal.userId)) {
        window.location.hash = pending.hash;
      } else if (pending !== null || (lastUser.current !== null && lastUser.current !== next.principal.userId)) {
        window.location.hash = "#/";
      }
    }
    if (principal !== null) lastUser.current = principal.userId;
    setCsrfToken(principal !== null ? nextStatus.csrf_token ?? null : null);
    setStatus(nextStatus);
    showSession(next);
  }, [rememberCurrentRoute, showSession]);

  const acceptAuthenticationStatus = useCallback((nextStatus: AuthenticationStatus) => {
    requestVersion.current += 1;
    applyStatus(nextStatus);
  }, [applyStatus]);

  const loadStatus = useCallback(() => {
    const version = ++requestVersion.current;
    setView({ kind: "loading" });
    void fetchAuthenticationStatus().then(
      (nextStatus) => {
        if (requestVersion.current === version) applyStatus(nextStatus);
      },
      () => {
        if (requestVersion.current === version) setView({ kind: "error" });
      },
    );
  }, [applyStatus]);

  const refreshAuthenticationStatus = useCallback(() => {
    if (refreshing.current !== "idle") {
      refreshing.current = "again";
      return;
    }
    refreshing.current = "running";
    const version = requestVersion.current;
    const finish = () => {
      const again = refreshing.current === "again";
      refreshing.current = "idle";
      if (again) refreshAuthenticationStatus();
    };
    void fetchAuthenticationStatus().then(
      (nextStatus) => {
        const outdated = current.current !== null && isOutdated(current.current, parseSession(nextStatus));
        if (requestVersion.current === version && !outdated) applyStatus(nextStatus);
        finish();
      },
      finish,
    );
  }, [applyStatus]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => subscribeToAuthenticationState((signal) => {
    if (signal === "refresh") {
      refreshAuthenticationStatus();
      return;
    }
    // An expiry/setup transition is authoritative. Invalidate a status request that
    // was already in flight so its older authenticated response cannot remount the
    // protected shell after the session has ended.
    requestVersion.current += 1;
    rememberCurrentRoute();
    setStatus((previous) => previous === null ? null : {
      ...previous, state: signal, setup_required: signal === "setup_required", csrf_token: null, access: null,
    });
    showSession(signal === "setup_required" ? { kind: "setup_required" } : { kind: "signed_out", signIn: signInOptions.current });
  }), [refreshAuthenticationStatus, rememberCurrentRoute, showSession]);

  const signedIn = view.kind === "signed_in";
  useEffect(() => {
    if (!signedIn) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") refreshAuthenticationStatus();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshAuthenticationStatus, signedIn]);

  useEffect(() => {
    const onHashChange = () => {
      if (!signedIn) rememberCurrentRoute();
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [rememberCurrentRoute, signedIn]);

  const session = view.kind === "loading" || view.kind === "error" ? null : view;
  const renderedLifetime = lifetime.current;
  const value: AuthenticationGateContextValue = {
    status,
    session,
    isAuthenticationCurrent: () => renderedLifetime === lifetime.current,
    acceptAuthenticationStatus: (next) => {
      if (renderedLifetime === lifetime.current) acceptAuthenticationStatus(next);
    },
    retryAuthenticationStatus: loadStatus,
    continueToSignIn: (notice) => {
      setSignInNotice(notice);
      loadStatus();
    },
    signInNotice,
    refreshAuthenticationStatus,
  };

  let content: ReactNode;
  if (view.kind === "signed_in") content = <OperationReceipts key={view.principal.userId}><Fragment key={sessionKey(view)}>{children}</Fragment></OperationReceipts>;
  else if (view.kind === "setup_required") content = setupRequired ?? <SetupRequired />;
  else if (view.kind === "signed_out") content = unauthenticated ?? <LoginRequired />;
  else if (view.kind === "error") content = <AuthenticationError onRetry={loadStatus} />;
  else content = <AuthenticationLoading />;

  return <AuthenticationGateContext.Provider value={value}>{content}</AuthenticationGateContext.Provider>;
}
