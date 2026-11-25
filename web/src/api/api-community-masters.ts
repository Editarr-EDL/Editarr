import { CommunityApiError } from "./api-community";
import { apiRequest } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import type {
  ActiveMasterFilters, AliasResponse, AlignmentPreview, AlignmentPreviewRequest, FeedbackResult, FindLabel,
  FindResponse, MasterLink, MasterLinkSummary, MasterPreferences, MasterPreferencesResult, MasterTarget,
  MasterView, ReportRequest, VoteValue,
} from "./api-community-master-types";

/**
 * A community route failure carrying the optional body details: `reason` (vote_ineligible), `mergedInto`
 * (version_merged), `retryAfter` seconds (rate_limited) and the messages of a 422 validation list.
 */
export class MasterApiError extends CommunityApiError {
  constructor(code: string, retryable: boolean, status: number,
    readonly reason: string | null = null, readonly mergedInto: string | null = null,
    readonly retryAfter: number | null = null, readonly validation: string[] = []) {
    super(code, retryable, status);
    this.name = "MasterApiError";
  }
}

/** Convert an error response into a `MasterApiError`; 422 lists become `validation_failed` and bare 404s `not_found`. */
function responseError(status: number, body: unknown, headers: Headers) {
  const value = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  if (status === 422 && Array.isArray(value.detail)) {
    const messages = value.detail.map((item) => typeof item?.msg === "string" ? item.msg.replace(/^Value error, /, "") : null)
      .filter((message): message is string => !!message);
    return new MasterApiError("validation_failed", false, status, null, null, null, messages);
  }
  const fallback = status === 404 ? "not_found" : status === 429 ? "rate_limited" : "remote_error";
  const retryAfter = typeof value.retryAfter === "number" ? value.retryAfter : Number(headers.get("Retry-After")) || null;
  return new MasterApiError(typeof value.code === "string" ? value.code : fallback, value.retryable === true || status === 429,
    status, typeof value.reason === "string" ? value.reason : null,
    typeof value.mergedInto === "string" ? value.mergedInto : null, status === 429 ? retryAfter : null);
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
  if (!response.ok) throw responseError(response.status, body, response.headers);
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

/** The link's in-effect filters on the local timeline now and their merged Simple EDL. `clipped: false`
 * returns them unclipped, as saved-review snapshots store them. */
export const fetchMasterActiveFilters = (linkId: string, signal?: AbortSignal, { clipped = true } = {}) =>
  communityJson<ActiveMasterFilters>(`${link(linkId)}/active-filters${clipped ? "" : "?clipped=false"}`, { signal });

export const previewMasterAlignment = (linkId: string, request: AlignmentPreviewRequest, signal?: AbortSignal) =>
  communityJson<AlignmentPreview>(`${link(linkId)}/alignment/preview`, { ...postJson(request), signal });

export const approveMasterAlignment = (linkId: string, token: string, forRevision: number, signal?: AbortSignal) =>
  communityJson<{ version: 1; link: MasterLink }>(`${link(linkId)}/alignment/approve`,
    { ...postJson({ token, for_revision: forRevision, confirmed: true }), signal });

export const fetchMasterPreferences = (signal?: AbortSignal) => communityJson<MasterPreferencesResult>("preferences", { signal });

export const saveMasterPreferences = (preferences: MasterPreferences, signal?: AbortSignal) =>
  communityJson<MasterPreferencesResult>("preferences", { ...postJson({
    confidence_threshold: preferences.confidenceThreshold, show_low_confidence: preferences.showLowConfidence,
    hide_downvoted: !!preferences.hideDownvoted,
  }, "PATCH"), signal });

/** Send a per-filter vote. Use `retry` with the same operation ID after a retryable failure. */
export const voteMasterFilter = (linkId: string, filterId: string, request: { vote: VoteValue; operationId: string },
  retry = false, signal?: AbortSignal) => communityJson<FeedbackResult>(
  `${filter(linkId, filterId)}/vote${retry ? "/retry" : ""}`, { ...postJson(retry
    ? { operation_id: request.operationId, confirmed: true }
    : { vote: request.vote, operation_id: request.operationId, confirmed: true }), signal });

/** Report the link's version or one of its filters; a blank note is left out. */
export const reportCommunityMaster = (linkId: string, { note, ...report }: ReportRequest, signal?: AbortSignal) =>
  communityJson<FeedbackResult>(`${link(linkId)}/reports`, { ...postJson({ ...report,
    ...(note?.trim() ? { note: note.trim() } : {}), confirmed: true }), signal });
