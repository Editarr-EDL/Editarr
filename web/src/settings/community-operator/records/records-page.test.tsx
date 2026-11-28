import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { pageFixture, suggestionFixture, versionRowFixture } from "../test-fixtures";
import { renderOperator, stubOperatorFetch, pinFixtureClock } from "../test-render";
import { AllContributors } from "../contributors/all-contributors";
import { ServiceRecords } from "../health/service-records";
import { RecordsPage } from "./records-page";

beforeEach(() => pinFixtureClock());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("lists versions by status with readable columns, paging and the selected version's inspection", async () => {
  const fetcher = stubOperatorFetch(path => path.startsWith("/admin/v1/versions?") ?
    { version: 1, versions: [versionRowFixture, { ...versionRowFixture, versionId: "version-2", status: "merged_into", mergedInto: "version-1" }], nextCursor: "next-1" } : undefined);
  renderOperator(<RecordsPage view="versions" params={{ filter: "candidate", selected: "version-1" }} />);
  const table = await screen.findByRole("table", { name: "All versions" });
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/versions?status=candidate");
  expect(within(table).getAllByRole("link", { name: "Remastered" })[0]).toHaveAttribute("href", "#/items/work-1?version=version-1");
  expect(within(table).getAllByText("3 of 4")).toHaveLength(2);
  expect(within(table).getByRole("link", { name: "version-1" })).toHaveAttribute("href", "#/versions?filter=all&selected=version-1");
  expect(screen.getByRole("link", { name: "Candidate" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Next page" })).toHaveAttribute("href", "#/versions?filter=candidate&after=next-1");
  expect(await screen.findByRole("button", { name: "Confirm version" })).toBeInTheDocument();
  expect(await screen.findByRole("region", { name: "Master filters" })).toBeInTheDocument();
});

it("defaults alias suggestions to pending and lists open reports with private notes", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<RecordsPage view="aliases" params={{}} />);
  expect(await screen.findByText("“Remastered”")).toBeInTheDocument();
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/alias-suggestions?state=pending");
  cleanup();
  renderOperator(<RecordsPage view="reports" params={{}} />);
  const table = await screen.findByRole("table", { name: "All reports" });
  expect(within(table).getByText("<script>window.hostileTextExecuted=true</script>")).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "filter-1" })).toHaveAttribute("href", "#/filters/filter-1");
  expect(suggestionFixture.state).toBe("pending");
});

it("lists every contributor and the operations log with handler observations", async () => {
  stubOperatorFetch(path => path === "/admin/v1/dashboard/contributors" ? pageFixture([{ contributorId: "c-idle", displayName: "Idle Ida",
    state: "suspended", createdAt: 1 }], "cursor-2") : undefined);
  renderOperator(<AllContributors params={{ tab: "all" }} />);
  expect(await screen.findByRole("link", { name: "Idle Ida" })).toHaveAttribute("href", "#/contributors?selected=c-idle");
  expect(screen.getByText("Suspended")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Next page" })).toHaveAttribute("href", "#/contributors?tab=all&after=cursor-2");
  cleanup();
  renderOperator(<ServiceRecords params={{}} />);
  const handlers = await screen.findByRole("table", { name: "Daily handler observations" });
  expect(within(handlers).getByRole("rowheader", { name: "Download" }).closest("tr")).toHaveTextContent("3");
  expect(within(handlers).getByRole("rowheader", { name: "All requests" }).closest("tr")).toHaveTextContent("40");
  expect(await screen.findByRole("table", { name: "Operations log" })).toHaveTextContent("Publication");
});
