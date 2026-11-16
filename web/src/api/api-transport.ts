import type {
  ReviewRepairSummary,
  McfMetadataSeedCandidate,
  McfImportIssue,
  ReviewErrorDetail,
} from "./api-types";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { observeMediaRequest } from "./media-access";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * Authentication transitions observed on API responses. `setup_required` and
 * `unauthenticated` unmount the protected shell; `refresh` asks the gate to reread
 * the session because permissions or the credential state may have changed.
 */
export type AuthenticationState = "setup_required" | "unauthenticated";
export type AuthenticationSignal = AuthenticationState | "refresh";

/** 403 codes that end this browser's session rather than refuse one action. */
const SESSION_ENDING_CODES = new Set(["account_disabled"]);
/** Responses after which the signed-in account's capabilities may be out of date. */
const REFRESH_CODES = new Set(["capability_denied", "permission_revision_stale"]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly lineNumber: number | null;
  readonly lineText: string | null;
  readonly references: unknown | null;
  readonly filterIds: string[];
  readonly documentFields: string[];
  readonly metadataCandidates: McfMetadataSeedCandidate[];
  readonly importIssues: McfImportIssue[];
  readonly retryAfterSeconds: number | null;
  readonly repair: ReviewRepairSummary | null;
  readonly reason: string | null;

  constructor(
    message: string,
    status: number,
    code?: string,
    lineNumber: number | null = null,
    lineText: string | null = null,
    references: unknown | null = null,
    filterIds: string[] = [],
    documentFields: string[] = [],
    metadataCandidates: McfMetadataSeedCandidate[] = [],
    importIssues: McfImportIssue[] = [],
    retryAfterSeconds: number | null = null,
    repair: ReviewRepairSummary | null = null,
    reason: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.lineNumber = lineNumber;
    this.lineText = lineText;
    this.references = references;
    this.filterIds = filterIds;
    this.documentFields = documentFields;
    this.metadataCandidates = metadataCandidates;
    this.importIssues = importIssues;
    this.retryAfterSeconds = retryAfterSeconds;
    this.repair = repair;
    this.reason = reason;
  }
}

export class AuthenticationStateError extends ApiError {
  readonly authenticationState: AuthenticationState;

  constructor(
    message: string,
    status: number,
    authenticationState: AuthenticationState,
    code?: string,
  ) {
    super(message, status, code);
    this.name = "AuthenticationStateError";
    this.authenticationState = authenticationState;
  }
}

type AuthenticationStateListener = (signal: AuthenticationSignal) => void;

const authenticationStateListeners = new Set<AuthenticationStateListener>();
let csrfToken: string | null = null;

/**
 * Subscribe to the shared authentication boundary. The token deliberately remains
 * module-private and in memory only; callers receive only the signal.
 */
export function subscribeToAuthenticationState(
  listener: AuthenticationStateListener,
): () => void {
  authenticationStateListeners.add(listener);
  return () => authenticationStateListeners.delete(listener);
}

function notifyAuthenticationState(signal: AuthenticationSignal): void {
  if (signal !== "refresh") csrfToken = null;
  for (const listener of authenticationStateListeners) listener(signal);
}

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

function requestUrl(input: RequestInfo | URL): URL {
  return input instanceof Request
    ? new URL(input.url, window.location.origin)
    : new URL(String(input), window.location.origin);
}

function requestMethod(input: RequestInfo | URL, init: RequestInit): string {
  return init.method ?? (input instanceof Request ? input.method : "GET");
}

function isUnsafeRequest(input: RequestInfo | URL, init: RequestInit): boolean {
  return !SAFE_METHODS.has(requestMethod(input, init).toUpperCase());
}

function isSameOriginApiRequest(input: RequestInfo | URL): boolean {
  const url = requestUrl(input);
  const apiBasePath = runtimeApiBasePath();
  return (
    url.origin === window.location.origin
    && (url.pathname === apiBasePath || url.pathname.startsWith(`${apiBasePath}/`))
  );
}

function isBootstrapAuthenticationRequest(input: RequestInfo | URL): boolean {
  const { pathname } = requestUrl(input);
  const apiBasePath = runtimeApiBasePath();
  return pathname === `${apiBasePath}/auth/setup`;
}

