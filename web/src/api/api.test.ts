import { afterEach, describe, expect, it, vi } from "vitest";

import { signedInStatus } from "../test/app-test-fixtures";
import {
  ApiError,
  AuthenticationStateError,
  apiRequest,
  fetchAuthenticationStatus,
  fetchCapabilities,
  fetchModelStorage,
  fetchDeploymentConfiguration,
  fetchRestartCapability,
  fetchScanFindings,
  fetchScanReferences,
  loginWithJellyfin,
  logoutAuthentication,
  setCsrfToken,
  setupWithJellyfin,
  saveDeploymentConfiguration,
  requestEditarrRestart,
  prefetchModel,
  cancelModelDownload,
  removeModel,
  subscribeToAuthenticationState,
  fetchSavedReviews,
  fetchMcfRegistry,
  fetchFilterDefaultsSettings,
  updateFilterDefaultsSettings,
  applyFilterDefaults,
  fetchMcfPreflight,
  importMcfUpload,
  retimeSavedReview,
  startCreditsDetection,
  fetchCreditsDetection,
  submitCreditsModelConsent,
  acceptCreditsDetection,
  creditsDetectionClipUrl,
  fetchExportStatus,
  deleteExport,
  importEdlUpload,
  cancelScanQueueItem,
} from "../api";
import { publishReviewExport } from "./api-exports";
import { resetRuntimeUrlsForTests } from "../shared/runtime-urls";
import { SCANS_CHANGED_EVENT } from "../shared/scan-events";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const authenticatedStatus = { ...signedInStatus(), csrf_token: "csrf-token" };

function setRuntimeBootstrap(json: string): void {
  document.getElementById("editarr-bootstrap")?.remove();
  document.head.insertAdjacentHTML("beforeend", `<script id="editarr-bootstrap" type="application/json">${json}</script>`);
  resetRuntimeUrlsForTests();
}

