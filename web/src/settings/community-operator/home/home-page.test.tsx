import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { enableDialogs, renderOperator, stubOperatorFetch } from "../test-render";
import { HomePage } from "./home-page";

beforeEach(enableDialogs);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("summarises the period, decisions, updates, leaders and service health", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<HomePage params={{ period: "30d" }} />);
  expect(await screen.findByText(/5 decisions are waiting, the oldest for 4 d\. The service is healthy and the last backup verified/)).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([path]) => path === "/admin/v1/home?period=30d")).toBe(true);
  expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");
  const attention = screen.getByRole("heading", { name: /Needs attention/ }).closest("section")!;
  expect(within(attention).getByText(/^Report · Inaccurate — Violence › Gore/, { selector: ".tt" })).toBeInTheDocument();
  expect(screen.getByText("+3 filters")).toBeInTheDocument();
  expect(within(screen.getByRole("heading", { name: /Top contributors/ }).closest("section")!).getByRole("link", { name: /Mira Okafor/ }))
    .toHaveAttribute("href", "#/contributors?selected=c-mira");
  expect(screen.getByText("Ready · all checks pass")).toBeInTheDocument();
});

it("starts a reasoned action from a decision only after a fresh inspection", async () => {
  const fetcher = stubOperatorFetch(path => path.startsWith("/admin/v1/moderation/report/") ? { version: 1, resource: "report:r_gore", revision: 0,
    state: { reportId: "r_gore", contributorId: "c-ana", targetType: "filter", targetId: "f_gore", reason: "inaccurate", note: null, createdAt: 1, state: "open" },
    audit: [] } : undefined);
  renderOperator(<HomePage params={{}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Resolve: Report · Inaccurate/ }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([path]) => path === "/admin/v1/moderation/report/r_gore")).toBe(true);
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled());
});
