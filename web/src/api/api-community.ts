import { apiRequest } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export type CommunityProfile = {
  contributorId: string;
  shortId: string;
  installationId: string;
  keyId: string;
  keyState: "active";
  displayName: string;
  profileRevision: number;
  policyVersion: string;
  protocolVersion: 1;
  allowedOperations: ("identity:read" | "identity:rename")[];
};

export type CommunityConnection = {
  configured: boolean;
  serviceUrl?: string;
  identity?: { installationId: string; keyId: string; lastKnownKeyState: string };
  connected: boolean;
  profile: CommunityProfile | null;
  pending: { stateId: string | null; expiresAt: number; awaitingConfirmation: true } | null;
  pendingRename?: { operation_id: string; display_name: string } | null;
  pendingRotation?: { operationId: string; newKeyId: string; state: "pending" } | null;
  rotationCompleted?: { operationId: string; keyId: string; committedAt: number } | null;
  rotationStatus?: "not_committed";
  authorizationUrl?: string;
  pendingRecovery?: boolean;
};

export type CommunityRecoveryStatus = {
  configured: boolean;
  serviceUrl?: string;
  identity: { installationId: string; keyId: string; contributorId: string | null } | null;
  pending: { operationId: string; stateId: string | null; oldKeyId: string; newKeyId: string } | null;
  completed: { operationId: string; stateId: string | null; oldKeyId: string; newKeyId: string } | null;
  authorizationUrl?: string;
  recoveryStatus?: "not_committed";
};

export class CommunityApiError extends Error {
  constructor(readonly code: string, readonly retryable: boolean, readonly status: number) {
    super(code);
    this.name = "CommunityApiError";
  }
}

export type CommunityRequest = { userId?: number; signal?: AbortSignal };

async function request<Result = CommunityConnection>(path: string, init?: RequestInit, recovery = false, options: CommunityRequest = {}): Promise<Result> {
  const base = options.userId === undefined ? "/community" : `/users/${options.userId}/community`;
  const url = `${runtimeApiBasePath()}${base}/${path}`;
  let response: Response;
  try {
    response = await apiRequest(url, { ...init, signal: options.signal ?? init?.signal });
  } catch (error) {
    if (typeof error === "object" && error !== null && "name" in error && error.name === "AbortError") throw error;
    throw new CommunityApiError("network_unavailable", true, 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new CommunityApiError(typeof body?.code === "string" ? body.code : "remote_error",
      body?.retryable === true, response.status);
  }
  if (!body || typeof body.configured !== "boolean" || (!recovery && typeof body.connected !== "boolean")) {
    throw new CommunityApiError("invalid_response", false, response.status);
  }
  return body as Result;
}

export const fetchCommunityConnection = (signal?: AbortSignal, options?: CommunityRequest) => request("connection", { signal }, false, options);
export const fetchLocalCommunityConnection = (signal?: AbortSignal, options?: CommunityRequest) => request("connection/status", { signal }, false, options);
export const fetchCommunityProfile = (signal?: AbortSignal, options?: CommunityRequest) => request("profile", { signal }, false, options);
export const startCommunityEnrollment = (options?: CommunityRequest) => request("enrollment/start", { method: "POST" }, false, options);
export const cancelCommunityEnrollment = (options?: CommunityRequest) => request("enrollment", { method: "DELETE" }, false, options);
export const disconnectCommunity = (options?: CommunityRequest) => request("connection", { method: "DELETE" }, false, options);
export const cancelCommunityProfileUpdate = (options?: CommunityRequest) => request("profile", { method: "DELETE" }, false, options);
export const completeCommunityEnrollment = (stateId: string, options?: CommunityRequest) => request("enrollment/complete", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ state_id: stateId, confirmed: true }),
}, false, options);
export const renameCommunityProfile = (displayName: string, operationId: string, options?: CommunityRequest) => request("profile", {
  method: "PATCH", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ display_name: displayName, operation_id: operationId }),
}, false, options);

export const rotateCommunityKey = (options?: CommunityRequest) => request("keys/rotate", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }),
}, false, options);
export const reconcileCommunityKey = (options?: CommunityRequest) => request("keys/rotation-status", { method: "POST" }, false, options);
export const retryCommunityKey = (options?: CommunityRequest) => request("keys/rotation-retry", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }),
}, false, options);

export const fetchCommunityRecovery = (signal?: AbortSignal, options?: CommunityRequest) => request<CommunityRecoveryStatus>("keys/recovery", { signal }, true, options);
export const startCommunityRecovery = (oldKeyId: string, options?: CommunityRequest) => request<CommunityRecoveryStatus>("keys/recovery/start", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true, old_key_id: oldKeyId }),
}, true, options);
export const completeCommunityRecovery = (stateId: string, options?: CommunityRequest) => request<CommunityRecoveryStatus>("keys/recovery/complete", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true, state_id: stateId }),
}, true, options);
export const checkCommunityRecovery = (options?: CommunityRequest) => request<CommunityRecoveryStatus>("keys/recovery/check", { method: "POST" }, true, options);
export const retryCommunityRecovery = (options?: CommunityRequest) => request<CommunityRecoveryStatus>("keys/recovery/retry", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }),
}, true, options);