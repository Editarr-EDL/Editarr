import { CommunityApiError } from "./api-community";
import type { LookupInput, LookupTarget } from "./api-community-lookup";
import type { Scan } from "./api-scan-types";
import { apiRequest } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export type DownloadChoice = { contributionId: string; revisionId: string; artifactDigest: string; channels: ("audio" | "video")[] };
export type DownloadOutcome = { revisionId: string; status: "imported" | "partial" | "not_imported";
  scanId?: string; approvedChannels?: ("audio" | "video")[]; receiptState?: string; reviewRequired?: boolean; reviewed?: boolean };
export type DownloadResult = { version: 1; revisionId: string; status: "imported" | "reused";
  scan: Scan; receiptState: string; reviewRequired: boolean; reviewed: boolean };

async function post(path: string, body: unknown, signal: AbortSignal) {
  let response: Response;
  try {
    response = await apiRequest(`${runtimeApiBasePath()}/community/downloads${path}`, {
      method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new CommunityApiError("network_unavailable", true, 0);
  }
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new CommunityApiError(typeof value?.code === "string" ? value.code : "remote_error", value?.retryable === true, response.status);
  if (value?.version !== 1) throw new CommunityApiError("invalid_response", false, 0);
  return value;
}

export async function downloadCommunityFilter(input: LookupInput, targetKey: string, choice: DownloadChoice, confirmed: boolean, signal: AbortSignal): Promise<DownloadResult> {
  const result = await post("", { lookup: { ...input, target_key: targetKey }, choice, confirmed }, signal);
  if (result.revisionId !== choice.revisionId || !["imported", "reused"].includes(result.status)
    || typeof result.scan?.id !== "string" || typeof result.receiptState !== "string" || typeof result.reviewRequired !== "boolean" || typeof result.reviewed !== "boolean") {
    throw new CommunityApiError("invalid_response", false, 0);
  }
  return result;
}

export async function communityDownloadStatus(target: LookupTarget, targetKey: string, choices: DownloadChoice[], signal: AbortSignal): Promise<DownloadOutcome[]> {
  const result = await post("/status", { target, target_key: targetKey, choices }, signal);
  if (!Array.isArray(result.outcomes) || result.outcomes.length !== choices.length
    || result.outcomes.some((entry: DownloadOutcome, index: number) => entry.revisionId !== choices[index].revisionId
      || !["imported", "partial", "not_imported"].includes(entry.status)
      || (entry.status !== "not_imported" && (typeof entry.scanId !== "string" || typeof entry.receiptState !== "string"
        || typeof entry.reviewRequired !== "boolean" || typeof entry.reviewed !== "boolean" || !Array.isArray(entry.approvedChannels)
        || !entry.approvedChannels.length || entry.approvedChannels.some((channel) => channel !== "audio" && channel !== "video"))))) {
    throw new CommunityApiError("invalid_response", false, 0);
  }
  return result.outcomes;
}