describe("central web API boundary", () => {
  afterEach(() => {
    setCsrfToken(null);
    setRuntimeBootstrap('{"version":1,"basePath":"/","apiBasePath":"/api"}');
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses same-origin cookies, retains CSRF only in memory, and sends it only on unsafe requests", async () => {
    const request = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const localStorage = { setItem: vi.fn() };
    const sessionStorage = { setItem: vi.fn() };
    vi.stubGlobal("fetch", request);
    vi.stubGlobal("localStorage", localStorage);
    vi.stubGlobal("sessionStorage", sessionStorage);

    setCsrfToken("csrf-token");
    await apiRequest("/api/scans", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "not-the-session-token" },
    });
    await apiRequest("/api/scans");

    const unsafeInit = request.mock.calls[0][1] as RequestInit;
    const safeInit = request.mock.calls[1][1] as RequestInit;
    expect(unsafeInit.credentials).toBe("same-origin");
    expect(new Headers(unsafeInit.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(safeInit.credentials).toBe("same-origin");
    expect(new Headers(safeInit.headers).has("X-CSRF-Token")).toBe(false);
    expect(localStorage.setItem).not.toHaveBeenCalled();
    expect(sessionStorage.setItem).not.toHaveBeenCalled();
    expect(String(request.mock.calls[0][0])).not.toContain("csrf-token");
  });

  it("uses encoded review URLs, query sorting, CSRF, and browser-managed multipart headers", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ review: {}, scan: {}, warning: null }, 201));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");

    await fetchSavedReviews("item/one", {
      media_source_id: "version one",
      sort: "name",
      direction: "asc",
    });
    await importEdlUpload(new File(["1 2 1\n"], "folder/incoming.edl"), {
      name: "Imported",
      target: { source: "jellyfin", item_id: "item-1" },
    });

    expect(String(request.mock.calls[0][0])).toBe(
      "/api/library-items/item%2Fone/reviews?media_source_id=version+one&sort=name&direction=asc",
    );
    const uploadInit = request.mock.calls[1][1] as RequestInit;
    expect(new Headers(uploadInit.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(new Headers(uploadInit.headers).has("Content-Type")).toBe(false);
    expect(uploadInit.body).toBeInstanceOf(FormData);
  });

  it("uses protected MCF/default/export helpers with base paths and structured errors", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json({ families: [] }))
      .mockResolvedValueOnce(json({ revision: 0, selection_rules: [], severity_defaults: [] }))
      .mockResolvedValueOnce(json({ revision: 1, selection_rules: [], severity_defaults: [] }))
      .mockResolvedValueOnce(json({ filters: [], selection_changes: 0, severity_changes: 0, channel_changes: 0 }))
      .mockResolvedValueOnce(json({ review_id: "review/one", revision: 1, eligible: true, issues: [] }))
      .mockResolvedValueOnce(json({ format: "mcf" }))
      .mockResolvedValueOnce(json({ review: {}, scan: {}, source_version: "1.1.0", notices: [] }, 201))
      .mockResolvedValueOnce(json({ exports: {} }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(json({
        detail: {
          code: "incomplete_mcf_review",
          message: "Incomplete",
          line_number: null,
          references: null,
          filter_ids: ["filter-1"],
          document_fields: ["material_end_seconds"],
          metadata_candidates: [],
        },
      }, 422));
    vi.stubGlobal("fetch", request);
    setRuntimeBootstrap('{"version":1,"basePath":"/editarr","apiBasePath":"/editarr/api"}');
    setCsrfToken("csrf-token");
    const settings = { revision: 0, selection_rules: [], severity_defaults: [] };
    const options = {
      name: "Imported",
      target: { source: "jellyfin" as const, item_id: "item-1", media_source_id: "version one" },
      synchronization: { start_seconds: 0, end_seconds: 101.25, end_mode: "before_credits" as const },
      mcf_material_end_seconds: 101.25,
      accepted_credits: null,
    };

    await fetchMcfRegistry();
    await fetchFilterDefaultsSettings();
    await updateFilterDefaultsSettings(settings);
    await applyFilterDefaults([]);
    await fetchMcfPreflight("review/one");
    await publishReviewExport("review/one", "mcf", { revision: 1, target: "self" });
    await importMcfUpload(new File(["mcf"], "folder/movie.mcf", { type: "application/octet-stream" }), options);
    await fetchExportStatus("item/one", "version one");
    await deleteExport("item/one", null, "mcf");
    await expect(publishReviewExport("review/one", "mcf", { revision: 1, target: "self" })).rejects.toMatchObject({
      code: "incomplete_mcf_review",
      filterIds: ["filter-1"],
      documentFields: ["material_end_seconds"],
    });

    expect(request.mock.calls.map(([input]) => String(input))).toEqual([
      "/editarr/api/mcf-registry",
      "/editarr/api/settings/filters",
      "/editarr/api/settings/filters",
      "/editarr/api/settings/filters/apply",
      "/editarr/api/reviews/review%2Fone/mcf/preflight",
      "/editarr/api/reviews/review%2Fone/exports/mcf",
      "/editarr/api/mcf-imports/upload",
      "/editarr/api/library-items/item%2Fone/exports?media_source_id=version+one",
      "/editarr/api/library-items/item%2Fone/exports/mcf",
      "/editarr/api/reviews/review%2Fone/exports/mcf",
    ]);
    for (const index of [2, 3, 5, 6, 8, 9]) {
      expect(new Headers((request.mock.calls[index][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("csrf-token");
    }
    const upload = request.mock.calls[6][1] as RequestInit;
    expect(new Headers(upload.headers).has("Content-Type")).toBe(false);
    expect((upload.body as FormData).get("file")).toBeInstanceOf(File);
    expect(JSON.parse(String((upload.body as FormData).get("options")))).toEqual(options);
  });

  it("uses protected retiming and credits detection routes", async () => {
    const changed = vi.fn();
    window.addEventListener(SCANS_CHANGED_EVENT, changed);
    const request = vi.fn()
      .mockResolvedValueOnce(json({ filters: [] }))
      .mockResolvedValueOnce(json({ scan: {}, queue_id: 4, model_consent: null }, 202))
      .mockResolvedValueOnce(json({ id: "scan/one" }))
      .mockResolvedValueOnce(json({ scan: {}, queue_id: 4, model_consent: null }, 202))
      .mockResolvedValueOnce(json({ accepted_credits: {}, membership: {}, filter: {} }));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");
    const synchronization = { start_seconds: 0, end_seconds: 100, end_mode: "file_end" as const };

    await retimeSavedReview("review/one", {
      revision: 2,
      memberships: [],
      current_synchronization: synchronization,
      proposed_synchronization: { ...synchronization, start_seconds: 1 },
      mcf_document: { title: null, year: null, work_type: null, season: null, episode: null, source_url: null, imdb_url: null, release: null, comment: null, material_start_seconds: 0, material_end_seconds: 90 },
      filters: [],
      accepted_credits: null,
      new_manual_filters: [],
    });
    await startCreditsDetection({ target: { source: "jellyfin", item_id: "item-1" }, qwen_model: null });
    await fetchCreditsDetection("scan/one");
    await submitCreditsModelConsent("scan/one", "continue_without_qwen");
    await acceptCreditsDetection("scan/one", { context: { kind: "review", review_id: "review/one", revision: 2 }, accepted_boundary_seconds: 90 });
    window.removeEventListener(SCANS_CHANGED_EVENT, changed);
    expect(changed).toHaveBeenCalledTimes(2);

    expect(request.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/reviews/review%2Fone/retime",
      "/api/credits-detections",
      "/api/credits-detections/scan%2Fone",
      "/api/credits-detections/scan%2Fone/model-consent",
      "/api/credits-detections/scan%2Fone/accept",
    ]);
    expect(creditsDetectionClipUrl("scan/one", 90.125)).toBe("/api/credits-detections/scan%2Fone/clip?boundary_seconds=90.125");
    for (const index of [0, 1, 3, 4]) {
      expect(new Headers((request.mock.calls[index][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("csrf-token");
    }
  });

  it("refreshes scan views after successful queue changes but not rejected changes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(json({ detail: "Cannot cancel" }, 409)));
    const changed = vi.fn();
    window.addEventListener(SCANS_CHANGED_EVENT, changed);
    try {
      await cancelScanQueueItem(1);
      await expect(cancelScanQueueItem(2)).rejects.toThrow();
      expect(changed).toHaveBeenCalledTimes(1);
    } finally { window.removeEventListener(SCANS_CHANGED_EVENT, changed); }
  });

  it("loads immutable scan findings for review snapshot staging", async () => {
    const request = vi.fn().mockResolvedValue(json({ revision: 0, findings: [] }));
    vi.stubGlobal("fetch", request);

    await fetchScanFindings("scan/one");

    expect(String(request.mock.calls[0][0])).toBe("/api/scans/scan%2Fone/findings");
    expect((request.mock.calls[0][1] as RequestInit | undefined)?.method).toBeUndefined();
  });

  it("loads structured scan references for dependency-aware deletion", async () => {
    const references = { scan_id: "scan/one", active_exports: [], reviews: [] };
    const request = vi.fn().mockResolvedValue(json(references));
    vi.stubGlobal("fetch", request);

    await expect(fetchScanReferences("scan/one")).resolves.toEqual(references);

    expect(String(request.mock.calls[0][0])).toBe("/api/scans/scan%2Fone/references");
  });

  it("uses a Request object's method and never sends CSRF to non-API or cross-origin URLs", async () => {
    const request = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");

    await apiRequest(new Request(`${window.location.origin}/api/scans`, { method: "POST" }));
    await apiRequest("/health", { method: "POST" });
    await apiRequest("https://untrusted.example/api/scans", { method: "POST" });

    expect(new Headers((request.mock.calls[0][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(new Headers((request.mock.calls[1][1] as RequestInit).headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers((request.mock.calls[2][1] as RequestInit).headers).has("X-CSRF-Token")).toBe(false);
  });

  it("uses a nested bootstrap for API helpers and path-boundary CSRF checks", async () => {
    const request = vi.fn().mockResolvedValue(json({ audio_models: [], audio_filters: [], video_models: [] }));
    vi.stubGlobal("fetch", request);
    setRuntimeBootstrap('{"version":1,"basePath":"/media/editarr","apiBasePath":"/media/editarr/api"}');
    setCsrfToken("csrf-token");

    await fetchCapabilities();
    await apiRequest("/media/editarr/api/scans", { method: "POST" });
    await apiRequest("/media/editarr/api/auth/setup", { method: "POST" });
    await apiRequest("/media/editarr/api2/scans", { method: "POST" });

    expect(request.mock.calls.map(([input]) => String(input))).toEqual([
      "/media/editarr/api/capabilities",
      "/media/editarr/api/scans",
      "/media/editarr/api/auth/setup",
      "/media/editarr/api2/scans",
    ]);
    expect(new Headers((request.mock.calls[1][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(new Headers((request.mock.calls[2][1] as RequestInit).headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers((request.mock.calls[3][1] as RequestInit).headers).has("X-CSRF-Token")).toBe(false);
  });

  it("shares credentials for JSON helpers and supports empty responses", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json({ audio_models: [], audio_filters: [], video_models: [] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);

    await expect(fetchCapabilities()).resolves.toEqual({
      audio_models: [], audio_filters: [], video_models: [],
    });
    await expect(deleteExport("item one", "version one", "edl")).resolves.toBeUndefined();

    for (const [, init] of request.mock.calls) {
      expect((init as RequestInit).credentials).toBe("same-origin");
    }
    expect(String(request.mock.calls[1][0])).toBe("/api/library-items/item%20one/exports/edl?media_source_id=version+one");
  });

  it("returns status and exposes typed authentication expiry events", async () => {
    const states: string[] = [];
    const unsubscribe = subscribeToAuthenticationState((state) => states.push(state));
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticatedStatus))
      .mockResolvedValueOnce(json({ code: "authentication_required", detail: "Sign in again" }, 401));
    vi.stubGlobal("fetch", request);

    await expect(fetchAuthenticationStatus()).resolves.toMatchObject(authenticatedStatus);
    await expect(loginWithJellyfin({ username: "Admin", password: "incorrect" })).rejects.toMatchObject({
      name: "AuthenticationStateError",
      status: 401,
      code: "authentication_required",
      authenticationState: "unauthenticated",
      message: "Sign in again",
    } satisfies Partial<AuthenticationStateError>);
    expect(states).toEqual(["unauthenticated"]);
    expect(new Headers((request.mock.calls[1][1] as RequestInit).headers).has("X-CSRF-Token")).toBe(false);
    unsubscribe();
  });

  it("uses typed setup, Jellyfin sign-in, and logout endpoints", async () => {
    const request = vi.fn().mockImplementation(() => Promise.resolve(json(authenticatedStatus)));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");

    await setupWithJellyfin({ server_url: "http://jellyfin.test", username: "Admin", password: "new password" });
    await loginWithJellyfin({ username: "Admin", password: "current password", remember_me: true });
    await logoutAuthentication();

    expect(request.mock.calls.map(([input, init]) => [
      String(input),
      (init as RequestInit).method,
      new Headers((init as RequestInit).headers).get("X-CSRF-Token"),
    ])).toEqual([
      ["/api/auth/setup", "POST", null],
      ["/api/auth/jellyfin/login", "POST", "csrf-token"],
      ["/api/auth/logout", "POST", "csrf-token"],
    ]);
  });

  it("uses the application-relative deployment settings endpoint with the standard CSRF boundary", async () => {
    const configuration = {
      active: {}, next_start: {}, saved: {}, restart_required: false, restart: {},
    };
    const request = vi.fn()
      .mockResolvedValueOnce(json(configuration))
      .mockResolvedValueOnce(json(configuration));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");

    await fetchDeploymentConfiguration();
    await saveDeploymentConfiguration({ port: 8123 });

    expect(request.mock.calls.map(([input, init]) => [
      String(input),
      (init as RequestInit).method,
      new Headers((init as RequestInit).headers).get("X-CSRF-Token"),
      (init as RequestInit).body,
    ])).toEqual([
      ["/api/settings/deployment", undefined, null, undefined],
      ["/api/settings/deployment", "PUT", "csrf-token", JSON.stringify({ port: 8123 })],
    ]);
  });

  it("uses protected model storage routes with the standard CSRF boundary", async () => {
    const storage = { artifacts: [], model_capabilities: [] };
    const action = { status: storage, accepted: true, removed: false };
    const request = vi.fn()
      .mockResolvedValueOnce(json(storage))
      .mockResolvedValueOnce(json(action, 202))
      .mockResolvedValueOnce(json(action))
      .mockResolvedValueOnce(json({ ...action, removed: true }));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");

    await fetchModelStorage();
    await prefetchModel("whisper");
    await cancelModelDownload("faster-whisper-base");
    await removeModel("faster-whisper-base");

    expect(request.mock.calls.map(([input, init]) => [
      String(input), (init as RequestInit).method,
      new Headers((init as RequestInit).headers).get("X-CSRF-Token"),
    ])).toEqual([
      ["/api/models", undefined, null],
      ["/api/models/prefetch", "POST", "csrf-token"],
      ["/api/models/faster-whisper-base/cancel", "POST", "csrf-token"],
      ["/api/models/faster-whisper-base", "DELETE", "csrf-token"],
    ]);
  });

  it("uses protected restart helpers and permits a same-origin reconnect API path", async () => {
    const restart = {
      supported: true, mode: "standalone_controller", generation: 2,
      in_progress: false, outcome: "applied", target_generation: 2,
      reason: null, detail: null,
    };
    const acknowledgement = {
      status: "accepted", target_generation: 2,
      reconnect_url: `${window.location.origin}/next/#/settings/general`,
    };
    const request = vi.fn()
      .mockResolvedValueOnce(json(acknowledgement, 202))
      .mockResolvedValueOnce(json(restart));
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");

    await expect(requestEditarrRestart()).resolves.toEqual(acknowledgement);
    await expect(fetchRestartCapability(`${window.location.origin}/next/api`)).resolves.toEqual(restart);

    expect(request.mock.calls.map(([input, init]) => [
      String(input),
      (init as RequestInit).method,
      (init as RequestInit).credentials,
      new Headers((init as RequestInit).headers).get("X-CSRF-Token"),
    ])).toEqual([
      ["/api/system/restart", "POST", "same-origin", "csrf-token"],
      [`${window.location.origin}/next/api/system/restart`, undefined, "same-origin", null],
    ]);
  });

  it("leaves CSRF ownership to the gate instead of accepting unconfirmed auth responses", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticatedStatus))
      .mockResolvedValueOnce(json({ ...authenticatedStatus, csrf_token: null }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);
    setCsrfToken("current-user-csrf");

    await fetchAuthenticationStatus();
    await loginWithJellyfin({ username: "Admin", password: "current password" });
    await apiRequest("/api/scans", { method: "POST" });

    expect(new Headers((request.mock.calls[2][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("current-user-csrf");
  });

  it("never leaks login credentials or a CSRF token into browser storage, URLs, document text, or the console", async () => {
    const password = "canary-password-never-rendered";
    const token = "canary-csrf-token-never-rendered";
    const request = vi.fn()
      .mockResolvedValueOnce(json({ ...authenticatedStatus, csrf_token: token }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const localStorage = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() };
    const sessionStorage = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() };
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const consoleLog = vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", request);
    vi.stubGlobal("localStorage", localStorage);
    vi.stubGlobal("sessionStorage", sessionStorage);

    await loginWithJellyfin({ username: "Admin", password, remember_me: true });
    await apiRequest("/api/scans", { method: "POST" });

    expect(localStorage.setItem).not.toHaveBeenCalled();
    expect(sessionStorage.setItem).not.toHaveBeenCalled();
    expect(String(request.mock.calls[0][0])).not.toContain(password);
    expect(String(request.mock.calls[0][0])).not.toContain(token);
    expect(String(request.mock.calls[1][0])).not.toContain(token);
    expect(document.body.textContent).not.toContain(password);
    expect(document.body.textContent).not.toContain(token);
    expect(consoleError).not.toHaveBeenCalled();
    expect(consoleLog).not.toHaveBeenCalled();
  });

  it("preserves server error detail in a typed safe error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ code: "invalid_input", detail: "Choose a name" }, 422)));

    await expect(loginWithJellyfin({ username: "Admin", password: "password" })).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      code: "invalid_input",
      message: "Choose a name",
    } satisfies Partial<ApiError>);
  });

  it("preserves nested review and import error metadata", async () => {
    const references = [{ scan_id: "scan-1", name: "Primary scan" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({
      detail: {
        code: "invalid_edl_import",
        message: "Unsupported action",
        line_number: 12,
        references,
      },
    }, 422)));

    await expect(loginWithJellyfin({ username: "Admin", password: "password" })).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      code: "invalid_edl_import",
      message: "Unsupported action (line 12)",
      lineNumber: 12,
      references,
    } satisfies Partial<ApiError>);
  });
});
