import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { installProductionApi, json, mediaItem } from "./app-test-fixtures";

const words = ["Arrival", "Blue", "Comet", "Dune", "Echo", "Falcon", "Glass", "Harbor", "Island", "Jade", "Kingdom", "Lantern",
  "Meadow", "North", "Ocean", "Paper", "Quiet", "River", "Silver", "Tide", "Union", "Valley", "Winter", "Xenon", "Yellow", "Zephyr"];
/**
 * `size` titles spread evenly over the letters A to Y, sorted by title ("Arrival 00", "Arrival 01", …), from
 * 1985 to 2024 with every 25th title from the fourth on missing a year.
 */
function generatedLibrary(size: number) {
  const perLetter = size / 25;
  const width = Math.max(2, String(perLetter - 1).length);
  return Array.from({ length: size }, (_, index) => {
    const name = `${words[Math.floor(index / perLetter)]} ${String(index % perLetter).padStart(width, "0")}`;
    return { ...mediaItem, id: `movie-${index}`, name, sort_name: name, year: index % 25 === 3 ? null : 1985 + (index % 40) };
  });
}
type Movie = ReturnType<typeof generatedLibrary>[number];

/** The listing in the server's order: by title, or by year with titles missing one last; then its jump groups. */
function ordered(library: Movie[], sortBy: string | null, direction: string | null) {
  const year = (item: Movie) => item.year ?? Number.POSITIVE_INFINITY;
  const sorted = sortBy === "year" ? [...library].sort((left, right) => year(left) - year(right) || left.name.localeCompare(right.name)) : library;
  return direction === "desc" ? [...sorted].reverse() : sorted;
}
function jumpGroups(items: Movie[], sortBy: string | null) {
  const groupOf = (item: Movie) => {
    if (sortBy === "title") return { key: item.name[0], label: item.name[0] };
    if (item.year === null) return { key: ":none", label: "No year" };
    const decade = Math.floor(item.year / 10) * 10;
    return { key: String(decade), label: `${decade}s` };
  };
  if (sortBy !== "title" && sortBy !== "year") return [];
  return items.reduce<Array<{ key: string; label: string; offset: number }>>((groups, item, offset) => {
    const group = groupOf(item);
    return groups.at(-1)?.key === group.key ? groups : [...groups, { ...group, offset }];
  }, []);
}

