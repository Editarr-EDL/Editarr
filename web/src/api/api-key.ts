import { apiRequest, responseError } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export interface ApiKeyStatus {
  configured: boolean;
  created_at: string | null;
  revealable: boolean;
}

export interface ApiKeySecret {
  api_key: string;
  created_at: string;
}

async function send<T>(path: string, method: string, password?: string): Promise<T> {
  const response = await apiRequest(`${runtimeApiBasePath()}/auth/api-key${path}`, {
    method,
    cache: "no-store",
    ...(password === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reauthentication: { password } }),
    }),
  });
  if (!response.ok) throw await responseError(response, "Unable to manage the API key.");
  return response.json() as Promise<T>;
}

/** Read the signed-in user's key metadata; the key itself is never part of it. */
export const apiKeyStatus = () => send<ApiKeyStatus>("", "GET");
/** Show the current key after Jellyfin confirms the account's password in the same request. */
export const revealApiKey = (password: string) => send<ApiKeySecret>("/reveal", "POST", password);
/** Create or replace the key after Jellyfin confirms the password; the old key stops working. */
export const issueApiKey = (password: string) => send<ApiKeySecret>("", "POST", password);
/** Revoke the key without affecting browser sessions. */
export const revokeApiKey = () => send<ApiKeyStatus>("", "DELETE");
