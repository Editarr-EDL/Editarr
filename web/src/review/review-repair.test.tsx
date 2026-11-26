import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReviewRepairSummary, SavedReview, SavedReviewSummary } from "../api/api-review-types";
import type { Principal } from "../auth/session";
import { knownCapabilities } from "../auth/capabilities";
import { emptyExportStatus } from "./export-status";
import { SavedReviewPanel } from "./review-workspace";

const access = vi.hoisted(() => ({ principal: null as Principal | null }));
vi.mock("../auth/auth-gate", () => ({ usePrincipal: () => access.principal, useOptionalAuthenticationGate: () => null, useCan: () => false }));
vi.mock("../api/api-service", () => ({ fetchMcfRegistry: vi.fn(async () => ({ families: [] })), applyFilterDefaults: vi.fn() }));
vi.mock("../scans/sharing-dialog", () => ({ ShareAction: () => null }));

const repair: ReviewRepairSummary = {
  message: "This review uses a scan that no longer exists.",
  missing_scan_ids: ["missing"], detected_at: "2026-10-25T00:00:00Z",
  recover: { kept_cues: 3, removed_memberships: 2, removed_accepted_credits: 1 },
};
const healthy: SavedReview = {
  id: "healthy", media_item_id: "media-1", target: { source: "jellyfin", item_id: "media-1", media_source_id: null },
  name: "Healthy review", revision: 1, content_revision: 1, edl_content_revision: 1, mcf_content_revision: 1,
  created_at: "2026-10-25T00:00:00Z", updated_at: "2026-10-25T00:00:00Z",
  exports: emptyExportStatus(), repair: null, memberships: [], scans: [], filters: [], accepted_credits: null,
  synchronization: { start_seconds: 0, end_seconds: 100, end_mode: "file_end" },
  mcf_document: { title: "Healthy review", year: null, work_type: "movie", season: null, episode: null,
    source_url: null, imdb_url: null, release: null, comment: null, material_start_seconds: 0, material_end_seconds: 100 },
};
const broken: SavedReviewSummary = { ...healthy, id: "broken", name: "Broken review", repair };
const props = { item: { id: "media-1", duration_seconds: 100 }, mediaSourceId: null, scans: [], onImported: vi.fn() };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

function installApi(initial: SavedReviewSummary[] = [broken], detail: SavedReview | Response = healthy) {
  let reviews = initial;
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.startsWith("/api/library-items/media-1/reviews?")) return json(reviews);
    if (path === "/api/reviews/broken/deletion") {
      if (init?.method !== "POST") return json({ review_id: "broken", revision: 1, confirmation: "c".repeat(64), files: [], legacy_formats: [], scan_ids: [] });
      reviews = [];
      return json({ review_id: "broken", retained: [], removed: [] });
    }
    if (init?.method === "POST") {
      reviews = [{ ...broken, repair: null }];
      return json(reviews[0]);
    }
    if (path.startsWith("/api/reviews/")) return detail instanceof Response ? detail.clone() : json(detail);
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", requests);
  access.principal = { capabilities: knownCapabilities(["reviews.delete.own", "reviews.save.own"]) } as Principal;
  return requests;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); access.principal = null; });

it("flags the row on a separate line with Resolve and never opens it, leaving healthy rows unchanged", async () => {
  const requests = installApi([healthy, broken]);
  render(<SavedReviewPanel {...props} />);
  const openHealthy = await screen.findByRole("button", { name: "Open review Healthy review" });
  await screen.findByRole("button", { name: "Rename review" });
  const flaggedName = screen.getByRole("button", { name: "Open review Broken review" });
  const flag = screen.getByRole("img", { name: "Review needs repair" });
  expect(flag).toBeVisible();
  const errorRow = flag.closest("tr")!;
  expect(errorRow).not.toBe(flaggedName.closest("tr"));
  expect(errorRow.previousElementSibling).toBe(flaggedName.closest("tr"));
  expect(within(errorRow).getByText(repair.message)).toBeVisible();
  expect(within(errorRow).getByRole("button", { name: "Resolve Broken review" })).toBeEnabled();
  expect(openHealthy).toBeEnabled();
  expect(within(openHealthy.closest("tr")!).queryByRole("img")).not.toBeInTheDocument();
  fireEvent.click(flaggedName);
  expect(requests.mock.calls.some(([url]) => String(url) === "/api/reviews/broken")).toBe(false);
});

it("does not automatically fetch or open the most recent review when its summary is flagged", async () => {
  const requests = installApi();
  render(<SavedReviewPanel {...props} />);
  await screen.findByRole("button", { name: "Resolve Broken review" });
  expect(requests.mock.calls.every(([url]) => !String(url).startsWith("/api/reviews/"))).toBe(true);
  expect(screen.queryByRole("button", { name: "Rename review" })).not.toBeInTheDocument();
});

