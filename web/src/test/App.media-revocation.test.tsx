import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { setCsrfToken } from "../api/api-transport";
import { observeMediaRequest } from "../api/media-access";
import { CatalogItemBoundary } from "../catalog/catalog-item-boundary";
import { completeScan, installProductionApi, json, signedInStatus } from "./app-test-fixtures";

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.location.hash = "";
});

it("renders the projected scan failure without hiding its status", async () => {
  const fallback = installProductionApi();
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    if (new URL(String(input), "http://editarr.test").pathname === "/api/scans/history") {
      return json([completeScan({ name: "Own failed scan", status: "failed", is_own: true, error_message: "Scan could not complete" })]);
    }
    return fallback(input, init);
  }));
  window.location.hash = "#/library/movies/item/movie-1";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await screen.findByText(/Own failed scan.*failed/);
  expect(await screen.findByText("Scan could not complete")).toBeVisible();
});

it.each([403, 404])("clears the workspace on an item-scoped %s without a principal revision", async (status) => {
  const fallback = installProductionApi();
  let denied = false;
  let itemReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const { pathname } = new URL(String(input), "http://editarr.test");
    if (pathname === "/api/auth/status") return json(signedInStatus({
      userId: 4, username: "member-a", role: "user", capabilities: [
        "media.browse", "scans.start", "scans.findings.read", "scan_jobs.view.own",
        "models.progress.view.own", "reviews.create.own", "reviews.view.own", "system.version.view",
      ],
    }));
    if (pathname === "/api/library-items/movie-1") {
      itemReads++;
      if (denied) return json({ code: "not_found", detail: "Media unavailable" }, 404);
    }
    if (pathname === "/api/media/audio-tracks" && denied) return json({ code: "not_found", detail: "Media unavailable" }, status);
    if (pathname === "/api/scans/history") return json(denied ? [] : [completeScan({ name: "Previously visible result" })]);
    return fallback(input, init);
  }));
  window.location.hash = "#/library/movies/item/movie-1";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await screen.findByText(/Previously visible result.*complete/);
  denied = true;
  fireEvent.click(screen.getByRole("button", { name: "New Scan" }));
  await screen.findByText("Media unavailable");
  await waitFor(() => expect(screen.queryByRole("heading", { name: "Example Movie" })).not.toBeInTheDocument());
  expect(screen.queryByText(/Previously visible result/)).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "New Scan" })).not.toBeInTheDocument();
  const beforeReopen = itemReads;
  denied = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("heading", { name: "Example Movie" });
  expect(itemReads).toBeGreaterThan(beforeReopen);
  expect(screen.queryByRole("dialog", { name: "New Scan" })).not.toBeInTheDocument();
});

async function saveNewReview(createResponse: () => Response) {
  const fallback = installProductionApi();
  const itemReads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    const method = init?.method || "GET";
    if (url.pathname === "/api/library-items/movie-1") itemReads.push(method);
    if (url.pathname === "/api/scans/history") return json([completeScan({ id: "scan-a", scan_id: 1, name: "Peer shared scan" })]);
    if (url.pathname === "/api/library-items/movie-1/reviews" && method === "GET") return json([]);
    if (url.pathname === "/api/scans/scan-a/findings") return json({ revision: 0, findings: [] });
    if (url.pathname === "/api/library-items/movie-1/reviews" && method === "POST") return createResponse();
    return fallback(input, init);
  }));
  window.location.hash = "#/library/movies";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
  fireEvent.click(await screen.findByRole("button", { name: "New Review" }));
  fireEvent.change(screen.getByLabelText("New review name"), { target: { value: "Unsaved draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Start new review" }));
  fireEvent.click(screen.getByRole("button", { name: "Save Review" }));
  return itemReads;
}

it("retains the dirty-draft loss notice when simultaneous denials arrive", async () => {
  const unavailable = vi.fn().mockReturnValueOnce(true).mockReturnValue(false);
  render(<CatalogItemBoundary source="jellyfin" itemId="movie-1" onUnavailable={unavailable}>
    Protected workspace
  </CatalogItemBoundary>);
  const complete = observeMediaRequest(new URL("http://localhost/api/library-items/movie-1"), {});
  await act(async () => {
    await Promise.all([complete(json({ code: "not_found" }, 404)), complete(json({ code: "not_found" }, 404))]);
  });
  expect(unavailable).toHaveBeenCalledOnce();
  expect(screen.getByText("Your unsaved review draft could not be kept because this media is no longer available.")).toBeVisible();
});

it("control: a non-target create failure keeps the unsaved draft", async () => {
  await saveNewReview(() => json({ detail: { code: "scan_not_found", message: "Scan not found" } }, 404));
  await screen.findByText(/Scan not found|Unable to create saved review/);
  expect(screen.getByRole("heading", { name: "Example Movie" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Save Review" })).toBeVisible();
  expect(screen.queryByText("Media unavailable")).not.toBeInTheDocument();
});

it("a create refusal for a since-deleted shared scan keeps the accessible workspace and unsaved draft", async () => {
  const itemReads = await saveNewReview(() => json({ code: "not_found", detail: "Not found" }, 404));
  await screen.findByText("Not found");
  expect(screen.queryByText("Media unavailable")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Example Movie" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Save Review" })).toBeVisible();
  expect(document.body.textContent).toContain("Unsaved draft");
  expect(itemReads.length).toBeGreaterThan(1);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "General" }));
  expect(confirm).toHaveBeenCalledWith("Discard unsaved review changes and leave this page?");
  expect(screen.getByRole("button", { name: "Save Review" })).toBeVisible();
});

it("records the observed outcome: the wipe discards the never-saved draft", async () => {
  const itemReads = await saveNewReview(() => json({ code: "media_unavailable", detail: "Media unavailable" }, 404));
  await screen.findByText("Media unavailable");
  expect(screen.getByText("Your unsaved review draft could not be kept because this media is no longer available.")).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Example Movie" })).not.toBeInTheDocument();
  const before = itemReads.length;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("heading", { name: "Example Movie" });
  expect(itemReads.length).toBeGreaterThan(before);
  expect(document.body.textContent).not.toContain("Unsaved draft");
  const save = screen.queryByRole("button", { name: "Save Review" });
  if (save) expect(save).toBeDisabled();
});