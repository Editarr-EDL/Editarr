import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { enableDialogs, renderOperator, stubOperatorFetch, pinFixtureClock } from "../test-render";
import { ItemPage } from "./item-page";
import { ItemsPage } from "./items-page";

beforeEach(() => { pinFixtureClock(); enableDialogs(); window.location.hash = "#/items"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

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
  expect(within(card).getByText("3 of 5 counted")).toBeInTheDocument();
  const implicit = within(card).getByText(/implicit coverage negative/).closest("li")!;
  expect(within(implicit).queryByRole("link", { name: /^Inspect/ })).not.toBeInTheDocument();
  expect(within(card).getByText(/implicit support/).closest("li")!.querySelector("a[href*='kind=attestation']")).toBeNull();
  expect(within(card).getAllByRole("link", { name: /^Inspect/ }).map(link => link.textContent)).toEqual(["Inspect Mira Okafor's attestation", "Inspect Jonas Lind's attestation", "Inspect Theo Marsh's attestation"]);
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

it("pages revision history with the service cursor and marks only the service's current revision", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<ItemPage workId="w_arrival" params={{}} />);
  const history = await screen.findByRole("region", { name: "Revision history" });
  expect(within(history).getByText("rev 7").closest("li")).toHaveClass("cur");
  expect(within(history).getAllByText("current")).toHaveLength(1);
  expect(within(history).getByText("rev 2").closest("li")).toHaveTextContent("no known changes; other changes unavailable");
  const older = within(history).getByRole("link", { name: "Older" });
  expect(older).toHaveAttribute("href", "#/items/w_arrival?version=v_arrival_dc&before=WzJd%2B%2F%3D%3D");
  cleanup();
  renderOperator(<ItemPage workId="w_arrival" params={{ version: "v_arrival_dc", before: "WzJd+/==" }} />);
  const page2 = await screen.findByRole("region", { name: "Revision history" });
  expect(await within(page2).findByText("rev 1")).toBeInTheDocument();
  expect(within(page2).queryByText("rev 7")).not.toBeInTheDocument();
  expect(within(page2).queryByText("current")).not.toBeInTheDocument();
  expect(within(page2).getByRole("link", { name: "Newest" })).toHaveAttribute("href", "#/items/w_arrival?version=v_arrival_dc");
  expect(within(page2).queryByRole("link", { name: "Older" })).not.toBeInTheDocument();
  expect(fetcher.mock.calls.some(([path]) => path === "/admin/v1/items/w_arrival/versions/v_arrival_dc?before=WzJd%2B%2F%3D%3D")).toBe(true);
  expect(screen.getByRole("table", { name: "Master filters" })).toBeInTheDocument();
});
