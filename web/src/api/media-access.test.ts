import { afterEach, expect, it, vi } from "vitest";
import { observeMediaRequest, watchMediaAccess } from "./media-access";
import { apiRequest } from "./api-transport";
import { createElement, useEffect } from "react";
import { render } from "@testing-library/react";

const cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.splice(0).forEach(cleanup => cleanup());
  vi.unstubAllGlobals();
});
const denial = (code = "not_found", status = 404) => new Response(JSON.stringify({ code, detail: "Media unavailable" }), { status });

it.each([
  ["/api/library-items/movie-1/exports", undefined],
  ["/api/media/audio-tracks?source=jellyfin&item_id=movie-1", undefined],
  ["/api/media/audio-tracks/selection", { source: "jellyfin", item_id: "movie-1" }],
  ["/api/scans", { jellyfin_item_id: "movie-1" }],
  ["/api/credits-detections", { target: { source: "jellyfin", item_id: "movie-1" } }],
  ["/api/filter-candidates", { source: "jellyfin", item_id: "movie-1" }],
])("routes target denial for %s", async (route, body) => {
  const denied = vi.fn();
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  await observeMediaRequest(new URL(route, "http://localhost"), { body: JSON.stringify(body) })(denial("media_unavailable"));
  expect(denied).toHaveBeenCalledOnce();
});

it("does not confuse another item, missing export, or capability denial with target loss", async () => {
  const denied = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  await observeMediaRequest(new URL("http://localhost/api/library-items/movie-2"), {})(denial());
  for (const code of ["export_not_found", "capability_denied"]) {
    await observeMediaRequest(new URL("http://localhost/api/library-items/movie-1/exports/edl"), {})(denial(code));
  }
  expect(denied).not.toHaveBeenCalled();
});

it("rejects a late denial from a prior workspace even after reopening the same item", async () => {
  const old = vi.fn();
  const stop = watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, old);
  const complete = observeMediaRequest(new URL("http://localhost/api/library-items/movie-1"), {});
  stop();
  const reopened = vi.fn();
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, reopened));
  await complete(denial());
  expect(old).not.toHaveBeenCalled();
  expect(reopened).not.toHaveBeenCalled();
});

it.each([403, 404])("revalidates an unrelated item-scoped %s before revoking", async status => {
  const denied = vi.fn();
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  await observeMediaRequest(new URL("http://localhost/api/library-items/movie-1/reviews"), { method: "POST" })(denial("not_found", status));
  expect(fetch).toHaveBeenCalledOnce();
  expect(String(fetch.mock.calls[0][0])).toBe("http://localhost/api/library-items/movie-1");
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: "GET", cache: "no-store", credentials: "same-origin" });
  expect(denied).not.toHaveBeenCalled();
});

it("uses the Request object's method before classifying an identity-route refusal", async () => {
  const denied = vi.fn();
  const fetch = vi.fn<typeof globalThis.fetch>()
    .mockResolvedValueOnce(denial())
    .mockResolvedValueOnce(new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  const response = await apiRequest(new Request("http://localhost/api/library-items/movie-1", { method: "POST" }));
  expect(response.status).toBe(404);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1][1]).toMatchObject({ method: "GET", cache: "no-store" });
  expect(denied).not.toHaveBeenCalled();
});

it("revokes only when the fresh item read is refused", async () => {
  const denied = vi.fn();
  const fetch = vi.fn<typeof globalThis.fetch>(async () => denial("not_found"));
  vi.stubGlobal("fetch", fetch);
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  await observeMediaRequest(new URL("/api/library-items/movie-1/reviews", "http://localhost"), { method: "POST" })(denial("scan_not_found"));
  expect(String(fetch.mock.calls[0][0])).toBe("http://localhost/api/library-items/movie-1");
  expect(denied).toHaveBeenCalledOnce();
});

it.each([undefined, null, "", 42])("never treats a code-less or invalid code %s as revocation", async code => {
  const denied = vi.fn();
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  for (const route of ["/api/library-items/movie-1", "/api/library-items/movie-1/reviews", "/api/media/audio-tracks?source=jellyfin&item_id=movie-1"]) {
    await observeMediaRequest(new URL(route, "http://localhost"), {})(new Response(JSON.stringify({ code }), { status: 404 }));
  }
  expect(denied).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

it.each([403, 404])("accepts a coded identity-read %s as media refusal", async status => {
  const denied = vi.fn();
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  await observeMediaRequest(new URL("http://localhost/api/library-items/movie-1"), {})(denial(status === 403 ? "capability_denied" : "not_found", status));
  expect(denied).toHaveBeenCalledOnce();
});

it("does not revoke on a failed or code-less revalidation", async () => {
  const denied = vi.fn();
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied));
  for (const read of [() => Promise.reject(new Error("offline")), async () => new Response("{}", { status: 404 }), async () => denial("unavailable", 503)]) {
    vi.stubGlobal("fetch", vi.fn(read));
    await observeMediaRequest(new URL("http://localhost/api/library-items/movie-1/reviews"), { method: "POST" })(denial());
  }
  expect(denied).not.toHaveBeenCalled();
});

it("does not deliver an in-flight revalidation to a reopened workspace", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
  const old = vi.fn();
  const stop = watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, old);
  const pending = observeMediaRequest(new URL("http://localhost/api/library-items/movie-1/reviews"), { method: "POST" })(denial());
  await vi.waitFor(() => expect(finish).toBeDefined());
  stop();
  const reopened = vi.fn();
  cleanups.push(watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, reopened));
  finish(denial());
  await pending;
  expect(old).not.toHaveBeenCalled();
  expect(reopened).not.toHaveBeenCalled();
});

it("shares only in-flight revalidation within a mounted target workspace", async () => {
  const finishes: ((response: Response) => void)[] = [];
  const fetch = vi.fn(() => new Promise<Response>(resolve => { finishes.push(resolve); }));
  vi.stubGlobal("fetch", fetch);
  const denied = vi.fn();
  function Workspace() {
    useEffect(() => watchMediaAccess({ source: "jellyfin", itemId: "movie-1" }, denied), []);
    return null;
  }
  const first = render(createElement(Workspace));
  const fail = () => observeMediaRequest(new URL("http://localhost/api/library-items/movie-1/reviews"), { method: "POST" })(denial("scan_not_found"));
  const burst = Array.from({ length: 20 }, fail);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
  await new Promise(resolve => setTimeout(resolve, 30));
  const reads = fetch.mock.calls.length;
  finishes.splice(0).forEach(finish => finish(new Response("{}")));
  await Promise.all(burst);
  expect(reads).toBe(1);
  expect(denied).not.toHaveBeenCalled();
  const later = fail();
  await vi.waitFor(() => expect(finishes).toHaveLength(1));
  first.unmount();
  const replacement = render(createElement(Workspace));
  const reopened = fail();
  await vi.waitFor(() => expect(finishes).toHaveLength(2));
  finishes[0](denial());
  finishes[1](new Response("{}"));
  await Promise.all([later, reopened]);
  replacement.unmount();
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(denied).not.toHaveBeenCalled();
});