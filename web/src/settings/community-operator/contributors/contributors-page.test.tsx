import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch } from "../test-render";
import { ContributorsPage } from "./contributors-page";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("ranks contributors by accepted filters and profiles the selection with account actions", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<ContributorsPage params={{ metric: "uploads", period: "7d" }} />);
  const table = await screen.findByRole("table", { name: "Leaderboard by Uploads" });
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/contributors/leaderboard?period=7d&metric=uploads");
  expect(within(table).getAllByRole("row")).toHaveLength(10);
  expect(screen.getByRole("list", { name: "Top three" })).toHaveTextContent(/1st.*Mira Okafor/);
  const profile = await screen.findByRole("complementary", { name: "Mira Okafor" });
  expect(within(profile).getByText("accepted filters")).toBeInTheDocument();
  expect(within(profile).getByRole("button", { name: "Suspend contributor" })).toBeInTheDocument();
  expect(within(profile).getByRole("button", { name: "Quarantine contributor" })).toBeInTheDocument();
  expect(within(profile).getByText(/1,912 requests/)).toBeInTheDocument();
  expect(within(profile).getByText("Withdrawn vote")).toBeInTheDocument();
  expect(within(profile).getByText(/withdrew their vote/)).toBeInTheDocument();
});
