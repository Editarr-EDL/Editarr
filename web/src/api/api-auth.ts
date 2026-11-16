import type {
  AuthenticationCredentials,
  AuthenticationStatus,
  JellyfinSetupRequest,
  QuickConnectAttempt,
} from "./api-system-types";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { apiRequest, responseError } from "./api-transport";

async function authentication<T = AuthenticationStatus>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await apiRequest(`${runtimeApiBasePath()}/auth/${path}`, { ...init, cache: "no-store" });
  if (!response.ok) throw await responseError(response, fallback);
  return await response.json() as T;
}

function post(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const fetchAuthenticationStatus = () =>
  authentication("status", {}, "Unable to determine authentication status");

/** First-run setup: Editarr signs the Jellyfin administrator in and mints its own key. */
export const setupWithJellyfin = (request: JellyfinSetupRequest) =>
  authentication("setup", post(request), "Unable to set up Editarr");

/** Sign in with Jellyfin credentials; the server finds or creates the linked account. */
export const loginWithJellyfin = (credentials: AuthenticationCredentials) =>
  authentication("jellyfin/login", post(credentials), "Unable to sign in with Jellyfin");

/** Whether sign-in offers Quick Connect: Jellyfin is connected, reachable, and allows it. */
export const fetchQuickConnectAvailability = async () =>
  (await authentication<{ enabled: boolean }>("jellyfin/quick-connect", {}, "Unable to check Quick Connect")).enabled;

/** Start this browser's Quick Connect sign-in, replacing any earlier code. */
export const initiateQuickConnect = () =>
  authentication<QuickConnectAttempt>("jellyfin/quick-connect/initiate", post({}), "Unable to start Quick Connect");

/** Ask whether a Jellyfin user approved this browser's code. */
export const fetchQuickConnectStatus = () =>
  authentication<QuickConnectAttempt>("jellyfin/quick-connect/status", {}, "Unable to check Quick Connect");

/** Sign in with this browser's approved code, which can sign in once. */
export const completeQuickConnect = (request: { remember_me: boolean }) =>
  authentication("jellyfin/quick-connect/complete", post(request), "Unable to sign in with Quick Connect");

export async function cancelQuickConnect(): Promise<void> {
  const response = await apiRequest(`${runtimeApiBasePath()}/auth/jellyfin/quick-connect/cancel`, { method: "POST", cache: "no-store" });
  if (!response.ok) throw await responseError(response, "Unable to cancel Quick Connect");
}

export const logoutAuthentication = () =>
  authentication("logout", { method: "POST" }, "Unable to sign out");
