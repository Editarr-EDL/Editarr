import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OperatorAccess } from "./operator-access";
import { readOperatorOverview, readOperatorPage } from "../../api/api-community-operator";
import { operatorFixture, contributorFixture, pageFixture, overviewFixture, operationFixture } from "./test-fixtures";

beforeEach(() => { window.history.replaceState(null, "", "/admin/#/overview"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function installFetch() {
  const fetcher = vi.fn((path: string) => Promise.resolve(Response.json(operatorFixture(new URL(path, "https://operator.test").pathname))));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function navigate(view: string) {
  window.history.pushState(null, "", `/admin/#/${view}`);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

it("keeps payload storage separate from unavailable provider totals and overlapping partial observations", async () => {
  installFetch(); render(<OperatorAccess />);
  await screen.findByText("Service snapshot loaded");
  expect(within(screen.getByRole("region", { name: "Payload storage" })).getByText("12,345")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Provider measurements" })).getAllByText("Unavailable")).toHaveLength(1);
  expect(screen.getByText(/Partial, overlapping application evidence/)).toBeInTheDocument();
  expect(screen.getAllByText("Unknown").length).toBeGreaterThan(1);
  expect(screen.queryByText(/total usage/i)).not.toBeInTheDocument();
});

it("inspects escaped contributor state, historical keys and distinct daily activity", async () => {
  const fetcher = installFetch(); render(<OperatorAccess />);
  await screen.findByText("Service snapshot loaded");
  navigate("contributors?selected=contributor-1");
  await screen.findByRole("region", { name: "Contributor usage" });
  const inspection = screen.getByRole("region", { name: "Resource inspection" });
  expect(within(inspection).getByText(contributorFixture.displayName)).toBeInTheDocument();
  expect(within(inspection).getByText("retired")).toBeInTheDocument();
  expect(within(inspection).getByText("Optimistic revision")).toBeInTheDocument();
  expect(inspection.querySelector("img")).toBeNull();
  expect(fetcher.mock.calls.every(([path]) => path.startsWith("/admin/v1/"))).toBe(true);
});

it("retains a selected route across expired sessions and hides protected data", async () => {
  const fetcher = installFetch(); navigate("contributors?selected=contributor-1"); render(<OperatorAccess />);
  await screen.findByRole("region", { name: "Contributor usage" });
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({}, { status: 401 })));
  fireEvent.click(screen.getByRole("button", { name: "Refresh data" }));
  await screen.findByText("Sign-in required");
  expect(screen.queryByRole("region", { name: "Resource inspection" })).not.toBeInTheDocument();
  expect(window.location.hash).toBe("#/contributors?selected=contributor-1");
  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
});

it("marks old snapshots stale on refresh failure, clears them on navigation and offers retry", async () => {
  const fetcher = installFetch(); render(<OperatorAccess />); await screen.findByText("Service snapshot loaded");
  fetcher.mockImplementationOnce(() => Promise.reject(new TypeError("offline")));
  fireEvent.click(screen.getByRole("button", { name: "Refresh data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Stale snapshot");
  expect(screen.getByRole("region", { name: "Payload storage" })).toBeInTheDocument();
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({}, { status: 503 })));
  navigate("reports");
  expect(await screen.findByRole("alert")).toHaveTextContent("Service data unavailable");
  expect(screen.queryByRole("region", { name: "Payload storage" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh data" }));
  await screen.findByText("Service snapshot loaded");
});

it("handles empty pages, missing inspection and forbidden sessions without stale identity", async () => {
  const fetcher = installFetch(); navigate("operations");
  fetcher.mockImplementation(path => Promise.resolve(Response.json(path === "/admin/v1/session" ? operatorFixture(path) : pageFixture())));
  render(<OperatorAccess />); await screen.findByText("No records");
  fetcher.mockImplementation(path => Promise.resolve(Response.json(operatorFixture(new URL(path, "https://operator.test").pathname))));
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({}, { status: 404 })));
  navigate("contributors?selected=missing");
  expect(await screen.findByRole("alert")).toHaveTextContent("Selected resource not found");
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({}, { status: 403 })));
  fireEvent.click(screen.getByRole("button", { name: "Refresh data" }));
  await screen.findByText("Operator access denied");
  expect(screen.queryByText("fixture-operator")).not.toBeInTheDocument();
});

it("cancels obsolete reads and ignores late data after navigation", async () => {
  const fetcher = installFetch(); render(<OperatorAccess />); await screen.findByText("Service snapshot loaded");
  let finish!: (response: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh data" }));
  navigate("backup"); await screen.findByRole("heading", { name: "Backup and recovery" });
  finish(Response.json({ ...overviewFixture, storage: { ...overviewFixture.storage, globalBytes: 9999999 } }));
  await waitFor(() => expect(screen.queryByText("9,999,999")).not.toBeInTheDocument());
});

it("bounds pages and response bytes and rejects extra secret fields or provider claims", async () => {
  const signal = new AbortController().signal;
  for (const value of [pageFixture(Array.from({ length: 21 }, () => contributorFixture)),
    pageFixture([{ ...contributorFixture, discordUserId: "private" }])]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(value)));
    await expect(readOperatorPage("contributors", null, signal)).rejects.toThrow();
  }
  for (const value of [{ ...overviewFixture, databaseSize: { ...overviewFixture.databaseSize, value: 12345 } },
    { ...overviewFixture, activity: { ...overviewFixture.activity, providerVerified: true } }]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(value)));
    await expect(readOperatorOverview(signal)).rejects.toThrow();
  }
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(" ".repeat(131073), { headers: { "Content-Type": "application/json" } })));
  await expect(readOperatorOverview(signal)).rejects.toThrow();
});

it("distinguishes identical operation IDs scoped to different kinds", async () => {
  const fetcher = installFetch(); navigate("operations");
  fetcher.mockImplementation(path => Promise.resolve(Response.json(path === "/admin/v1/dashboard/operations" ?
    pageFixture([operationFixture, { ...operationFixture, kind: "vote", state: "pending" }]) : operatorFixture(path))));
  render(<OperatorAccess />); await screen.findByText("Service snapshot loaded");
  const links = screen.getAllByRole("link", { name: /^Inspect / });
  expect(links[0].getAttribute("href")).not.toBe(links[1].getAttribute("href"));
  const href = links[1].getAttribute("href")!;
  window.history.pushState(null, "", `/admin/${href}`);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await waitFor(() => expect(within(screen.getByRole("region", { name: "Resource inspection" })).getByText("vote")).toBeInTheDocument());
});