import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { CAPABILITIES } from "../auth/capabilities";
import { setCsrfToken } from "../api/api-transport";
import type { ArchivedItem } from "../api/api-archive";
import { installProductionApi, json, signedInStatus } from "../test/app-test-fixtures";

afterEach(() => {
  cleanup(); setCsrfToken(null); vi.restoreAllMocks(); vi.unstubAllGlobals();
  window.localStorage.clear(); window.location.hash = "";
});

const movie: ArchivedItem = {
  id: "movie-item", source: "radarr", title: "Archived Movie", item_type: "Movie", series: null, season: null, episode: null, last_episode: null,
  path: "/media/movies/archived.mkv",
  scans: [{ id: "scan-1", scan_number: 7, name: "Opening pass", status: "complete", created_at: "2026-01-02T03:04:05Z" }],
  reviews: [{ id: "review-1", name: "Family cut", owner: "pat", updated_at: "2026-01-03T03:04:05Z" }],
  exports: [{ id: 3, format: "mcf", scope: "personal", exported_at: "2026-01-04T03:04:05Z" }],
};
const episode: ArchivedItem = {
  id: "episode-item", source: "sonarr", title: "Archived Episode", item_type: "Episode", series: "Synthetic Sonarr series", season: 2, episode: 5, last_episode: null,
  path: null, scans: [], reviews: [], exports: [],
};

const otherFile: ArchivedItem = {
  id: "other-file-item", source: "manual_files", title: "home-video.mkv", item_type: "Video", series: null, season: null, episode: null, last_episode: null,
  path: "/media/other/home-video.mkv",
  scans: [{ id: "scan-2", scan_number: 9, name: "Manual pass", status: "complete", created_at: "2026-01-05T03:04:05Z" }],
  reviews: [], exports: [],
};

function renderApp(hash: string, archive: ArchivedItem[], capabilities = [...CAPABILITIES]) {
  const fallback = installProductionApi();
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    paths.push(url.pathname === "/api/archive" ? `${url.pathname}${url.search}` : url.pathname);
    if (url.pathname === "/api/auth/status") return json(signedInStatus({ capabilities }));
    if (url.pathname === "/api/archive") {
      const offset = Number(url.searchParams.get("offset")), limit = Number(url.searchParams.get("limit"));
      return json({ total: archive.length, offset, limit, items: archive.slice(offset, offset + limit) });
    }
    return fallback(input, init);
  }));
  window.location.hash = hash;
  render(<AuthenticationGate><App /></AuthenticationGate>);
  return paths;
}

it("lists archived items from both sources without any action controls", async () => {
  renderApp("#/archive", [movie, episode]);
  const page = within((await screen.findByRole("heading", { name: "Archived Movie" })).closest("main")!);
  expect(page.getByText("Radarr · Movie")).toBeVisible();
  expect(page.getByText("Sonarr · Episode")).toBeVisible();
  expect(page.getByText("S02 E05")).toBeVisible();
  expect(page.getByText("Synthetic Sonarr series")).toBeVisible();
  expect(page.getByText("/media/movies/archived.mkv")).toBeVisible();
  expect(page.getByText("Scan #7")).toBeVisible();
  expect(page.getByText("Opening pass")).toBeVisible();
  expect(page.getByText("Family cut")).toBeVisible();
  expect(page.getByText("pat")).toBeVisible();
  expect(page.getByText("MCF")).toBeVisible();
  expect(page.getByText("personal")).toBeVisible();
  expect(page.queryAllByRole("button")).toEqual([]);
  expect(page.queryAllByRole("link")).toEqual([]);
  expect(screen.getByRole("button", { name: "Archive" })).toHaveAttribute("aria-current", "page");
});

it("pages through a long archive", async () => {
  const items = Array.from({ length: 25 }, (_, index) => ({ ...movie, id: `movie-${index}`, title: `Archived ${index + 1}` }));
  const paths = renderApp("#/archive", items);
  expect(await screen.findByRole("heading", { name: "Archived 20" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Archived 21" })).not.toBeInTheDocument();
  expect(screen.getByText("1-20 of 25")).toBeVisible();
  expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByRole("heading", { name: "Archived 21" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Archived 20" })).not.toBeInTheDocument();
  expect(screen.getByText("21-25 of 25")).toBeVisible();
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  expect(paths.filter(path => path.startsWith("/api/archive"))).toEqual(["/api/archive?offset=0&limit=20", "/api/archive?offset=20&limit=20"]);
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  expect(await screen.findByRole("heading", { name: "Archived 1" })).toBeVisible();
});

it("returns to the last page when the archive shrinks past the current page", async () => {
  const items = Array.from({ length: 41 }, (_, index) => ({ ...movie, id: `movie-${index}`, title: `Archived ${index + 1}` }));
  const paths = renderApp("#/archive", items);
  await screen.findByText("1-20 of 41");
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("21-40 of 41");
  items.pop();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("21-40 of 40")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Archived 40" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled();
  expect(paths.filter(path => path.startsWith("/api/archive")).slice(-2)).toEqual(["/api/archive?offset=40&limit=20", "/api/archive?offset=20&limit=20"]);
});

it("returns to the first page when the archive empties while on a later page", async () => {
  const items = Array.from({ length: 41 }, (_, index) => ({ ...movie, id: `movie-${index}`, title: `Archived ${index + 1}` }));
  const paths = renderApp("#/archive", items);
  await screen.findByText("1-20 of 41");
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("21-40 of 41");
  items.splice(0);
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("No archived media.")).toBeVisible();
  await vi.waitFor(() => expect(paths.filter(path => path.startsWith("/api/archive")).slice(-2)).toEqual(["/api/archive?offset=40&limit=20", "/api/archive?offset=0&limit=20"]));
});

it("labels a multi-episode file with its range", async () => {
  renderApp("#/archive", [{ ...episode, episode: 1, last_episode: 3 }]);
  expect(await screen.findByText("S02 E01-E03")).toBeVisible();
});

it("labels a former Other Files record with its source and keeps its original file name", async () => {
  renderApp("#/archive", [otherFile]);
  const page = within((await screen.findByRole("heading", { name: "home-video.mkv" })).closest("main")!);
  expect(page.getByText("Other Files · Video")).toBeVisible();
  expect(page.getByText("/media/other/home-video.mkv")).toBeVisible();
  expect(page.getByText("Scan #9")).toBeVisible();
  expect(page.queryAllByRole("button")).toEqual([]);
  expect(screen.queryByRole("button", { name: "Other Files" })).not.toBeInTheDocument();
});

it("shows an empty state when nothing is archived", async () => {
  renderApp("#/archive", []);
  expect(await screen.findByText("No archived media.")).toBeVisible();
});

it("hides the archive and refuses its route without archive.view", async () => {
  const paths = renderApp("#/archive", [movie], CAPABILITIES.filter(capability => capability !== "archive.view"));
  expect(await screen.findByRole("heading", { name: "Page unavailable" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
  expect(paths.filter(path => path.startsWith("/api/archive"))).toEqual([]);
});

async function landingHeading(hash: string) {
  renderApp(hash, []);
  const heading = (await screen.findAllByRole("heading", { level: 1 }))[0].textContent;
  cleanup();
  return heading;
}

it.each(["#/sources/radarr", "#/sources/sonarr/series/1"])("treats the removed route %s like any unknown route", async hash => {
  const unknown = await landingHeading("#/no-such-route");
  expect(unknown).toBe("Connections");
  expect(await landingHeading(hash)).toBe(unknown);
});
