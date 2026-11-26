import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { ReviewFilter } from "../api/api-review-types";
import { json, reviewFilter, savedReview } from "../test/app-test-fixtures";
import { useSavedReviewWorkspace } from "./review-workspace-state";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it.each(["before-response", "after-response", "before-commit"] as const)("releases preview URLs when unmount occurs %s", async (timing) => {
  const filter = reviewFilter("private-filter", "audio");
  const review = savedReview({ filters: [filter] });
  let deliver!: (value: Response) => void;
  const response = new Promise<Response>((resolve) => { deliver = resolve; });
  const liveUrls = new Set<string>();
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => {
      liveUrls.add("blob:private-preview");
      return "blob:private-preview";
    });
    static revokeObjectURL = vi.fn((url: string) => { liveUrls.delete(url); });
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const pathname = new URL(String(input), "http://editarr.test").pathname;
    if (pathname.endsWith("/mute-preview")) return response;
    if (pathname.endsWith("/mcf-registry")) return json({ families: [] });
    if (pathname === "/api/reviews/review-1") return json(review);
    if (pathname.endsWith("/reviews")) return json([review]);
    return json({});
  }));
  const workspace = renderHook(() => useSavedReviewWorkspace({
    item: { id: "movie-1" }, mediaSourceId: "source-1", scans: [], onImported: () => undefined,
  }));
  await waitFor(() => expect(workspace.result.current.selected?.id).toBe("review-1"));
  let pending!: Promise<void>;
  act(() => { pending = workspace.result.current.previewFilter(filter); });
  if (timing === "before-response") workspace.unmount();
  await act(async () => {
    deliver(new Response(new Blob(["private clip"])));
    await pending;
    if (timing === "before-commit") workspace.unmount();
  });
  if (timing === "after-response") {
    expect(workspace.result.current.preview?.url).toBe("blob:private-preview");
    expect([...liveUrls]).toEqual(["blob:private-preview"]);
    workspace.unmount();
  }
  expect([...liveUrls]).toEqual([]);
});
it("previews a saved community filter's unchanged clip against its saved review", async () => {
  const scanFilter = reviewFilter("scan-filter", "audio");
  const community: ReviewFilter = { ...reviewFilter("community-filter", "audio"), source_scan_id: null, source_finding_id: null,
    community_source_id: "src-1", community_filter_id: "master-1", start_seconds: 30, end_seconds: 34 };
  const review = savedReview({ filters: [scanFilter, community] });
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:preview");
    static revokeObjectURL = vi.fn();
  });
  const requests: { method: string; pathname: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const pathname = new URL(String(input), "http://editarr.test").pathname;
    if (pathname.endsWith("preview")) {
      requests.push({ method: init?.method ?? "GET", pathname, body: init?.body ? JSON.parse(String(init.body)) : null });
      return new Response(new Blob(["clip"]));
    }
    if (pathname.endsWith("/mcf-registry")) return json({ families: [] });
    if (pathname === "/api/reviews/review-1") return json(review);
    if (pathname.endsWith("/reviews")) return json([review]);
    return json({});
  }));
  const workspace = renderHook(() => useSavedReviewWorkspace({
    item: { id: "movie-1" }, mediaSourceId: "source-1", scans: [], onImported: () => undefined,
  }));
  await waitFor(() => expect(workspace.result.current.selected?.id).toBe("review-1"));
  await act(() => workspace.result.current.previewFilter(scanFilter));
  await act(() => workspace.result.current.previewFilter(community));

  // The server allows an unchanged saved community clip with the review's preview grant;
  // only an edited clip also needs permission to edit cues.
  expect(requests).toEqual([
    { method: "GET", pathname: "/api/reviews/review-1/filters/scan-filter/mute-preview", body: null },
    { method: "POST", pathname: "/api/library-items/movie-1/reviews/preview", body: expect.objectContaining({
      review_id: "review-1", source: "audio", edit_action: "mute", start_seconds: 30, end_seconds: 34 }) },
  ]);
  expect(workspace.result.current.preview?.filterId).toBe("community-filter");
});

it("previews an edited community filter with its edited action, like a scan filter", async () => {
  const community: ReviewFilter = { ...reviewFilter("community-filter", "audio"), source_scan_id: null, source_finding_id: null,
    community_source_id: "src-1", community_filter_id: "master-1", start_seconds: 30, end_seconds: 34 };
  const review = savedReview({ filters: [community] });
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:preview");
    static revokeObjectURL = vi.fn();
  });
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const pathname = new URL(String(input), "http://editarr.test").pathname;
    if (pathname === "/api/library-items/movie-1/reviews/preview") {
      bodies.push(JSON.parse(String(init?.body)));
      return new Response(new Blob(["clip"]));
    }
    if (pathname.endsWith("/mcf-registry")) return json({ families: [] });
    if (pathname === "/api/reviews/review-1") return json(review);
    if (pathname.endsWith("/reviews")) return json([review]);
    return json({});
  }));
  const workspace = renderHook(() => useSavedReviewWorkspace({
    item: { id: "movie-1" }, mediaSourceId: "source-1", scans: [], onImported: () => undefined,
  }));
  await waitFor(() => expect(workspace.result.current.selected?.id).toBe("review-1"));
  await act(() => workspace.result.current.previewFilter({ ...community, edit_action: "skip" }));

  expect(bodies).toEqual([expect.objectContaining({ review_id: "review-1", source: "audio", edit_action: "skip" })]);
  expect(workspace.result.current.preview?.filterId).toBe("community-filter");
});
