import { apiRequest } from "./api-transport";
import { CommunityApiError, type CommunityRequest } from "./api-community";
import { runtimeApiBasePath } from "../shared/runtime-urls";

async function request(path: string, options: CommunityRequest) {
  const query = options.userId === undefined ? "" : `?user_id=${options.userId}`;
  const response = await apiRequest(`${runtimeApiBasePath()}/community/import-receipts/${path}${query}`, {
    method: "POST", signal: options.signal,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }),
  });
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new CommunityApiError(value?.code ?? "remote_error", value?.retryable === true, response.status);
  return value;
}

export async function recoverCommunityReceipts(options: CommunityRequest = {}): Promise<string[]> {
  const value = await request("recover", options);
  if (!Array.isArray(value?.importIds) || value.importIds.some((id: unknown) => typeof id !== "string")) {
    throw new CommunityApiError("invalid_response", false, 0);
  }
  return value.importIds;
}

export async function retryCommunityReceipt(importId: string, options: CommunityRequest = {}): Promise<void> {
  const value = await request(`${encodeURIComponent(importId)}/retry`, options);
  if (value?.importId !== importId || value.state !== "acknowledged") throw new CommunityApiError("invalid_response", false, 0);
}