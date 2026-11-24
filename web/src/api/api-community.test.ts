import { afterEach, expect, it, vi } from "vitest";
import { CommunityApiError, completeCommunityEnrollment, fetchCommunityConnection, fetchLocalCommunityConnection,
  renameCommunityProfile, startCommunityEnrollment, rotateCommunityKey, reconcileCommunityKey, retryCommunityKey } from "./api-community";
import { setCsrfToken } from "./api-transport";
import { resetRuntimeUrlsForTests } from "../shared/runtime-urls";

afterEach(() => {
  setCsrfToken(null);
  document.getElementById("editarr-bootstrap")?.remove();
  document.head.insertAdjacentHTML("beforeend", '<script id="editarr-bootstrap" type="application/json">{"version":1,"basePath":"/","apiBasePath":"/api"}</script>');
  resetRuntimeUrlsForTests();
  vi.unstubAllGlobals();
});

it("uses only mounted local APIs, session CSRF and explicit completion", async () => {
  document.getElementById("editarr-bootstrap")?.remove();
  document.head.insertAdjacentHTML("beforeend", '<script id="editarr-bootstrap" type="application/json">{"version":1,"basePath":"/editarr","apiBasePath":"/editarr/api"}</script>');
  resetRuntimeUrlsForTests();
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
    configured: true, connected: false, profile: null, pending: null,
  }), { headers: { "Content-Type": "application/json" } })));
  vi.stubGlobal("fetch", fetch);
  setCsrfToken("csrf-test");
  await startCommunityEnrollment();
  await completeCommunityEnrollment("b".repeat(64));
  await renameCommunityProfile("Public Name", "durable-rename");
  for (const [url, init] of fetch.mock.calls) {
    expect(url).toMatch(/^\/editarr\/api\/community\//);
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-test");
  }
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ state_id: "b".repeat(64), confirmed: true });
  expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ display_name: "Public Name", operation_id: "durable-rename" });
  await fetchLocalCommunityConnection();
  expect(fetch.mock.calls[3][0]).toBe("/editarr/api/community/connection/status");
  expect(fetch.mock.calls[3][1].credentials).toBe("same-origin");
});

it("preserves machine-readable outages rather than returning an empty connection", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    code: "membership_unavailable", retryable: true,
  }), { status: 503, headers: { "Content-Type": "application/json" } })));
  await expect(fetchCommunityConnection()).rejects.toMatchObject({
    code: "membership_unavailable", retryable: true, status: 503,
  });
});

it("pins named identity requests to their account and abort signal", async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
    configured: true, connected: false, profile: null, pending: null,
  }), { headers: { "Content-Type": "application/json" } })));
  vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  const options = { userId: 17, signal: controller.signal };
  await startCommunityEnrollment(options);
  await completeCommunityEnrollment("b".repeat(64), options);
  await renameCommunityProfile("Public Name", "durable-rename", options);
  await rotateCommunityKey(options);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    "/api/users/17/community/enrollment/start", "/api/users/17/community/enrollment/complete",
    "/api/users/17/community/profile", "/api/users/17/community/keys/rotate",
  ]);
  for (const [, init] of fetch.mock.calls) expect(init.signal).toBe(controller.signal);
});

it("maps network failure and preserves cancellation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await expect(fetchCommunityConnection()).rejects.toBeInstanceOf(CommunityApiError);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Cancelled", "AbortError")));
  await expect(fetchCommunityConnection()).rejects.toMatchObject({ name: "AbortError" });
});

it("sends explicit local rotation commands without any key material", async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
    configured: true, connected: true, profile: null, pending: null,
  }), { headers: { "Content-Type": "application/json" } })));
  vi.stubGlobal("fetch", fetch);
  setCsrfToken("csrf-test");
  await rotateCommunityKey(); await reconcileCommunityKey(); await retryCommunityKey();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    "/api/community/keys/rotate", "/api/community/keys/rotation-status", "/api/community/keys/rotation-retry",
  ]);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ confirmed: true });
  expect(fetch.mock.calls[1][1].body).toBeUndefined();
  expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ confirmed: true });
  for (const [, init] of fetch.mock.calls) {
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-test");
    expect(init.credentials).toBe("same-origin");
  }
});