it.each(["automatic", "selected"])("turns a %s detail 409 into a flagged list row without an editor", async (mode) => {
  const unflagged = { ...broken, repair: null };
  const requests = installApi(mode === "automatic" ? [unflagged] : [healthy, unflagged]);
  const original = requests.getMockImplementation()!;
  requests.mockImplementation(async (url, init) => String(url) === "/api/reviews/broken"
    ? json({ detail: { code: "review_needs_repair", message: repair.message, repair } }, 409)
    : original(url, init));
  render(<SavedReviewPanel {...props} />);
  if (mode === "selected") {
    await screen.findByRole("button", { name: "Rename review" });
    fireEvent.click(screen.getByRole("button", { name: "Open review Broken review" }));
  }
  await screen.findByRole("button", { name: "Resolve Broken review" });
  expect(screen.getByRole("button", { name: "Open review Broken review" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Rename review" })).not.toBeInTheDocument();
});

it("deletes a flagged review through the file-choice dialog and refreshes the list", async () => {
  const requests = installApi();
  render(<SavedReviewPanel {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Resolve Broken review" }));
  fireEvent.click(screen.getByRole("radio", { name: "Delete the review" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  const deletion = await screen.findByRole("dialog", { name: "Delete review" });
  expect(await within(deletion).findByText("No published files you can see come from this review.")).toBeVisible();
  fireEvent.click(within(deletion).getByRole("button", { name: "Delete review" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(screen.queryByRole("img", { name: "Review needs repair" })).not.toBeInTheDocument());
  expect(requests).toHaveBeenCalledWith("/api/reviews/broken/deletion", expect.objectContaining({ method: "POST" }));
  expect(requests.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
});

it.each([
  { option: "Recover the review", submit: "Recover review", method: "POST", endpoint: "/api/reviews/broken/repair/recover" },
])("$option succeeds through its endpoint, closes the modal and refreshes the list", async ({ option, submit, method, endpoint }) => {
  const requests = installApi();
  render(<SavedReviewPanel {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Resolve Broken review" }));
  const listReads = requests.mock.calls.filter(([url]) => String(url).startsWith("/api/library-items/media-1/reviews?")).length;
  fireEvent.click(screen.getByRole("radio", { name: option }));
  fireEvent.click(screen.getByRole("button", { name: submit }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(screen.queryByRole("img", { name: "Review needs repair" })).not.toBeInTheDocument());
  expect(requests).toHaveBeenCalledWith(endpoint, expect.objectContaining({ method }));
  expect(requests.mock.calls.filter(([url]) => String(url).startsWith("/api/library-items/media-1/reviews?")).length).toBeGreaterThan(listReads);
});

it("keeps a detail conflict flagged when an older list read finishes afterwards", async () => {
  installApi();
  let completeList!: (response: Response) => void;
  let listReads = 0;
  const unflagged = { ...broken, repair: null };
  const requests = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).startsWith("/api/library-items/media-1/reviews?")) {
      listReads += 1;
      if (listReads === 1) return new Promise<Response>((resolve) => { completeList = resolve; });
      return json([unflagged]);
    }
    return json({ detail: { code: "review_needs_repair", message: repair.message, repair } }, 409);
  });
  vi.stubGlobal("fetch", requests);
  render(<SavedReviewPanel {...props} />);
  await waitFor(() => expect(requests.mock.calls.some(([url]) => String(url) === "/api/reviews/broken")).toBe(true));
  completeList(json([unflagged]));
  expect(await screen.findByRole("button", { name: "Resolve Broken review" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Open review Broken review" })).toBeDisabled();
});

it("renders an unexpectedly loaded flagged review under an alert with every editor control disabled", async () => {
  installApi();
  render(<SavedReviewPanel {...props} importedReview={{ ...healthy, ...broken }} />);
  const editor = await screen.findByRole("group", { name: "Review awaiting repair" });
  expect(editor).toHaveAttribute("disabled");
  expect(editor).toHaveClass("review-repair-readonly");
  const alert = screen.getByRole("alert");
  expect(within(alert).getByText(repair.message)).toBeVisible();
  expect(within(alert).getByRole("button", { name: "Resolve" })).toBeEnabled();
  const controls = editor.querySelectorAll("input, button, select, textarea");
  expect(controls.length).toBeGreaterThan(5);
  for (const control of controls) expect(control).toBeDisabled();
  fireEvent.click(within(alert).getByRole("button", { name: "Resolve" }));
  expect(screen.getByRole("dialog", { name: "Resolve review" })).toBeVisible();
});

it.each(["save", "reload"])("retains the unsaved draft when %s returns a repair conflict", async (failure) => {
  const requests = installApi([healthy]);
  const original = requests.getMockImplementation()!;
  let conflict = false;
  requests.mockImplementation(async (url, init) => {
    if (String(url) === "/api/reviews/healthy" && init?.method === "PUT") {
      conflict = true;
      return json({ detail: { code: failure === "save" ? "review_needs_repair" : "stale_review_revision", message: repair.message, repair } }, 409);
    }
    if (conflict && String(url) === "/api/reviews/healthy") {
      return json({ detail: { code: "review_needs_repair", message: repair.message, repair } }, 409);
    }
    return original(url, init);
  });
  render(<SavedReviewPanel {...props} />);
  const title = await screen.findByRole("textbox", { name: "MCF title" });
  fireEvent.change(title, { target: { value: "Unsaved private draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
  const editor = await screen.findByRole("group", { name: "Review awaiting repair" });
  expect(editor).toBeDisabled();
  expect(within(editor).getByRole("textbox", { name: "MCF title" })).toHaveValue("Unsaved private draft");
  expect(within(editor).getByRole("textbox", { name: "MCF title" })).toBeDisabled();
  expect(screen.getByRole("heading", { name: "Healthy review" })).toBeVisible();
  const resolve = screen.getByRole("button", { name: "Resolve" });
  expect(resolve).toBeEnabled();
  fireEvent.click(resolve);
  expect(screen.getByRole("dialog", { name: "Resolve review" })).toBeVisible();
});