function installCatalog(size = 250) {
  const library = generatedLibrary(size);
  let failNext = false;
  const failStarts = new Set<number>();
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
    if (failNext || failStarts.has(start)) { failNext = false; failStarts.delete(start); return json({ detail: "Jellyfin is unavailable" }, 502); }
    const matching = ordered(url.searchParams.get("edl_filter") === "exported" ? library.slice(0, 30) : library,
      sortBy, url.searchParams.get("sort_direction"));
    return json({
      items: matching.slice(start, start + limit), total_count: matching.length,
      rating_values: ["PG-13"], resolution_values: ["4K"], jump_groups: jumpGroups(matching, sortBy),
    });
  }));
  return {
    library,
    requests,
    hold: () => { hold = true; },
    failOnce: (start: number) => failStarts.add(start),
    release: (fail = false) => { hold = false; failNext = fail; pending.splice(0).forEach((resolve) => resolve()); },
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
    await status("Loading titles starting with T…");
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

  it("jumps to a decade beyond the loaded titles and marks it current", async () => {
    const api = installCatalog();
    renderLibrary();
    await status("Showing 100 of 250");
    fireEvent.click(screen.getByRole("button", { name: "Sort by Title, ascending" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Year" }));
    const rail = await screen.findByRole("toolbar", { name: "Jump to decade" });
    const listing = ordered(api.library, "year", "asc");
    const target = jumpGroups(listing, "year").find((group) => group.key === "2010")!;
    expect(target.offset).toBeGreaterThan(100);

    api.hold();
    fireEvent.click(within(rail).getByRole("button", { name: "Jump to 2010s" }));
    await status("Loading titles for 2010s…");
    api.release();
    await waitFor(() => expect(screen.getByRole("button", { name: `Open ${listing[target.offset].name}` })).toHaveFocus());
    expect(api.requests.slice(-2)).toEqual(["year:all:0:100", "year:all:100:100"]);
    // The jumped-to title is the first one in view, so its decade is current.
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      const index = Number((this as HTMLElement).dataset?.catalogIndex ?? -1);
      const top = (index - target.offset) * 100;
      return { top, bottom: top + 90, height: 90, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
    });
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(within(rail).getByRole("button", { name: "Jump to 2010s" })).toHaveAttribute("aria-current", "true"));
    expect(within(rail).getByRole("button", { name: "Jump to 2000s" })).not.toHaveAttribute("aria-current");
  });

  it("sorts from one menu and shows the rail of the sort's groups in its direction", async () => {
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
    const decades = await screen.findByRole("toolbar", { name: "Jump to decade" });
    expect(screen.queryByRole("toolbar", { name: "Jump to letter" })).not.toBeInTheDocument();
    expect(within(decades).getAllByRole("button").map((button) => [button.textContent, button.getAttribute("aria-label")])).toEqual([
      ["1980s", "Jump to 1980s"], ["1990s", "Jump to 1990s"], ["2000s", "Jump to 2000s"], ["2010s", "Jump to 2010s"],
      ["2020s", "Jump to 2020s"], ["—", "Jump to No year"],
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Year, ascending" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Descending" }));
    await status("Showing 100 of 250");
    expect(within(screen.getByRole("toolbar", { name: "Jump to decade" })).getAllByRole("button").map((button) => button.textContent))
      .toEqual(["—", "2020s", "2010s", "2000s", "1990s", "1980s"]);
    expect(screen.getByRole("button", { name: "Sort by Year, descending" })).toHaveTextContent("Year ↓");
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getByRole("columnheader", { name: /Year/ })).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Title/ })).getByRole("button"));
    expect(screen.getByRole("button", { name: "Sort by Title, ascending" })).toBeVisible();
    expect(await screen.findByRole("toolbar", { name: "Jump to letter" })).toBeVisible();
  });

  it("clears earlier results while a new query loads and keeps them hidden when it fails", async () => {
    const api = installCatalog();
    renderLibrary();
    await status("Showing 100 of 250");
    api.hold();
    const search = screen.getByRole("combobox", { name: "Search titles or filters" });
    fireEvent.change(search, { target: { value: "edl exp" } });
    fireEvent.click(await screen.findByRole("option", { name: "EDL: Exported" }));
    await waitFor(() => expect(api.requests.at(-1)).toBe("title:exported:0:100"));
    expect(screen.queryByRole("list", { name: "Movies" })).not.toBeInTheDocument();
    expect(screen.queryByRole("toolbar", { name: "Jump to letter" })).not.toBeInTheDocument();
    expect(screen.getByText("Loading library…")).toBeVisible();
    const refresh = screen.getByRole("button", { name: "Refresh library" });
    expect(refresh).toHaveAttribute("aria-disabled", "true");
    expect(refresh).toBeEnabled();
    expect(search).toHaveFocus();
    fireEvent.change(search, { target: { value: "pg" } });
    expect(await screen.findByRole("option", { name: "Rating: PG-13" })).toBeVisible();

    api.release(true);
    expect(await screen.findByRole("alert")).toHaveTextContent("Jellyfin is unavailable");
    expect(screen.queryByRole("button", { name: /^Open Arrival/ })).not.toBeInTheDocument();
    expect(screen.queryByText("No matching items")).not.toBeInTheDocument();
  });

  it("returns to the top of the results when the query changes after scrolling", async () => {
    installCatalog();
    renderLibrary();
    await status("Showing 100 of 250");
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    vi.spyOn(window, "scrollY", "get").mockReturnValue(2000);
    // Scrolled down: the toolbar (100px) is stuck and the start of the results lies 400px above the window.
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(
      { top: -400, bottom: -300, height: 100, left: 0, right: 0, width: 0, x: 0, y: -400, toJSON: () => ({}) } as DOMRect);
    fireEvent(window, new Event("resize"));
    const search = screen.getByRole("combobox", { name: "Search titles or filters" });
    search.focus();
    fireEvent.change(search, { target: { value: "edl exp" } });
    fireEvent.click(await screen.findByRole("option", { name: "EDL: Exported" }));
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 1500 }));
    expect(search).toHaveFocus();
  });

  it("jumps across a large library with one request and a bounded window", async () => {
    const api = installCatalog(10_000);
    renderLibrary();
    await status("Showing 100 of 10000");
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Jump to letter" })).getByRole("button", { name: "Jump to Y" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Yellow 000" })).toHaveFocus());
    expect(api.requests).toEqual(["title:all:0:100", "title:all:9600:100"]);
    await status("Showing 9601–9700 of 10000");
    expect(within(screen.getByRole("list", { name: "Movies" })).getAllByRole("listitem")).toHaveLength(100);

    fireEvent.click(screen.getByRole("button", { name: "Load earlier" }));
    await status("Showing 9501–9700 of 10000");
    expect(screen.getByRole("button", { name: "Open Xenon 399" })).toBeVisible();
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Jump to letter" })).getByRole("button", { name: "Jump to X" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Xenon 000" })).toHaveFocus());
    await status("Showing 9201–9700 of 10000");
    expect(api.requests.slice(2)).toEqual(["title:all:9500:100", "title:all:9200:300"]);
  });

  it("retries the request that failed, keeping the window shown when a jump fails", async () => {
    const api = installCatalog(10_000);
    renderLibrary();
    await status("Showing 100 of 10000");
    api.failOnce(100);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Jellyfin is unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await status("Showing 200 of 10000");
    expect(api.requests.slice(1)).toEqual(["title:all:100:100", "title:all:100:100"]);

    api.failOnce(9600);
    const rail = screen.getByRole("toolbar", { name: "Jump to letter" });
    fireEvent.click(within(rail).getByRole("button", { name: "Jump to Y" }));
    await status("Loading titles starting with Y…");
    expect(await screen.findByRole("alert")).toHaveTextContent("Jellyfin is unavailable");
    await status("Showing 200 of 10000");
    expect(screen.getByRole("button", { name: "Open Arrival 000" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Yellow 000" })).toHaveFocus());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await status("Showing 9601–9700 of 10000");

    api.failOnce(9500);
    fireEvent.click(screen.getByRole("button", { name: "Load earlier" }));
    expect(await screen.findByRole("alert")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await status("Showing 9501–9700 of 10000");
    expect(api.requests.slice(-2)).toEqual(["title:all:9500:100", "title:all:9500:100"]);
  });

  it("moves focus to the first title when Load earlier reaches the start", async () => {
    installCatalog(10_000);
    renderLibrary();
    await status("Showing 100 of 10000");
    const rail = () => screen.getByRole("toolbar", { name: "Jump to letter" });
    fireEvent.click(within(rail()).getByRole("button", { name: "Jump to Y" }));
    await status("Showing 9601–9700 of 10000");
    fireEvent.click(within(rail()).getByRole("button", { name: "Jump to B" }));
    await status("Showing 401–500 of 10000");
    for (const shown of ["301–500", "201–500", "101–500"]) {
      const earlier = screen.getByRole("button", { name: "Load earlier" });
      earlier.focus();
      fireEvent.click(earlier);
      await status(`Showing ${shown} of 10000`);
      expect(screen.getByRole("button", { name: "Load earlier" })).toHaveFocus();
    }
    fireEvent.click(screen.getByRole("button", { name: "Load earlier" }));
    await status("Showing 500 of 10000");
    expect(screen.queryByRole("button", { name: "Load earlier" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Arrival 000" })).toHaveFocus();
  });
});

