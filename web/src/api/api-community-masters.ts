import { CommunityApiError } from "./api-community";
import { apiRequest } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import type {
  AliasResponse, AlignmentPreview, AlignmentPreviewRequest, FilterDecision, FindLabel, FindResponse, MasterFilter,
  MasterLink, MasterLinkSummary, MasterPreferences, MasterTarget, MasterView, ReportRequest, VoteValue,
} from "./api-community-master-types";

/** A community route failure carrying the optional `reason` and `mergedInto` details from the body. */
export class MasterApiError extends CommunityApiError {
  constructor(code: string, retryable: boolean, status: number,
    readonly reason: string | null = null, readonly mergedInto: string | null = null) {
    super(code, retryable, status);
    this.name = "MasterApiError";
  }
}

/**
 * Call a local `/api/community/*` route and return its versioned JSON body, or undefined for 204 No
 * Content. Transport failures become a retryable `network_unavailable` error so they never read as
 * an empty result; aborts propagate.
 */
export async function communityJson<Result>(path: string, init: RequestInit = {}): Promise<Result> {
  let response: Response;
  try {
    response = await apiRequest(`${runtimeApiBasePath()}/community/${path}`, { cache: "no-store", ...init });
  } catch (reason) {
    if (reason instanceof Error && reason.name === "AbortError") throw reason;
    throw new MasterApiError("network_unavailable", true, 0);
  }
  if (response.ok && (response.status === 204 || response.headers.get("Content-Length") === "0")) return undefined as Result;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new MasterApiError(typeof body?.code === "string" ? body.code : "remote_error", body?.retryable === true,
      response.status, typeof body?.reason === "string" ? body.reason : null,
      typeof body?.mergedInto === "string" ? body.mergedInto : null);
  }
  if (!body || typeof body !== "object" || Array.isArray(body) || ("version" in body && body.version !== 1)) {
    throw new MasterApiError("invalid_response", false, response.status);
  }
  return body as Result;
}

export const postJson = (body: unknown, method = "POST"): RequestInit =>
  ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const link = (linkId: string) => `masters/${encodeURIComponent(linkId)}`;
const filter = (linkId: string, filterId: string) => `${link(linkId)}/filters/${encodeURIComponent(filterId)}`;

export const findCommunityVersions = (target: MasterTarget, label: FindLabel, refresh: boolean, signal?: AbortSignal) =>
  communityJson<FindResponse>("find", { ...postJson({ target, label, refresh }), signal });

export const fetchVersionAliases = (signal?: AbortSignal) => communityJson<AliasResponse>("aliases", { signal });

/** Download, verify and link a community version. `replace` confirms switching an existing link's version. */
export const applyCommunityMaster = (request: {
  target: MasterTarget; findKey: string; communityVersionId: string; replace?: boolean;
}, signal?: AbortSignal) => communityJson<{ version: 1; link: MasterLink }>("masters", {
  ...postJson({ target: request.target, find_key: request.findKey, community_version_id: request.communityVersionId,
    confirmed: true, ...(request.replace ? { replace: true } : {}) }), signal });

export function listCommunityMasters(itemId: string, mediaSourceId: string | null, signal?: AbortSignal) {
  const query = new URLSearchParams({ item_id: itemId });
  if (mediaSourceId) query.set("media_source_id", mediaSourceId);
  return communityJson<{ version: 1; links: MasterLinkSummary[] }>(`masters?${query}`, { signal });
}

export const fetchCommunityMaster = (linkId: string, signal?: AbortSignal) =>
  communityJson<MasterView>(link(linkId), { signal });

export const previewMasterAlignment = (linkId: string, request: AlignmentPreviewRequest, signal?: AbortSignal) =>
  communityJson<AlignmentPreview>(`${link(linkId)}/alignment/preview`, { ...postJson(request), signal });

export const approveMasterAlignment = (linkId: string, token: string, forRevision: number, signal?: AbortSignal) =>
  communityJson<{ version: 1; link: MasterLink }>(`${link(linkId)}/alignment/approve`,
    { ...postJson({ token, for_revision: forRevision, confirmed: true }), signal });

export const decideMasterFilter = (linkId: string, filterId: string, decision: FilterDecision, signal?: AbortSignal) =>
  communityJson<MasterFilter>(filter(linkId, filterId), { ...postJson(decision, "PATCH"), signal });

export const fetchMasterPreferences = (signal?: AbortSignal) => communityJson<MasterPreferences>("preferences", { signal });

export const saveMasterPreferences = (preferences: MasterPreferences, signal?: AbortSignal) =>
  communityJson<MasterPreferences>("preferences", { ...postJson({
    confidence_threshold: preferences.confidenceThreshold, show_low_confidence: preferences.showLowConfidence,
  }, "PATCH"), signal });

/** Send a per-filter vote. Use `retry` with the same operation ID after a retryable failure. */
export const voteMasterFilter = (linkId: string, filterId: string, request: { vote: VoteValue; operationId: string },
  retry = false, signal?: AbortSignal) => communityJson<{ version: 1; state: "completed"; result: unknown }>(
  `${filter(linkId, filterId)}/vote${retry ? "/retry" : ""}`, { ...postJson(retry
    ? { operation_id: request.operationId, confirmed: true }
    : { vote: request.vote, operation_id: request.operationId, confirmed: true }), signal });

export const reportCommunityMaster = (linkId: string, report: ReportRequest, signal?: AbortSignal) =>
  communityJson<Record<string, unknown>>(`${link(linkId)}/reports`, { ...postJson({ ...report, confirmed: true }), signal });
