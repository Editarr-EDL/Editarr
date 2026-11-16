import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ExportPublication, ExportStatus } from "../api/api-mcf-types";
import type { Principal } from "../auth/session";
import { knownCapabilities } from "../auth/capabilities";
import { emptyExportStatus } from "./export-status";
import { testPublication } from "./export-test-fixtures";
import { SavedReviewPanel } from "./review-workspace";

const access = vi.hoisted(() => ({ principal: null as Principal | null }));
vi.mock("../auth/auth-gate", () => ({ usePrincipal: () => access.principal, useOptionalAuthenticationGate: () => null }));
vi.mock("../api/api-service", () => ({ fetchMcfRegistry: vi.fn(async () => ({ families: [] })), applyFilterDefaults: vi.fn() }));
vi.mock("../api/api-users", () => ({ readAccount: vi.fn(async (id: number) => ({ id, display_name: `User ${id}` })) }));
vi.mock("../scans/sharing-dialog", () => ({ ShareAction: () => null }));

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
const retained = (mediaItemId: string): ExportPublication[] => [
  testPublication({ id: 1, media_item_id: mediaItemId, review_id: null }),
  testPublication({ id: 2, media_item_id: mediaItemId, review_id: null, format: "mcf" }),
];
const panel = (itemId: string, mediaItemId: string | null, exportStatus?: ExportStatus) => (
  <SavedReviewPanel item={{ id: itemId, duration_seconds: 100 }} mediaItemId={mediaItemId} mediaSourceId={null} scans={[]} exportStatus={exportStatus} onImported={vi.fn()} />
);

/** Answer every media version's discovery from `discovery`, with no saved reviews visible. */
function installApi(discovery: Record<string, () => Promise<Response>>) {
  const requests = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://editarr.test");
    if (/^\/api\/library-items\/[^/]+\/reviews$/.test(url.pathname)) return json([]);
    const mediaItemId = url.searchParams.get("media_item_id");
    if (url.pathname === "/api/exports" && mediaItemId && discovery[mediaItemId]) return discovery[mediaItemId]();
    throw new Error(`Unexpected request ${url.pathname}${url.search}`);
  });
  vi.stubGlobal("fetch", requests);
  return requests;
}

beforeEach(() => {
  access.principal = { userId: 1, capabilities: knownCapabilities(["reviews.create.own", "exports.personal.delete.own"]) } as Principal;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); access.principal = null; });

it("lists files retained from a deleted last review when the workspace opens with no visible review", async () => {
  installApi({ "media-a": async () => json({ publications: retained("media-a") }) });
  render(panel("item-a", "media-a"));
  const files = await screen.findByRole("region", { name: "Published files" });
  expect(within(files).getByRole("listitem", { name: "EDL · Personal · You" })).toBeVisible();
  expect(within(files).getByRole("listitem", { name: "MCF · Personal · You" })).toBeVisible();
  expect(within(files).getByRole("button", { name: "Delete EDL · Personal · You" })).toBeEnabled();
});

it("drops the previous media's files and Delete buttons when another file is selected", async () => {
  installApi({ "media-a": async () => json({ publications: retained("media-a") }) });
  const view = render(panel("item-a", "media-a"));
  await screen.findByRole("button", { name: "Delete EDL · Personal · You" });
  view.rerender(panel("item-b", null));
  await screen.findByText("No saved reviews yet.");
  expect(screen.queryByRole("region", { name: "Published files" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Delete (EDL|MCF)/ })).not.toBeInTheDocument();
});

it("ignores a discovery response for the previous media that arrives after the switch", async () => {
  let answerA!: (response: Response) => void;
  const requests = installApi({ "media-a": () => new Promise<Response>((resolve) => { answerA = resolve; }) });
  const view = render(panel("item-a", "media-a"));
  await waitFor(() => expect(answerA).toBeTypeOf("function"));
  view.rerender(panel("item-b", null));
  await waitFor(() => expect(requests.mock.calls.some(([url]) => String(url).startsWith("/api/library-items/item-b/reviews"))).toBe(true));
  await act(async () => { answerA(json({ publications: retained("media-a") })); });
  await screen.findByText("No saved reviews yet.");
  expect(screen.queryByRole("region", { name: "Published files" })).not.toBeInTheDocument();
});

it("hides the parent's not-yet-refreshed status rows that belong to the previous media", async () => {
  installApi({ "media-a": async () => json({ publications: retained("media-a") }), "media-b": () => new Promise<Response>(() => {}) });
  const parentStatus = { ...emptyExportStatus(), publications: retained("media-a") };
  const view = render(panel("item-a", "media-a", parentStatus));
  await screen.findByRole("button", { name: "Delete EDL · Personal · You" });
  view.rerender(panel("item-b", "media-b", parentStatus));
  await screen.findByText("No saved reviews yet.");
  expect(screen.queryByRole("region", { name: "Published files" })).not.toBeInTheDocument();
});
