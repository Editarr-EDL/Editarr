import { CommunityApiError } from "./api-community";
import { apiRequest } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export type Vote = "like" | "dislike" | "neutral";
export type VoteIntent = { operationId: string; vote: Vote; state: "pending" | "submitted" | "failed" };
export type Feedback = { version: 1; revisionId: string;
  scope: { serviceUrl: string; contributorId: string };
  current: { vote: Vote; eligible: boolean; reason: "self_vote" | "import_required" | null } | null;
  totals: { likes: number; dislikes: number } | null; pending: VoteIntent | null;
  error: { code: string; retryable: boolean } | null };
const isVote = (value: unknown): value is Vote => ["like", "dislike", "neutral"].includes(value as string);
const keys = (value: unknown, expected: string[]) => typeof value === "object" && value !== null
  && Object.keys(value).sort().join(",") === expected.sort().join(",");
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

async function request(revisionId: string, suffix: string, signal: AbortSignal, body?: unknown) {
  let response: Response;
  try {
    response = await apiRequest(`${runtimeApiBasePath()}/community/votes/${encodeURIComponent(revisionId)}${suffix}`, {
      method: body ? "POST" : "GET", signal, cache: "no-store",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new CommunityApiError("network_unavailable", true, 0);
  }
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new CommunityApiError(typeof value?.code === "string" ? value.code : "remote_error", value?.retryable === true, response.status);
  return value;
}

export async function readCommunityFeedback(revisionId: string, signal: AbortSignal): Promise<Feedback> {
  const value = await request(revisionId, "", signal);
  if (!keys(value, ["version", "revisionId", "scope", "current", "totals", "pending", "error"])
    || value.version !== 1 || value.revisionId !== revisionId
    || !keys(value.scope, ["serviceUrl", "contributorId"]) || typeof value.scope.serviceUrl !== "string"
    || !/^https:\/\/[^/]+$/.test(value.scope.serviceUrl) || typeof value.scope.contributorId !== "string" || !value.scope.contributorId
    || (value.current !== null && (!keys(value.current, ["vote", "eligible", "reason"]) || !isVote(value.current.vote)
      || typeof value.current.eligible !== "boolean" || (value.current.eligible ? value.current.reason !== null
        : !["self_vote", "import_required"].includes(value.current.reason))))
    || (value.totals !== null && (!keys(value.totals, ["likes", "dislikes"]) || !count(value.totals.likes) || !count(value.totals.dislikes)
      || value.totals.likes + value.totals.dislikes > Number.MAX_SAFE_INTEGER - 2))
    || (value.pending !== null && (!keys(value.pending, ["operationId", "vote", "state"])
      || !/^vote-[A-Za-z0-9_.-]{1,120}$/.test(value.pending.operationId) || !isVote(value.pending.vote)
      || !["pending", "submitted", "failed"].includes(value.pending.state)))
    || (value.error !== null && (!keys(value.error, ["code", "retryable"]) || typeof value.error.code !== "string"
      || typeof value.error.retryable !== "boolean"))
    || ((value.current === null || value.totals === null) !== (value.error !== null))
    || (value.error !== null && (value.current !== null || value.totals !== null))) {
    throw new CommunityApiError("invalid_response", false, 0);
  }
  return value;
}

export async function sendCommunityVote(revisionId: string, intent: Pick<VoteIntent, "operationId" | "vote">,
  signal: AbortSignal, retry = false): Promise<void> {
  const value = await request(revisionId, retry ? "/retry" : "", signal, {
    operation_id: intent.operationId, confirmed: true, ...(!retry ? { vote: intent.vote } : {}),
  });
  if (!keys(value, ["version", "state", "result"]) || value.version !== 1 || value.state !== "completed"
    || !keys(value.result, ["version", "operationId", "revisionId", "vote"]) || value.result.version !== 1
    || value.result.operationId !== intent.operationId || value.result.revisionId !== revisionId || value.result.vote !== intent.vote) {
    throw new CommunityApiError("invalid_response", false, 0);
  }
}