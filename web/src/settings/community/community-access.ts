import { useEffect, useRef } from "react";
import type { AccountSummary } from "../../api/api-users";
import { useCan, useOptionalAuthenticationGate, usePrincipal } from "../../auth/auth-gate";

export function useCommunityAccount(target?: AccountSummary) {
  const principal = usePrincipal();
  const other = target !== undefined && target.id !== principal?.userId;
  const mayManageAdmins = useCan("accounts.admins.manage");
  const protectedTarget = other && (target.role === "owner" || target.role === "admin"
    && (principal?.role !== "owner" || !mayManageAdmins));
  const suffix = other ? "other" : "own";
  const view = useCan(`community.identity.view.${suffix}`);
  const enroll = useCan(`community.identity.enroll.${suffix}`);
  const profile = useCan(`community.identity.profile.${suffix}`);
  const keys = useCan(`community.identity.keys.${suffix}`);
  const disconnect = useCan(`community.identity.disconnect.${suffix}`);
  return { allowed: !protectedTarget, view, enroll, profile, keys, disconnect,
    userId: other ? target.id : undefined, other };
}

export function useCommunityLifetime() {
  const gate = useOptionalAuthenticationGate();
  const controller = useRef(new AbortController());
  useEffect(() => {
    if (controller.current.signal.aborted) controller.current = new AbortController();
    return () => controller.current.abort();
  }, []);
  const current = () => !controller.current.signal.aborted && gate?.isAuthenticationCurrent?.() !== false;
  return {
    current,
    signal: () => controller.current.signal,
    refresh: () => { if (current()) gate?.refreshAuthenticationStatus?.(); },
  };
}