import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch } from "../test-render";
import { IdLookup } from "./id-lookup";

const reportId = "6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b";
beforeEach(() => { window.location.hash = "#/health"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const search = (ids: { kind: string; id: string; label: string }[]) => ({ version: 1, observedAt: 1, q: reportId, items: [], contributors: [], ids });
const lookUp = (value = reportId) => {
  fireEvent.change(screen.getByLabelText("Resource ID"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Look up" }));
};

it("opens a UUID report through the exact-ID search instead of guessing a contributor", async () => {
  const fetcher = stubOperatorFetch(path => path.startsWith("/admin/v1/search") ? search([{ kind: "report", id: reportId, label: "Inaccurate report" }]) : undefined);
  renderOperator(<IdLookup />); lookUp();
  await waitFor(() => expect(window.location.hash).toBe(`#/reports?selected=${reportId}`));
  expect(fetcher.mock.calls[0][0]).toBe(`/admin/v1/search?q=${reportId}`);
});

it("lists several matches, and offers the type chooser when nothing matches exactly", async () => {
  stubOperatorFetch(() => search([{ kind: "contributor", id: reportId, label: "Mira" }, { kind: "report", id: reportId, label: "Spam report" },
    { kind: "filter", id: "other", label: "not exact" }]));
  renderOperator(<IdLookup />); lookUp();
  expect(await screen.findByRole("link", { name: "Report · Spam report" })).toHaveAttribute("href", `#/reports?selected=${reportId}`);
  expect(screen.getByRole("link", { name: "Contributor · Mira" })).toBeInTheDocument();
  expect(screen.queryByText(/not exact/)).not.toBeInTheDocument();
  expect(window.location.hash).toBe("#/health");
  cleanup(); stubOperatorFetch(() => search([]));
  renderOperator(<IdLookup />); lookUp("sub_1:e4");
  expect(await screen.findByRole("link", { name: "choose the resource type in the inspector" })).toHaveAttribute("href", "#/inspect?selected=sub_1%3Ae4");
});

it("ends the session on a denied lookup and reports other failures inline", async () => {
  stubOperatorFetch(() => new Response("{}", { status: 403, headers: { "Content-Type": "application/json" } }));
  const { services } = renderOperator(<IdLookup />); lookUp();
  await waitFor(() => expect(services.onAccessFailure).toHaveBeenCalledWith(403));
  cleanup(); stubOperatorFetch(() => new Response("{}", { status: 503, headers: { "Content-Type": "application/json" } }));
  renderOperator(<IdLookup />); lookUp();
  expect(await screen.findByRole("alert")).toHaveTextContent("Lookup unavailable");
});
