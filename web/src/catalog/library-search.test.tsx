import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mediaItem } from "../test/app-test-fixtures";
import type { JellyfinMediaItem } from "../api/api-media-types";
import { LibrarySearch } from "./library-search";
import { activeTokens, searchSuggestions, type LibraryFilters, type SearchSuggestion } from "./library-search-suggestions";

const values = { ratings: ["G", "PG", "PG-13", "R"], resolutions: ["4K", "1080p"] };
const noFilters: LibraryFilters = { filter: "all", ratingFilter: "all", resolutionFilter: "all" };
const movie = (name: string) => ({ ...mediaItem, id: name, name, sort_name: name }) as unknown as JellyfinMediaItem;
const labels = (suggestions: SearchSuggestion[]) => suggestions.map((suggestion) => suggestion.label);

describe("searchSuggestions", () => {
  it("matches filter values, facet names and EDL wording, leaving out chosen values", () => {
    expect(labels(searchSuggestions("pg", noFilters, values, []))).toEqual(["Rating: PG", "Rating: PG-13"]);
    expect(labels(searchSuggestions("rat", noFilters, values, []))).toEqual(["Rating: G", "Rating: PG", "Rating: PG-13", "Rating: R"]);
    expect(labels(searchSuggestions("no edl", noFilters, values, []))).toEqual(["EDL: Not exported"]);
    expect(labels(searchSuggestions("edl", { ...noFilters, filter: "exported" }, values, []))).toEqual(["EDL: Not exported", "EDL: Outdated"]);
    expect(labels(searchSuggestions("4k", { ...noFilters, resolutionFilter: "4K" }, values, []))).toEqual([]);
  });

  it("lists titles first only for typed text and caps each group", () => {
    const titles = ["Paddington", "Paddington 2", "Pad 3", "Pad 4", "Pad 5", "Pad 6"].map(movie);
    const suggestions = searchSuggestions("pad", noFilters, values, titles);
    expect(suggestions.filter((suggestion) => suggestion.kind === "title")).toHaveLength(5);
    expect(suggestions[0]).toMatchObject({ kind: "title", label: "Paddington" });
    expect(labels(searchSuggestions("", noFilters, values, titles))).toEqual([
      "EDL: Exported", "EDL: Not exported", "EDL: Outdated", "Rating: G", "Rating: PG", "Rating: PG-13", "Rating: R", "Resolution: 4K",
    ]);
  });

  it("suggests the outdated EDL filter and names its token", () => {
    expect(labels(searchSuggestions("outdated", noFilters, values, []))).toEqual(["EDL: Outdated"]);
    expect(labels(searchSuggestions("stale", noFilters, values, []))).toEqual(["EDL: Outdated"]);
    expect(labels(searchSuggestions("edl", { ...noFilters, filter: "outdated" }, values, []))).toEqual(["EDL: Exported", "EDL: Not exported"]);
    expect(activeTokens({ filter: "outdated", ratingFilter: "all", resolutionFilter: "all" }).map((token) => token.label)).toEqual(["EDL outdated"]);
  });

  it("names active filters as tokens", () => {
    expect(activeTokens({ filter: "not_exported", ratingFilter: "PG", resolutionFilter: "4K" }).map((token) => token.label))
      .toEqual(["No EDL", "PG", "4K"]);
  });
});

function Harness({ onOpen }: { onOpen: (item: JellyfinMediaItem) => void }) {
  const [text, setText] = useState("");
  const [filters, setFilters] = useState<LibraryFilters>({ ...noFilters, resolutionFilter: "4K" });
  const suggestions = searchSuggestions(text, filters, values, text ? [movie("Paddington 2")] : []);
  return <LibrarySearch text={text} onTextChange={setText} tokens={activeTokens(filters)} suggestions={suggestions}
    onRemoveToken={(token) => setFilters((current) => ({ ...current, [token.facet]: "all" }))}
    onChoose={(suggestion) => {
      if (suggestion.kind === "title") onOpen(suggestion.item);
      else { setFilters((current) => ({ ...current, [suggestion.token.facet]: suggestion.token.value })); setText(""); }
    }}
    onClear={() => { setText(""); setFilters(noFilters); }} />;
}

describe("LibrarySearch", () => {
  afterEach(cleanup);

  it("chooses suggestions with the keyboard and edits tokens", () => {
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const input = screen.getByRole("combobox", { name: "Search titles or filters" });
    expect(input).toHaveAttribute("aria-expanded", "false");
    fireEvent.change(input, { target: { value: "pg" } });
    expect(input).toHaveAttribute("aria-expanded", "true");
    const listbox = screen.getByRole("listbox", { name: "Search suggestions" });
    expect(input).toHaveAttribute("aria-controls", listbox.id);
    expect(screen.getByRole("group", { name: "Titles" })).toHaveTextContent("Paddington 2");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["Paddington 2", "Rating: PG", "Rating: PG-13"]);

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Rating: PG-13" }).id);
    expect(screen.getByRole("option", { name: "Rating: PG-13" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getByRole("option", { name: "Paddington 2" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "Remove PG filter" })).toBeVisible();
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).not.toHaveAttribute("aria-activedescendant");

    expect(screen.getAllByRole("button", { name: /^Remove/ }).map((button) => button.getAttribute("aria-label")))
      .toEqual(["Remove PG filter", "Remove 4K filter"]);
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(screen.queryByRole("button", { name: "Remove 4K filter" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove PG filter" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Remove PG filter" }));
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(input).toHaveFocus();

    fireEvent.change(input, { target: { value: "padd" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveValue("padd");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ name: "Paddington 2" }));
  });

  it("keeps typed text when Backspace edits it and clears everything at once", () => {
    render(<Harness onOpen={vi.fn()} />);
    const input = screen.getByRole("combobox", { name: "Search titles or filters" });
    fireEvent.change(input, { target: { value: "r" } });
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(screen.getByRole("button", { name: "Remove 4K filter" })).toBeVisible();
    fireEvent.click(screen.getByRole("option", { name: "Rating: G" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("gives options valid ids whatever their values contain", () => {
    render(<LibrarySearch text="rating" onTextChange={vi.fn()} tokens={[]} onRemoveToken={vi.fn()} onChoose={vi.fn()} onClear={vi.fn()}
      suggestions={searchSuggestions("rating", noFilters, { ratings: ["Not Rated", "TV-MA #1"], resolutions: [] }, [])} />);
    const input = screen.getByRole("combobox", { name: "Search titles or filters" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const id = input.getAttribute("aria-activedescendant") ?? "";
    expect(id).toMatch(/^[^\s#]+$/);
    expect(document.getElementById(id)).toHaveTextContent("Rating: TV-MA #1");
  });
});

