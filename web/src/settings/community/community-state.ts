import { useEffect, useRef, useState } from "react";
import { CommunityApiError, fetchCommunityConnection, fetchLocalCommunityConnection } from "../../api/api-community";
import type { CommunityConnection, CommunityRequest } from "../../api/api-community";
import { useCommunityLifetime } from "./community-access";

export function communityError(reason: unknown): CommunityApiError {
  return reason instanceof CommunityApiError ? reason : new CommunityApiError("network_unavailable", true, 0);
}

export function failureMessage(code: string): string {
  const messages: Record<string, string> = {
    membership_ineligible: "Discord membership is no longer eligible. Community access is paused.",
    membership_unavailable: "Discord membership could not be verified. Community access is paused.",
    key_inactive: "This signing key is no longer active. Community access is paused.",
    recovery_required: "This installation requires identity recovery. No signing key has been replaced.",
    identity_incomplete: "The signing identity is incomplete. Restore a matching identity backup.",
    identity_corrupt: "The signing identity does not match its saved state. Restore a matching identity backup.",
    identity_unavailable: "The private signing identity could not be read.",
    identity_pending: "The saved identity changed. Refresh before retrying.",
    invalid_endpoint: "The configured community endpoint or audience is invalid.",
    wrong_service: "This identity belongs to another community service.",
    identity_conflict: "The saved community identity conflicts with the local connection.",
    unexpected_redirect: "The community service returned an unexpected redirect. No authority was forwarded.",
    invalid_signature: "The request signature was rejected. Check the installation clock before retrying.",
    invalid_enrollment_proof: "The Discord handoff could not be verified. Check its reference or cancel enrollment.",
    enrollment_expired: "The enrollment has expired. Start a new Discord authorization.",
    handoff_conflict: "A different Discord handoff is already pending for this installation.",
    display_name_taken: "That contributor name is already in use.",
    invalid_display_name: "Use 3-40 characters without control characters for your contributor name.",
    operation_conflict: "A different name change is pending. Retry or discard the saved change first.",
    network_unavailable: "The community service is unreachable. Saved operations are retained.",
    service_unavailable: "The community service is temporarily unavailable. Saved operations are retained.",
    service_paused: "The community service is paused.",
    not_configured: "Community service is not configured.",
    authentication_required: "Sign in to manage your community access.",
    capability_denied: "Your community permissions changed. Refresh before continuing.",
    not_found: "This account or activity is no longer available.",
    protected_account: "This account cannot be managed with your current permissions.",
    csrf_failed: "Local session verification failed. Sign in again before retrying.",
    stale_profile: "The service returned an older profile. The saved name has not been changed.",
    rotation_pending: "A signing key rotation is pending. Check its outcome before changing this identity.",
    rotation_not_pending: "No saved key rotation is available. Refresh the connection.",
    invalid_rotation_proof: "The saved rotation proof was rejected. The replacement has been retained.",
    key_operation_conflict: "The key rotation conflicts with the current service state. The replacement has been retained.",
    recovery_pending: "Discord-assisted key recovery is pending. Check its outcome before changing this identity.",
    recovery_expired: "The Discord recovery authorization expired. The saved replacement is retained.",
    recovery_conflict: "The affected key changed on the service. The saved replacement is retained; check the existing operation before retrying.",
    recovery_ownership_required: "Authorize with the original Discord account that owns this installation.",
    invalid_recovery: "The recovery handoff could not be verified. The saved replacement is retained.",
    recovery_not_pending: "No confirmed Discord recovery handoff is saved.",
  };
  return messages[code] ?? "The community request could not be completed. Saved operations are retained.";
}

export const blocksIdentity = (code?: string) => Boolean(code && (
  code.startsWith("identity_") || ["key_inactive", "recovery_required", "wrong_service", "invalid_endpoint"].includes(code)
));

export function handoffState(value: string, serviceUrl?: string): string | null {
  const text = value.trim();
  if (/^[0-9a-f]{64}$/.test(text)) return text;
  try {
    const url = new URL(text);
    if (!serviceUrl || url.origin !== serviceUrl || url.username || url.password || url.hash
      || !["/v1/discord/confirm", "/v1/discord/callback"].includes(url.pathname)
      || url.searchParams.getAll("state").length !== 1) return null;
    const state = url.searchParams.get("state");
    return state && /^[0-9a-f]{64}$/.test(state) ? state : null;
  } catch { return null; }
}

export function useCommunityConnection(enabled: boolean, userId?: number) {
  const [connection, setConnection] = useState<CommunityConnection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CommunityApiError | null>(null);
  const [verified, setVerified] = useState(false);
  const active = useRef(true);
  const running = useRef(false);
  const lifetime = useCommunityLifetime();
  const current = () => active.current && lifetime.current();
  const options = (): CommunityRequest => ({ userId, signal: lifetime.signal() });

  function accept(value: CommunityConnection, verified: boolean) {
    if (!current()) return;
    setConnection(value); setVerified(verified); setError(null);
  }

  function refuse(reason: unknown) {
    if (!current()) return null;
    const failure = communityError(reason);
    if ([403, 404].includes(failure.status)) {
      setConnection(null);
      lifetime.refresh();
    }
    setError(failure); setVerified(false);
    return failure;
  }

  async function reload(signal?: AbortSignal) {
    if (!current()) return;
    const local = await fetchLocalCommunityConnection(signal, options());
    if (!current() || signal?.aborted) return;
    accept(local, false);
    if (local.connected && !local.pendingRotation && !local.pendingRecovery) {
      const value = await fetchCommunityConnection(signal, options());
      if (current() && !signal?.aborted) accept(value, true);
    }
  }

  useEffect(() => {
    if (!enabled) {
      active.current = false;
      setConnection(null); setError(null); setVerified(false); setBusy(false);
      return;
    }
    active.current = true;
    const controller = new AbortController();
    setBusy(true);
    void reload(controller.signal).catch((reason: unknown) => {
      if (!controller.signal.aborted) refuse(reason);
    }).finally(() => { if (current() && !controller.signal.aborted) setBusy(false); });
    return () => { active.current = false; controller.abort(); };
  }, [enabled, userId]);

  async function run(operation: (options?: CommunityRequest) => Promise<CommunityConnection>, verified = false) {
    if (!enabled || !current() || running.current) return;
    running.current = true;
    setBusy(true); setError(null);
    try { accept(await operation(options()), verified); }
    catch (reason) {
      const failure = refuse(reason);
      if (!failure || [403, 404].includes(failure.status)) return;
      try {
        accept(await fetchLocalCommunityConnection(lifetime.signal(), options()), false);
        if (current()) setError(failure);
      } catch (reconciliationFailure) { refuse(reconciliationFailure); }
    } finally {
      running.current = false;
      if (current()) setBusy(false);
    }
  }

  async function refresh() {
    if (!enabled || !current() || running.current) return;
    running.current = true;
    setBusy(true); setError(null);
    try { await reload(lifetime.signal()); }
    catch (reason) { refuse(reason); }
    finally { running.current = false; if (current()) setBusy(false); }
  }

  return { connection, busy, error, verified, run, refresh };
}