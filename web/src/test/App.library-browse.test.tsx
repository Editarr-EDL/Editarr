import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { installProductionApi, json, mediaItem } from "./app-test-fixtures";

const words = ["Arrival", "Blue", "Comet", "Dune", "Echo", "Falcon", "Glass", "Harbor", "Island", "Jade", "Kingdom", "Lantern",
  "Meadow", "North", "Ocean", "Paper", "Quiet", "River", "Silver", "Tide", "Union", "Valley", "Winter", "Xenon", "Yellow", "Zephyr"];
// 250 titles, ten for each letter from A to Y, sorted by title.
const library = Array.from({ length: 250 }, (_, index) => {
  const name = `${words[Math.floor(index / 10) % 26]} ${String(index % 10).padStart(2, "0")}`;
  return { ...mediaItem, id: `movie-${index}`, name, sort_name: name, year: 2000 + (index % 20) };
});

function installCatalog() {
  const requests: string[] = [];
  const pending: Array<() => void> = [];
  let hold = false;
  const base = installProductionApi();
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname !== "/api/libraries/movies/items") return base(input, init);
    const start = Number(url.searchParams.get("start_index"));
    const limit = Number(url.searchParams.get("limit"));
    const sortBy = url.searchParams.get("sort_by");
    requests.push(`${sortBy}:${url.searchParams.get("edl_filter")}:${start}:${limit}`);
    if (hold) await new Promise<void>((resolve) => pending.push(resolve));
    const matching = url.searchParams.get("edl_filter") === "exported" ? library.slice(0, 30) : library;
    const offsets: Record<string, number> = {};
    if (sortBy === "title") matching.forEach((item, index) => { offsets[item.name[0]] ??= index; });
    return json({
      items: matching.slice(start, start + limit), total_count: matching.length,
      rating_values: ["PG-13"], resolution_values: ["4K"], title_letter_offsets: offsets,
    });
  }));
  return {
    requests,
    hold: () => { hold = true; },
    release: () => { hold = false; pending.splice(0).forEach((resolve) => resolve()); },
  };
}

function renderLibrary() {
  window.location.hash = "#/library/movies";
  render(<AuthenticationGate><App /></AuthenticationGate>);
}

const status = (text: string) => screen.findByText(text, { selector: "[role='status']" });

describe("continuous library browsing", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("loads further pages once each and starts over when a filter changes", async () => {
    const api = installCatalog();
    renderLibrary();
    await status("Showing 100 of 250");
    expect(screen.getByText("250 titles")).toBeVisible();
    expect(within(screen.getByRole("list", { name: "Movies" })).getAllByRole("listitem")).toHaveLength(100);

    api.hold();
    const loadMore = screen.getByRole("button", { name: "Load more" });
    loadMore.focus();
    fireEvent.click(loadMore);
    fireEvent.click(loadMore);
    await waitFor(() => expect(loadMore).toHaveAttribute("aria-disabled", "true"));
    api.release();
    await status("Showing 200 of 250");
    expect(loadMore).toHaveFocus();
    expect(api.requests).toEqual(["title:all:0:100", "title:all:100:100"]);

    const search = screen.getByRole("combobox", { name: "Search titles or filters" });
    fireEvent.change(search, { target: { value: "edl exp" } });
    fireEvent.click(await screen.findByRole("option", { name: "EDL: Exported" }));
    await status("Showing 30 of 30");
    expect(screen.getByText("30 titles")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(api.requests.slice(2)).toEqual(["title:exported:0:100"]);
  });

  it("jumps to a letter beyond the loaded titles", async () => {
    const api = installCatalog();
    renderLibrary();
    await status("Showing 100 of 250");
    const rail = screen.getByRole("toolbar", { name: "Jump to letter" });
    expect(within(rail).getByRole("button", { name: "Jump to numbers and symbols" })).toBeDisabled();

    api.hold();
    fireEvent.click(within(rail).getByRole("button", { name: "Jump to T" }));
    await status("Loading titles up to T…");
    expect(rail).toHaveAttribute("aria-busy", "true");
    api.release();
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Tide 00" })).toHaveFocus());
    expect(api.requests).toEqual(["title:all:0:100", "title:all:100:100"]);
    await status("Showing 200 of 250");

    fireEvent.click(within(rail).getByRole("button", { name: "Jump to B" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Blue 00" })).toHaveFocus());
    expect(api.requests).toHaveLength(2);

    fireEvent.click(within(rail).getByRole("button", { name: "Jump to Y" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Yellow 00" })).toHaveFocus());
    expect(api.requests).toEqual(["title:all:0:100", "title:all:100:100", "title:all:200:100"]);
  });

  it("sorts from one menu and hides the rail outside title order", async () => {
    const api = installCatalog();
    renderLibrary();
    await status("Showing 100 of 250");
    const sort = screen.getByRole("button", { name: "Sort by Title, ascending" });
    expect(sort).toHaveTextContent("Title ↑");
    fireEvent.keyDown(sort, { key: "ArrowDown" });
    const menu = screen.getByRole("menu", { name: "Sort by Title, ascending" });
    expect(within(menu).getByRole("menuitemradio", { name: "Title" })).toHaveFocus();
    expect(within(menu).getByRole("menuitemradio", { name: "Title" })).toHaveAttribute("aria-checked", "true");
    expect(within(menu).getByRole("menuitemradio", { name: "Ascending" })).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(within(menu).getByRole("menuitemradio", { name: "Year" })).toHaveFocus();
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "Year" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sort by Year, ascending" })).toHaveFocus();
    await waitFor(() => expect(api.requests).toContain("year:all:0:100"));
    await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Jump to letter" })).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sort by Year, ascending" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Descending" }));
    expect(screen.getByRole("button", { name: "Sort by Year, descending" })).toHaveTextContent("Year ↓");
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getByRole("columnheader", { name: /Year/ })).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Title/ })).getByRole("button"));
    expect(screen.getByRole("button", { name: "Sort by Title, ascending" })).toBeVisible();
    expect(await screen.findByRole("toolbar", { name: "Jump to letter" })).toBeVisible();
  });
});