/**
 * Issue every browser request with same-origin credentials and, for a current forms
 * session, the exact CSRF token issued by the server. Relative application API URLs use Vite's
 * development proxy and never expose credentials or tokens in a URL.
 */
export async function apiRequest(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers ?? (input instanceof Request ? input.headers : undefined));
  if (
    csrfToken !== null
    && isUnsafeRequest(input, init)
    && isSameOriginApiRequest(input)
    && !isBootstrapAuthenticationRequest(input)
  ) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const signedIn = csrfToken !== null;
  const observeMedia = observeMediaRequest(requestUrl(input), { ...init, method: requestMethod(input, init) });
  const response = await globalThis.fetch(input, { ...init, headers, credentials: "same-origin" });
  await observeMedia(response);
  // A Jellyfin sign-in refusal (`invalid_credentials`) is a form error, not evidence that
  // a session expired. All other structured 401 responses remain the single
  // authoritative session-expiry boundary.
  if (response.status === 401) {
    const body = await response.clone().json().catch(() => null) as { code?: unknown } | null;
    if (body?.code !== "invalid_credentials") notifyAuthenticationState("unauthenticated");
  }
  if (response.status === 423) notifyAuthenticationState("setup_required");
  if (signedIn && (response.status === 403 || response.status === 409)) {
    const body = await response.clone().json().catch(() => null) as { code?: unknown } | null;
    const code = typeof body?.code === "string" ? body.code : "";
    if (response.status === 403 && SESSION_ENDING_CODES.has(code)) notifyAuthenticationState("unauthenticated");
    else if (REFRESH_CODES.has(code)) notifyAuthenticationState("refresh");
  }
  return response;
}

/** Convert an API error response into a structured, user-facing error.
 * @param response Failed HTTP response.
 * @param fallback Message when the response has no usable detail.
 */
export async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => ({}));
  const errorBody = body !== null && typeof body === "object" ? body as {
    detail?: unknown;
    code?: unknown;
    repair?: ReviewRepairSummary | null;
  } : {};
  const structuredDetail = errorBody.detail !== null && typeof errorBody.detail === "object"
    ? errorBody.detail as Partial<ReviewErrorDetail>
    : null;
  const detailMessage = typeof errorBody.detail === "string"
    ? errorBody.detail
    : typeof structuredDetail?.message === "string"
      ? structuredDetail.message
      : fallback;
  const code = typeof structuredDetail?.code === "string"
    ? structuredDetail.code
    : typeof errorBody.code === "string" ? errorBody.code : undefined;
  const lineNumber = typeof structuredDetail?.line_number === "number"
    ? structuredDetail.line_number
    : null;
  const lineText = typeof structuredDetail?.line_text === "string"
    ? structuredDetail.line_text
    : null;
  const detail = lineNumber === null ? detailMessage : `${detailMessage} (line ${lineNumber})`;
  const references = structuredDetail?.references ?? null;
  const retryHeader = response.headers.get("Retry-After")?.trim() ?? "";
  const retrySeconds = /^\d+$/.test(retryHeader) ? Number(retryHeader) : NaN;
  if (response.status === 401 || response.status === 423) {
    return new AuthenticationStateError(
      detail,
      response.status,
      response.status === 423 ? "setup_required" : "unauthenticated",
      code,
    );
  }
  return new ApiError(
    detail,
    response.status,
    code,
    lineNumber,
    lineText,
    references,
    Array.isArray(structuredDetail?.filter_ids) ? structuredDetail.filter_ids : [],
    Array.isArray(structuredDetail?.document_fields) ? structuredDetail.document_fields : [],
    Array.isArray(structuredDetail?.metadata_candidates) ? structuredDetail.metadata_candidates : [],
    Array.isArray(structuredDetail?.import_issues) ? structuredDetail.import_issues : [],
    Number.isSafeInteger(retrySeconds) ? retrySeconds : null,
    structuredDetail?.repair ?? errorBody.repair ?? null,
    typeof structuredDetail?.reason === "string" ? structuredDetail.reason : null,
  );
}


export async function jsonApi<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await apiRequest(path, init);
  if (!response.ok) throw await responseError(response, fallback);
  return response.status === 204 ? undefined as T : response.json();
}
