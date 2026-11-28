import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch, pinFixtureClock } from "../test-render";
import { operatorRoute } from "../routes";
import { HealthPage } from "./health-page";

beforeEach(() => pinFixtureClock());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("shows readiness checks, accessible trend charts for the routed period and retention sweeps", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<HealthPage params={{ period: "90d" }} />);
  expect(await screen.findByRole("heading", { name: "Ready · all checks passing" })).toBeInTheDocument();
  expect(screen.getByText(/Database schema/)).toHaveTextContent("passing");
  const chart = await screen.findByRole("img", { name: /^Database size over 90 d\. From .+: lowest .+, highest .+, latest .+\.$/ });
  expect(chart.tagName.toLowerCase()).toBe("svg");
  expect(chart.querySelector("[style]")).toBeNull();
  expect(await screen.findByRole("img", { name: /Stored upload payloads over 90 d against a 350 MB quota/ })).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([path]) => path === "/admin/v1/trends?metric=downloads&period=90d")).toBe(true);
  expect(await screen.findByText("Batch limit")).toBeInTheDocument();
  expect(screen.getByText(/Last full recompute/)).toHaveTextContent("13 versions");
  expect(screen.getByRole("region", { name: "Recompute all versions" })).toBeInTheDocument();
});

it("pages retention sweeps and the operations log with validated routed cursors", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<HealthPage params={{}} />);
  const runs = await screen.findByRole("navigation", { name: "Retention sweep pages" });
  expect(within(runs).getByRole("link", { name: "Older" })).toHaveAttribute("href", "#/health?runsAfter=WzE3MDAsInJ1bi1yZXQteCJd%2B%2F%3D");
  expect(within(runs).queryByRole("link", { name: "Newest" })).not.toBeInTheDocument();
  const ops = await screen.findByRole("navigation", { name: "Operations log pages" });
  expect(within(ops).getByRole("link", { name: "Older" })).toHaveAttribute("href", "#/health?opsAfter=WyJvcCJd%2B%2F%3D");
  cleanup();
  renderOperator(<HealthPage params={operatorRoute("#/health?runsAfter=WzE3MDAsInJ1bi1yZXQteCJd%2B%2F%3D&period=7d").params} />);
  expect(await screen.findByText("Skipped")).toBeInTheDocument();
  const older = await screen.findByRole("navigation", { name: "Retention sweep pages" });
  expect(within(older).getByRole("link", { name: "Newest" })).toHaveAttribute("href", "#/health?period=7d");
  expect(fetcher.mock.calls.some(([path]) => path === "/admin/v1/maintenance/runs?kind=retention&after=WzE3MDAsInJ1bi1yZXQteCJd%2B%2F%3D")).toBe(true);
});
