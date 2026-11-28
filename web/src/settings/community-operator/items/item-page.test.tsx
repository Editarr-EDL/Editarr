import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { enableDialogs, renderOperator, stubOperatorFetch } from "../test-render";
import { ItemPage } from "./item-page";
import { ItemsPage } from "./items-page";

beforeEach(() => { enableDialogs(); window.location.hash = "#/items"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("lists items with versions, votes and reports and routes sorting through the hash", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<ItemsPage params={{ sort: "votes" }} />);
  const table = await screen.findByRole("table", { name: /Items, sorted by Most votes/ });
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/items?sort=votes");
  const arrival = within(table).getByRole("link", { name: "Arrival Road" }).closest("tr")!;
  expect(within(arrival).getByText("Remastered 4K · candidate")).toBeInTheDocument();
  expect(within(arrival).getByText("+381")).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "Example Series · S01E03" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Title" })[0]);
  expect(window.location.hash).toBe("#/items?sort=title");
});

it("opens the most-uploaded confirmed version, its timeline and table, and the selected filter card", async () => {
  stubOperatorFetch();
  renderOperator(<ItemPage workId="w_arrival" params={{ filter: "f_gore" }} />);
  expect(await screen.findByRole("heading", { level: 1, name: /Arrival Road/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Director's Cut/ })).toHaveAttribute("aria-pressed", "true");
  const card = await screen.findByRole("complementary", { name: "Violence › Gore" });
  expect(within(card).getByText("3 of 4 counted")).toBeInTheDocument();
  expect(within(card).getByText("±0.8 s")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Violence filters" }).querySelector("a[aria-current=true]")).not.toBeNull();
  expect(within(screen.getByRole("table", { name: "Master filters" })).getByRole("link", { name: "Violence › Gore" })).toHaveAttribute("aria-current", "true");
  expect(screen.getByText("rev 7").closest("li")).toHaveClass("cur");
});

it("excludes a voter's vote using the listed moderation revision after re-inspection", async () => {
  const fetcher = stubOperatorFetch(path => path.startsWith("/admin/v1/moderation/vote/") ? { version: 1, resource: "vote:f_gore:c-ana", revision: 0,
    state: { filterId: "f_gore", contributorId: "c-ana", vote: "down", excluded: 0, updatedAt: 1 }, audit: [] } :
    path === "/admin/v1/moderation" ? { version: 1, actionId: "x", resource: "vote:f_gore:c-ana", revision: 1, outcome: "applied" } : undefined);
  renderOperator(<ItemPage workId="w_arrival" params={{ filter: "f_gore" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Exclude Ana Ruiz's down vote" }));
  fireEvent.change(await screen.findByLabelText("Operator reason"), { target: { value: "Coordinated voting" } });
  fetcher.mockImplementationOnce((_path: string, init?: RequestInit) => Promise.resolve(Response.json({ version: 1,
    actionId: JSON.parse(String(init?.body)).actionId, resource: "vote:f_gore:c-ana", revision: 1, outcome: "applied" })));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(write[1]?.body))).toMatchObject({ command: "exclude_vote", resource: "vote", targetId: "f_gore", contributorId: "c-ana",
    expectedRevision: 0, reason: "Coordinated voting" });
});
