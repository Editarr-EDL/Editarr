import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { stubOperatorFetch, pinFixtureClock } from "../test-render";
import { operatorLink, operatorRoute } from "../routes";
import { OperatorApp } from "./operator-app";

beforeEach(() => { pinFixtureClock(); window.location.hash = "#/home"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const mount = (onAccessFailure = vi.fn()) => render(<OperatorApp session={{ version: 1, subject: "ryan@example.org" }}
  onAccessFailure={onAccessFailure} onReverify={vi.fn()} />);
const go = (hash: string) => act(() => { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent("hashchange")); });

it("routes hash changes (including back/forward) to pages and highlights the sidebar entry", async () => {
  stubOperatorFetch(); mount();
  expect(await screen.findByRole("heading", { level: 1, name: /Good (morning|afternoon|evening), Ryan/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
  await waitFor(() => expect(screen.getByRole("link", { name: /^Reports/ })).toHaveTextContent("3"));
  go("#/queue/aliases");
  expect(await screen.findByRole("heading", { level: 1, name: "Alias suggestions" })).toBeInTheDocument();
  expect(within(screen.getByRole("navigation", { name: "Operator pages" })).getByRole("link", { name: /^Alias suggestions/ }))
    .toHaveAttribute("aria-current", "page");
  go("#/home");
  expect(await screen.findByRole("heading", { level: 1, name: /Good/ })).toBeInTheDocument();
});

it("opens the navigation drawer and reports an expired session from any read", async () => {
  const denied = vi.fn();
  stubOperatorFetch(path => path.startsWith("/admin/v1/home") ? new Response("{}", { status: 401, headers: { "Content-Type": "application/json" } }) : undefined);
  mount(denied);
  await waitFor(() => expect(denied).toHaveBeenCalledWith(401));
  const menu = screen.getByRole("button", { name: "Open navigation" });
  fireEvent.click(menu);
  expect(menu).toHaveAttribute("aria-expanded", "true");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(menu).toHaveAttribute("aria-expanded", "false");
});

it("parses routes, drops unsafe values and maps legacy views", () => {
  expect(operatorRoute("#/items/w_arrival?version=v_1&filter=f_1&q=Blue%20Comet")).toEqual({ page: "items", path: ["w_arrival"],
    params: { version: "v_1", filter: "f_1", q: "Blue Comet" } });
  expect(operatorRoute("#/items/<x>?filter=<script>").path).toEqual([]);
  expect(operatorRoute("#/items?filter=<script>").params).toEqual({});
  const cursor = "WyJhYitjZC9lZiIsMTJd+/9=";
  const link = operatorLink("activity", { before: cursor, filter: "a+b" });
  expect(operatorRoute(link).params).toEqual({ before: cursor });
  expect(operatorRoute(`#/audit?after=${"A".repeat(8193)}`).params).toEqual({});
  expect(operatorRoute("#/audit?after=bad%20cursor").params).toEqual({});
  expect(operatorRoute("#/versions?filter=candidate&selected=v_1")).toMatchObject({ page: "versions", params: { filter: "candidate", selected: "v_1" } });
  expect(["#/aliases", "#/reports", "#/contributors", "#/operations", "#/audit", "#/backup", "#/overview", "#/recompute"].map(hash => operatorRoute(hash).page))
    .toEqual(["aliases", "reports", "contributors", "health", "audit", "backups", "home", "health"]);
  expect(operatorRoute("#/nowhere").page).toBe("home");
});
