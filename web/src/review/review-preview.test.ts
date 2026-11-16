import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

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