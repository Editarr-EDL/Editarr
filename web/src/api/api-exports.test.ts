import { afterEach, expect, it, vi } from "vitest";
import { deletePublication, downloadReviewExport, fetchVersionPublications, publishReviewExport } from "./api-exports";
import { testPublication } from "../review/export-test-fixtures";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); });

it("returns the server's replacement confirmation instead of throwing", async () => {
  const request = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({ detail: { code: "export_confirmation_required", message: "Confirm", confirmation: "c".repeat(64) } }, 409));
  vi.stubGlobal("fetch", request);
  await expect(publishReviewExport("review/one", "edl", { revision: 3, target: "review_owner" }))
    .resolves.toEqual({ kind: "confirmation_required", confirmation: "c".repeat(64) });
  expect(String(request.mock.calls[0][0])).toBe("/api/reviews/review%2Fone/exports/edl");
  expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual({ revision: 3, target: "review_owner" });
});

it("throws a stale confirmation with its code so the caller can recover", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: { code: "stale_export_confirmation", message: "The export changed" } }, 409)));
  await expect(publishReviewExport("r", "mcf", { revision: 1, target: "self", confirmation: "d".repeat(64) }))
    .rejects.toMatchObject({ status: 409, code: "stale_export_confirmation" });
});

it("returns the published record on success", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(testPublication({ id: 7 }))));
  await expect(publishReviewExport("r", "edl", { revision: 1, target: "self" })).resolves.toMatchObject({ kind: "published", publication: { id: 7 } });
});

it("downloads with the server's personal filename", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("1 2 1\n", {
    status: 200, headers: { "content-disposition": "attachment; filename*=UTF-8''Movie.user-local-1.edl" },
  })));
  const result = await downloadReviewExport("r", "edl", { revision: 1 });
  expect(result.filename).toBe("Movie.user-local-1.edl");
  expect(await result.blob.text()).toBe("1 2 1\n");
});

it("binds deletion to the publication UUID and revision", async () => {
  const request = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", request);
  await deletePublication(testPublication({ id: 41, publication_revision: 3 }));
  expect(request.mock.calls.map(([input]) => String(input))).toEqual([
    "/api/exports/41?revision=3&publication_id=00000000-0000-4000-8000-000000000041",
  ]);
  expect(request.mock.calls[0][1]?.method).toBe("DELETE");
});

it("reads an unversioned source's publications with an explicit empty version", async () => {
  const request = vi.fn(async (_input: RequestInfo | URL) => json({ publications: [testPublication()] }));
  vi.stubGlobal("fetch", request);
  expect(await fetchVersionPublications("media-1", null)).toHaveLength(1);
  expect(String(request.mock.calls[0][0])).toBe("/api/exports?media_item_id=media-1&media_source_id=");
});
