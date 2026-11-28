import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch } from "../test-render";
import { GlobalSearch } from "./global-search";

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); window.location.hash = "#/home"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("never shows or opens an earlier query's results while a new query is pending", async () => {
  let release: (() => void) | null = null;
  stubOperatorFetch(path => path.includes("q=comet") ? new Promise<Response>(resolve => { release = () => resolve(Response.json({
    version: 1, observedAt: 1, q: "comet", items: [], contributors: [], ids: [] })); }) : undefined);
  renderOperator(<GlobalSearch />);
  const input = screen.getByLabelText("Search titles, contributors or any ID");
  expect(input).toHaveAttribute("maxLength", "100");
  fireEvent.focus(input); fireEvent.change(input, { target: { value: "arrival" } });
  await act(() => vi.advanceTimersByTimeAsync(300));
  const results = await screen.findByRole("region", { name: "Search results" });
  expect(await within(results).findByRole("link", { name: "Arrival Road (1977)" })).toBeInTheDocument();
  fireEvent.change(input, { target: { value: "comet" } });
  expect(within(results).queryByRole("link", { name: "Arrival Road (1977)" })).not.toBeInTheDocument();
  expect(within(results).getByText("Searching…")).toBeInTheDocument();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(window.location.hash).toBe("#/home");
  await act(() => vi.advanceTimersByTimeAsync(300));
  await act(async () => { release?.(); });
  expect(await within(results).findByText("No matches")).toBeInTheDocument();
});

it("settles to an error instead of an uncaught exception when the query is refused", async () => {
  stubOperatorFetch(() => Response.json({ error: { code: "invalid_search_query" } }, { status: 400 }));
  renderOperator(<GlobalSearch />);
  const input = screen.getByLabelText("Search titles, contributors or any ID");
  fireEvent.focus(input); fireEvent.change(input, { target: { value: "a".repeat(100) } });
  await act(() => vi.advanceTimersByTimeAsync(300));
  expect(await screen.findByRole("alert")).toHaveTextContent("Search unavailable");
  expect(screen.queryByText("Searching…")).not.toBeInTheDocument();
});
